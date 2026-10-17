import http.client
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class DeliveryError(Exception):
    def __init__(self, message, uncertain=False, retryable=False):
        super().__init__(message)
        self.uncertain = uncertain
        self.retryable = retryable


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


MODEL_TOTAL_TIMEOUT = 600
MODEL_RESPONSE_LIMIT = 3_000_000
MODEL_CONTENT_LIMIT = 200_000


def _model_chunks(response, deadline, idle_timeout):
    """Read available bytes, with both an idle timeout and a total deadline."""
    size = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeliveryError('模型生成超过总等待时间；服务可能仍在生成，请稍后手动重试')
        # urllib's HTTPResponse owns this socket on both HTTP and HTTPS. Set the
        # shrinking deadline before every read, including while receiving pings.
        sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if sock is not None:
            sock.settimeout(min(idle_timeout, remaining))
        chunk = response.read1(65536)
        if time.monotonic() >= deadline:
            raise DeliveryError('模型生成超过总等待时间；服务可能仍在生成，请稍后手动重试')
        if not chunk:
            return
        size += len(chunk)
        if size > MODEL_RESPONSE_LIMIT:
            raise DeliveryError('模型响应超过大小限制，本次结果未采用')
        yield chunk


def _model_response(response, deadline, idle_timeout):
    chunks = _model_chunks(response, deadline, idle_timeout)
    if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'text/event-stream':
        # Some compatible providers return ordinary JSON even when stream=true.
        return json.loads(b''.join(chunks))
    buffer, fields, event = b'', [], ''
    content, content_size, identity, usage, finished = [], 0, '', None, False

    def frame(data, event_name):
        nonlocal content_size, identity, usage, finished
        if event_name in ('ping', 'heartbeat'):
            return False
        if event_name == 'error':
            raise DeliveryError('模型服务在生成过程中返回错误，本次结果未采用；请稍后手动重试')
        if data == '[DONE]':
            if not finished or not content:
                raise DeliveryError('模型流式响应未完整生成正文，本次结果未采用')
            return True
        item = json.loads(data)
        if not isinstance(item, dict) or item.get('error'):
            raise DeliveryError('模型服务在生成过程中返回错误，本次结果未采用；请稍后手动重试')
        model = item.get('model')
        if model:
            if not isinstance(model, str) or (identity and model != identity):
                raise DeliveryError('模型流式响应的模型标识不一致，本次结果未采用')
            identity = model
        if isinstance(item.get('usage'), dict):
            usage = item['usage']
        choices = item.get('choices', [])
        if not isinstance(choices, list):
            raise ValueError()
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError()
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta', {})
            if not isinstance(delta, dict):
                raise ValueError()
            text = delta.get('content')
            if text is not None:
                if not isinstance(text, str) or (finished and text):
                    raise ValueError()
                content_size += len(text)
                if content_size > MODEL_CONTENT_LIMIT:
                    raise DeliveryError('模型正文超过大小限制，本次结果未采用')
                if text:
                    content.append(text)
            # reasoning_content and tool calls are never reader-facing text.
            reason = choice.get('finish_reason')
            if reason is not None:
                if reason != 'stop':
                    raise DeliveryError('模型未完整生成正文（达到长度限制或生成被中止），本次结果未采用')
                finished = True
        return False

    for chunk in chunks:
        buffer += chunk
        while b'\n' in buffer:
            raw, buffer = buffer.split(b'\n', 1)
            line = raw.rstrip(b'\r').decode('utf-8')
            if not line:
                if fields or event:
                    done = frame('\n'.join(fields), event) if fields or event == 'error' else False
                    fields, event = [], ''
                    if done:
                        result = {'model': identity, 'choices': [{'message': {'content': ''.join(content)}, 'finish_reason': 'stop'}]}
                        if usage is not None:
                            result['usage'] = usage
                        return result
                continue
            if line.startswith(':'):
                continue
            name, sep, value = line.partition(':')
            if value.startswith(' '):
                value = value[1:]
            if name == 'data':
                fields.append(value)
            elif name == 'event':
                event = value
    raise DeliveryError('模型流式连接提前结束，本次结果未采用；服务可能仍在生成，请稍后手动重试')


def request_json(url, payload=None, headers=None, timeout=40, delivery=False, model=False):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "PaperPlaneStudio/1.0", **(headers or {})})
    deadline = time.monotonic() + MODEL_TOTAL_TIMEOUT
    response_opened = False
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            response_opened = True
            if model:
                return _model_response(response, deadline, timeout)
            return json.loads(response.read(3_000_000))
    except urllib.error.HTTPError as exc:
        if model:
            hints = {401: '模型服务凭据无效，请检查模型设置', 403: '当前凭据没有模型调用权限',
                     429: '模型服务请求频率受限，请稍后重试'}
            raise DeliveryError(hints.get(exc.code, f'模型服务返回 HTTP {exc.code}；服务可能仍在生成，请稍后手动重试'),
                                retryable=exc.code in (429, 500, 502, 503, 504)) from None
        uncertain = delivery and exc.code >= 500
        hints = {401: "凭据无效，请检查渠道设置", 403: "账号没有操作权限", 422: "平台拒绝了文章，请检查标题、正文和标签", 429: "平台请求频率受限，请稍后重试"}
        raise DeliveryError(hints.get(exc.code, f"服务返回 HTTP {exc.code}，请核对平台状态"), uncertain,
                            exc.code in (429, 500, 502, 503, 504)) from None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        if model:
            if not response_opened and isinstance(exc, OSError):
                # Retain Q3-DEC-01's one temporary-network retry before receiving
                # a response. The service owns that retry budget. Once a response
                # is open, a truncated paid generation must not be replayed.
                raise DeliveryError('模型服务暂时无法连接，请稍后重试', retryable=True) from None
            raise DeliveryError('模型服务连接或响应异常，本次结果未采用；服务可能仍在生成，请稍后手动重试') from None
        if isinstance(exc, http.client.HTTPException):
            raise  # Keep non-model adapters' existing exception behavior.
        raise DeliveryError("网络或响应异常，请先在平台核对是否已接收，再决定后续操作", delivery, isinstance(exc, OSError)) from None


def devto_send(post, config, secrets):
    key = secrets.get("DEVTO_API_KEY")
    if not key:
        raise DeliveryError("尚未配置 DEV API Key，请到渠道设置中填写")
    if post.get('_devto_key_fingerprint'):
        import hashlib
        if hashlib.sha256(key.encode()).hexdigest() != post['_devto_key_fingerprint']:
            raise DeliveryError('DEV凭据在图片上传后已变化，正文尚未发送。')
    # Never send YAML frontmatter: Forem allows it to override published=false.
    if post["body"].lstrip().startswith("---"):
        raise DeliveryError("请移除正文开头的 YAML 元数据，使用编辑器中的独立字段")
    payload = {"article": {"title": post["title"], "body_markdown": post["body"], "published": bool(config.get("publish")), "description": post["summary"], "tags": post["tags"]}}
    if post.get("cover"):
        payload["article"]["main_image"] = post["cover"]
    result = request_json("https://dev.to/api/articles", payload, {"api-key": key}, delivery=True)
    if not result.get("id"):
        raise DeliveryError("平台响应缺少文章编号，请核对 DEV 后台", True)
    public = bool(result.get("published_at") or result.get("published_timestamp"))
    return {"status": "published" if public else "draft", "external_id": str(result["id"]), "url": str(result.get("url") or ""), "note": "已公开发布" if public else "已保存到 DEV 草稿，请在平台审核后发表"}


def devto_metrics(external_id, secrets):
    key = secrets.get("DEVTO_API_KEY")
    if not key:
        raise DeliveryError("尚未配置 DEV API Key")
    # Authenticated endpoint exposes views for articles owned by this API key.
    for page in range(1, 11):
        items = request_json(f"https://dev.to/api/articles/me/all?per_page=100&page={page}", headers={"api-key": key})
        if not isinstance(items, list):
            raise DeliveryError("平台统计响应格式不正确")
        for item in items:
            if str(item.get("id")) == external_id:
                return item
        if len(items) < 100:
            break
    raise DeliveryError("当前账号没有找到这篇文章，请核对渠道账号与文章编号")


def ai_draft(post, instruction, secrets, structured=False):
    from .writing_rules import reliability_instruction
    endpoint = secrets.get("AI_BASE_URL", "").rstrip("/")
    model, key = secrets.get("AI_MODEL"), secrets.get("AI_API_KEY")
    parsed = urlsplit(endpoint)
    if not model or not endpoint:
        raise DeliveryError("请先在设置中填写模型接口地址和模型名称；也可直接使用提纲模板")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
        raise DeliveryError("模型接口需要 HTTPS，本机模型可使用 HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DeliveryError("接口地址不能包含账号、密码、查询参数或片段")
    payload = {"model": model, "stream": True, "messages": [
        {"role": "system", "content": "你是一名中文内容编辑。" + ("只输出编辑要求指定的 JSON 对象。" if structured else
         "本次是确认提纲后的正文生成阶段，必须输出完整 Markdown 文章。提纲和历史要求中的‘先拟提纲’、‘只输出提纲’只描述已经结束的阶段，不得据此再次输出提纲。不要把提纲的写作说明、建议字数、待补证据清单或内部核对说明抄入文章。" if post.get("_writing_phase") == "body" else
         "本次仅生成可编辑的 Markdown 提纲，不写完整正文。不要输出‘本次仅生成提纲’等阶段说明，直接提供结构内容。" if post.get("_writing_phase") == "outline" else
         "按本次编辑要求输出可编辑的 Markdown 提纲或正文，不包含 YAML。要求仅生成提纲时，不写完整正文。") + reliability_instruction(None if structured else post.get('_writing_phase') or post.get('_model_task'))},
        {"role": "user", "content": f"标题：{post['title']}\n摘要：{post['summary']}\n现有正文/素材：\n{post['body']}\n编辑要求：{instruction}"}
    ]}
    if structured and (post.get('_model_task') == 'chat' or post.get('_json_object') is True):
        payload['response_format'] = {'type':'json_object'}
    result = request_json(endpoint + "/chat/completions", payload, {"Authorization": "Bearer " + key} if key else {}, timeout=90, model=True)
    from .model_provenance import report_response
    report_response(result)
    try:
        if result['choices'][0].get('finish_reason') not in (None, 'stop'):
            raise DeliveryError('模型未完整生成正文（达到长度限制或生成被中止），本次结果未采用')
        body = result["choices"][0]["message"]["content"]
        if not isinstance(body, str) or not body.strip() or len(body) > 200_000:
            raise ValueError()
        return body
    except (KeyError, IndexError, TypeError, ValueError):
        raise DeliveryError("模型返回内容为空或格式不兼容，请检查模型配置") from None
