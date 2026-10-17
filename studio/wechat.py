"""WeChat Official Account draft delivery using only the Python standard library.

This module has no publish, mass-send or draft-delete operation. The official
draft-update endpoint is allowed for explicit, guarded maintenance of an existing
draft; normal delivery continues to use draft/add with duplicate prevention.
Credentials are supplied by Config.get; media is read only below data_dir/media.
The host application owns routes (network.build_wechat_opener), durable receipts
and duplicate prevention. Tokens remain in process memory and are never logged.

Official API reference paths (the account must have the relevant permissions):
https://developers.weixin.qq.com/doc/offiaccount/Basic_Information/getStableAccessToken.html
https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html
https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Get_draft.html
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import http.client
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urlencode, urlsplit
import uuid

from .adapters import DeliveryError
from .markdown import render


API_ROOT = "https://api.weixin.qq.com"
# Application import limit, not a claim that WeChat still enforces old 1 MB docs.
# The account API remains authoritative when accepting/rejecting each upload.
MAX_INLINE_BYTES = 5 * 1024 * 1024
MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 3_000_000
MAX_IMAGES = 50
_ENDPOINTS = frozenset({"/cgi-bin/stable_token", "/cgi-bin/media/uploadimg",
                        "/cgi-bin/material/add_material", "/cgi-bin/draft/add",
                        "/cgi-bin/draft/get", "/cgi-bin/draft/count", "/cgi-bin/material/get_material"})
_TOKEN_ERRORS = {40001, 40014, 42001}
_TOKENS = {}
_TOKEN_LOCK = threading.RLock()
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]+)\)")
_CAPTION_STYLE = "margin:8px 0 24px;font-size:13px;line-height:1.7;color:#63786D;text-align:center;"
_INLINE_STYLES = {
    "p": "margin:0 0 18px;font-size:16px;line-height:1.8;color:#263D36;",
    "h1": "margin:30px 0 20px;font-size:24px;line-height:1.45;color:#143E35;font-weight:700;",
    "h2": "margin:32px 0 18px;padding-left:12px;border-left:4px solid #168B78;font-size:22px;line-height:1.5;color:#143E35;font-weight:700;",
    "h3": "margin:28px 0 16px;font-size:19px;line-height:1.6;color:#143E35;font-weight:700;",
    "h4": "margin:24px 0 14px;font-size:17px;line-height:1.6;color:#143E35;font-weight:700;",
    "blockquote": "margin:22px 0;padding:12px 16px;border-left:4px solid #168B78;background:#F1F5EF;font-size:16px;line-height:1.8;color:#526C63;",
    "img": "display:block;width:100%;max-width:100%;height:auto;margin:22px auto;border-radius:10px;",
    "pre": "box-sizing:border-box;max-width:100%;margin:20px 0;padding:16px;border-radius:8px;background:#F1F5EF;font-size:14px;line-height:1.7;white-space:pre-wrap;word-break:break-all;",
    "code": "font-family:Consolas,Menlo,monospace;font-size:14px;line-height:1.7;overflow-wrap:anywhere;",
    "em": "font-style:italic;color:#526C63;",
    "strong": "font-weight:700;color:#143E35;",
    "ul": "margin:16px 0 22px;padding-left:25px;font-size:16px;line-height:1.8;color:#263D36;",
    "ol": "margin:16px 0 22px;padding-left:25px;font-size:16px;line-height:1.8;color:#263D36;",
    "li": "margin:8px 0;",
    "a": "color:#168B78;text-decoration:underline;",
    "figcaption": _CAPTION_STYLE,
}


def _inline_styles(content):
    """Adapt safe HTML to WeChat, including visible external-link fallbacks."""
    # The platform editor may discard external anchors entirely. Keep the
    # destination as ordinary text so a reference survives that conversion.
    def reference(match):
        url, label = html.unescape(match[1]), match[2]
        if urlsplit(url).hostname == "mp.weixin.qq.com":
            return match[0]
        visible = html.escape(url)
        if html.unescape(label) == url:
            return f'<span>{visible}</span>'
        return (f'<span>{label}</span><br><span style="font-size:13px;color:#63786D;'
                f'word-break:break-all;overflow-wrap:anywhere;">复制到浏览器打开：{visible}</span>')

    content = re.sub(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', reference, content, flags=re.S)
    # Literal newlines between list items became empty paragraphs in the
    # WeChat editor. Code line breaks must be explicit before compacting HTML.
    def code_block(match):
        code = match[1].replace("\t", "&#9;").replace(" ", "&nbsp;").replace("\n", "<br>")
        return f'<pre><code>{code}</code></pre>'

    content = re.sub(r'<pre><code>(.*?)</code></pre>', code_block, content, flags=re.S)
    content = content.replace('>\n<', '><')
    # Existing italic paragraphs beginning with 图 1｜ are image captions.
    content = re.sub(r"<p>(?=<em>图\s*\d+[｜|：:.\s])", f'<p style="{_CAPTION_STYLE}">', content)

    def styled(match):
        tag, attrs = match[1], match[2]
        if "style=" in attrs:
            return match[0]
        return f'<{tag}{attrs} style="{_INLINE_STYLES[tag]}">'

    return re.sub(r"<(" + "|".join(_INLINE_STYLES) + r")\b([^>]*)>", styled, content)


class _APIError(DeliveryError):
    def __init__(self, message, code, uncertain=False):
        super().__init__(message, uncertain)
        self.code = code


def _api_error(code, creating=False):
    hints = {
        -1: "微信服务繁忙", 40001: "公众号凭据或 access_token 无效",
        40013: "公众号 AppID 无效", 40014: "公众号 access_token 无效",
        40125: "公众号 AppSecret 无效", 40164: "当前出口 IP 不在公众号白名单中，请检查固定出口与 IP 白名单",
        42001: "公众号 access_token 已过期", 45009: "公众号接口调用已达限额",
        48001: "公众号未获此接口权限，请在公众平台核对账号权限",
        40007: "草稿或素材编号无效，请在公众号后台核对",
    }
    # Do not echo errmsg: servers/proxies sometimes include a URL with a token.
    return _APIError(f"{hints.get(code, '微信接口拒绝请求')}（错误码 {code}）", code,
                     uncertain=creating and code < 0)


class WeChatClient:
    def __init__(self, config, *, timeout=40, allow_draft_update=False):
        self.allow_draft_update = allow_draft_update is True
        self.config = config
        self.app_id = config.get("WECHAT_APP_ID", "")
        self.secret = config.get("WECHAT_APP_SECRET", "")
        if not isinstance(self.app_id, str) or not self.app_id.strip() or not isinstance(self.secret, str) or not self.secret.strip():
            raise DeliveryError("请先配置公众号 AppID 和 AppSecret")
        self.app_id, self.secret = self.app_id.strip(), self.secret.strip()
        self.timeout = timeout
        self.opener = None
        # Changing credentials or route must not reuse an old account's token.
        identity = [self.app_id, self.secret] + [str(config.get(key, "")) for key in
                    ("WECHAT_PROXY", "WECHAT_SSH_HOST", "WECHAT_SSH_KEY", "WECHAT_SSH_HOST_ALIAS", "WECHAT_SSH_PORT")]
        self.cache_key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _request(self, endpoint, payload=None, *, token=None, image=None, description=None, creating=False, query=None):
        if endpoint not in _ENDPOINTS and not (self.allow_draft_update and endpoint == '/cgi-bin/draft/update'):
            raise DeliveryError("不支持的公众号接口")
        params = dict(query or {})
        if token is not None:
            params["access_token"] = token
        url = API_ROOT + endpoint + ("?" + urlencode(params) if params else "")
        headers = {"User-Agent": "PaperPlaneStudio/1.0", "Accept": "application/json"}
        if image is not None:
            content, suffix, mime = image
            boundary = "PaperPlane" + uuid.uuid4().hex
            # The original filename/path is not sent to the remote account.
            filename='video.mp4' if mime=='video/mp4' else 'image'+suffix
            data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{filename}\"\r\n"
                    f"Content-Type: {mime}\r\n\r\n").encode("ascii") + content + b'\r\n'
            if description is not None:
                data+=(f'--{boundary}\r\nContent-Disposition: form-data; name="description"\r\n\r\n').encode('ascii')+json.dumps(description,ensure_ascii=False).encode('utf-8')+b'\r\n'
            data+=f"--{boundary}--\r\n".encode("ascii")
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        else:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, headers=headers)
        if self.opener is None:
            # The route may be direct, HTTP CONNECT or an application-owned tunnel.
            # Import lazily: validating a post never starts a network connection.
            from .network import build_wechat_opener
            self.opener = build_wechat_opener(self.config)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("oversized response")
                result = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(result, dict):
                    raise ValueError("expected object")
        except urllib.error.HTTPError as exc:
            uncertain = creating and (exc.code >= 500 or exc.code == 408)
            raise DeliveryError(f"微信接口返回 HTTP {exc.code}，请检查渠道连接与平台状态", uncertain) from None
        except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException, urllib.error.URLError):
            message = "新增草稿请求中断或响应异常，请先核对公众号草稿箱，勿直接重试" if creating else "微信接口连接或响应异常，请检查网络后重试核对"
            raise DeliveryError(message, creating) from None
        code = result.get("errcode", 0)
        if type(code) is not int:
            raise DeliveryError("微信接口错误码格式异常，请核对平台结果", creating)
        if code:
            raise _api_error(code, creating)
        return result

    def token(self, *, invalidated=None):
        with _TOKEN_LOCK:
            cached = _TOKENS.get(self.cache_key)
            if invalidated is not None and cached and cached[0] == invalidated:
                _TOKENS.pop(self.cache_key, None)
                cached = None
            if cached and cached[1] > time.monotonic():
                return cached[0]
            result = self._request("/cgi-bin/stable_token", {"grant_type": "client_credential", "appid": self.app_id,
                                   "secret": self.secret, "force_refresh": False})
            token, expires = result.get("access_token"), result.get("expires_in")
            if not isinstance(token, str) or not token or type(expires) is not int or expires <= 0:
                raise DeliveryError("微信凭据响应缺少有效 token 或有效期")
            # Short-lived test tokens also expire early, rather than outliving the server token.
            margin = min(300, expires * 0.1)
            _TOKENS[self.cache_key] = (token, time.monotonic() + expires - margin)
            return token

    def api(self, endpoint, payload=None, **kwargs):
        token = self.token()
        try:
            return self._request(endpoint, payload, token=token, **kwargs)
        except _APIError as exc:
            if exc.code not in _TOKEN_ERRORS:
                raise
            # Only an explicit invalid/expired-token rejection permits one retry.
            # Timeouts, malformed responses and server failures never replay draft/add.
            token = self.token(invalidated=token)
            return self._request(endpoint, payload, token=token, **kwargs)


def _text(post, key, label, limit, required=False):
    value = post.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise DeliveryError(f"公众号{label}格式不正确或超过 {limit} 字")
    if required and not value.strip():
        raise DeliveryError(f"请填写公众号{label}")
    return value.strip() if key != "body" else value


def _visible_markdown(source):
    """Mask code using the same fenced/inline rules as studio.markdown.render."""
    output, fenced = [], False
    for line in source.splitlines(keepends=True):
        if line.startswith("```"):
            fenced = not fenced
            output.append(" " * len(line))
        elif fenced:
            output.append(" " * len(line))
        else:
            output.append(re.sub(r"`([^`]+)`", lambda m: " " * len(m[0]), line))
    return "".join(output)


def _local_image(src, data_dir, *, cover=False):
    if not isinstance(src, str) or not src.strip() or any(ord(c) < 32 for c in src):
        raise DeliveryError("公众号图片路径无效，请从图片素材中重新选择")
    if re.match(r"(?i)^(https?:|//)", src):
        raise DeliveryError("公众号 API 草稿暂不接受远程图片，请先下载并导入本工作台的图片素材")
    root = Path(data_dir).resolve() / "media"
    if src.startswith("/media/"):
        parsed = urlsplit(src)
        relative = unquote(parsed.path[len("/media/"):])
        if parsed.query or parsed.fragment or "\\" in relative or any(p in ("", ".", "..") for p in relative.split("/")):
            raise DeliveryError("公众号图片路径必须位于工作台 data/media 内")
        candidate = root / relative
    elif Path(src).is_absolute():
        candidate = Path(src)
    else:
        raise DeliveryError("公众号图片必须来自本工作台 data/media，请使用素材库图片路径")
    # Reject an unrelated absolute/UNC path before resolving it: even a failed
    # resolve against a network share would be an unintended external access.
    if not candidate.is_relative_to(root):
        raise DeliveryError("公众号图片路径必须位于工作台 data/media 内")
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError()
        maximum = MAX_COVER_BYTES if cover else MAX_INLINE_BYTES
        with resolved.open("rb") as stream:
            content = stream.read(maximum + 1)
    except (OSError, ValueError):
        raise DeliveryError("公众号图片不存在或超出工作台 data/media，请重新导入素材") from None
    if not content or len(content) >= maximum:
        raise DeliveryError(f"公众号{'封面' if cover else '正文图片'}需小于 5 MB，请先压缩后导入")
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 33 and content[12:16] == b"IHDR":
        image = (content, ".png", "image/png")
    elif content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        image = (content, ".jpg", "image/jpeg")
    else:
        raise DeliveryError("公众号 API 图片需为 PNG 或 JPEG 文件，请转换后重新导入")
    return resolved, image


def _prepare_post(post, data_dir):
    if not isinstance(post, dict):
        raise DeliveryError("公众号稿件格式不正确")
    fields = {key: _text(post, key, label, limit, required) for key, label, limit, required in
              (("title", "标题", 64, True), ("body", "正文", 200_000, True),
               ("author", "作者", 16, False), ("summary", "摘要", 120, False))}
    if fields["body"].lstrip().startswith("---"):
        raise DeliveryError("请移除正文开头的 YAML 元数据，使用编辑器中的独立字段")
    source = fields["body"]
    visible = _visible_markdown(source)
    from . import videos
    video_items={v['id']:v for v in videos.snapshot_for_post(post,data_dir)} if videos.MARKER.search(visible) else {}
    video_blocks=[]
    for match in videos.MARKER.finditer(visible):
        item=video_items.get(match[2])
        if not item:raise DeliveryError('正文引用的视频不属于当前文章或已缺失，请重新选择视频素材')
        block=videos.wechat_html(item)
        video_blocks.append((match.start(),match.end(),'PPVIDEO'+uuid.uuid4().hex,block))
    for start,end,placeholder,block in reversed(video_blocks):
        source=source[:start]+placeholder+source[end:]
    visible=_visible_markdown(source)
    if re.search(r"<\s*(?:img|picture|svg)\b", visible, re.I):
        raise DeliveryError("公众号图片请使用 Markdown 图片语法，暂不接受 HTML 图片")
    matches = list(_IMAGE.finditer(visible))
    residual = _IMAGE.sub("", visible)
    if "![" in residual:
        raise DeliveryError("公众号图片请使用 ![说明](/media/文件名.png)，不支持引用式或不完整的图片语法")
    if len(matches) > MAX_IMAGES:
        raise DeliveryError(f"单篇公众号草稿最多处理 {MAX_IMAGES} 张图片")
    occurrences, images, replacements, placeholders = [], {}, [], []
    marker = uuid.uuid4().hex
    for index, match in enumerate(matches):
        src = match[2].strip()
        if src.startswith("<") and src.endswith(">"):
            src = src[1:-1]
        elif any(c.isspace() for c in src):
            raise DeliveryError("图片路径含空格时请用尖括号包裹，暂不支持 Markdown 图片标题")
        path, image = _local_image(src, data_dir)
        images.setdefault(path, image)
        occurrences.append(path)
        placeholder = f"https://paperplane.invalid/{marker}/image-{index}"
        placeholders.append(placeholder)
        replacements.append((match.start(), match.end(), f"![{match[1]}]({placeholder})"))
    for start, end, replacement in reversed(replacements):
        source = source[:start] + replacement + source[end:]
    cover_path, cover_image = _local_image(post.get("cover", ""), data_dir, cover=True)
    content = _inline_styles(render(source))
    for _,_,placeholder,block in video_blocks:
        content=re.sub(r'<p\b[^>]*>'+placeholder+r'</p>',lambda m:block,content)
        if placeholder in content:raise DeliveryError('视频必须位于正文独立一行，请重新插入视频')
    if _HTMLShape(content).images != placeholders:
        raise DeliveryError("正文中有图片未能正确转换，请使用独立一行的 Markdown 图片语法")
    fields.update(content=content, image_paths=occurrences, images=images, placeholders=placeholders,
                  cover_path=cover_path, cover_image=cover_image, video_accounts=[video_items[m[2]].get('wechat_app_id','') for m in videos.MARKER.finditer(_visible_markdown(fields['body'])) if videos.player(video_items[m[2]].get('embed_url',''))[0]=='wechat'])
    if len(fields["content"].encode("utf-8")) > 1_000_000:
        raise DeliveryError("公众号正文生成后过大，请精简内容")
    return fields


def validate_post(post, data_dir):
    """Read-only preflight. Raises DeliveryError before any network or mutation."""
    prepared = _prepare_post(post, data_dir)
    return {"ok": True, "image_count": len(prepared["image_paths"]),
            "unique_image_count": len(prepared["images"]), "cover": True}


class _HTMLShape(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.words, self.images, self.code, self.links = [], [], [], []
        self.structure = []
        self.videos=[]
        self.video_players=[]
        self.code_depth = 0
        self.feed(value)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in ('iframe','mpvideo'):
            from .videos import player,native_players
            values=dict(attrs);info=player(values.get('src') or values.get('data-src') or '')
            vid=(values.get('vid') or values.get('data-vid') or '') if tag=='mpvideo' else (info[1] if info else '')
            if tag=='mpvideo' and not re.fullmatch(r'(?:wxv|apiv)_[0-9]{6,30}',vid):vid=''
            self.videos.append(vid)
            # Matching IDs alone must not accept an empty custom editor tag.
            # Do not claim playback verification: this checks the player URL
            # and a visible viewport, not the reader's network/media session.
            style=values.get('style','').replace(' ','').lower()
            viewport=bool(re.fullmatch(r'[1-9][0-9]{1,3}(?:px)?',values.get('height','')))
            # Native editor cards use data-w/data-ratio and expand in the reader;
            # they intentionally have no literal height or src attribute.
            if tag=='iframe' and native_players(self.get_starttag_text()+'</iframe>'):viewport=True
            hidden='display:none' in style or 'visibility:hidden' in style or bool(re.search(r'(?:^|;)height:0(?:px)?(?:;|$)',style))
            self.video_players.append(bool(tag=='iframe' and info and vid and viewport and not hidden and (info[0]!='wechat' or values.get('data-mpvid')==vid)))
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "pre"):
            self.structure.append(tag)
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))
        if tag in ("pre", "code"):
            if not self.code_depth:
                self.words.append(f"\x00code-{len(self.code)}\x00")
                self.code.append("")
            self.code_depth += 1
        elif tag == "br" and self.code_depth:
            self.code[-1] += "\n"
        elif tag in ("p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "br") and not self.code_depth:
            self.words.append(" ")
        if tag == "img":
            values = dict(attrs)
            self.images.append(values.get("src") or values.get("data-src") or "")

    def handle_endtag(self, tag):
        if tag in ("pre", "code") and self.code_depth:
            self.code_depth -= 1
        elif tag in ("p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote") and not self.code_depth:
            self.words.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, value):
        if self.code_depth:
            self.code[-1] += value
        else:
            self.words.append(value)

    @property
    def text(self):
        # Ordinary HTML whitespace is collapsible, but code indentation changes
        # program meaning and must survive the comparison.
        return (re.sub(r"\s+", " ", "".join(self.words)).strip(),
                tuple(value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ") for value in self.code))


def _cdn_identity(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(html.unescape(value))
        # Only use WeChat's own image URLs returned by the upload endpoint.
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("mmbiz.qpic.cn", "mmbiz.qlogo.cn") or parsed.username or parsed.password:
            return None
        # The draft service changes the same uploaded image's rendition from
        # /0 to /640. Only these observed variants are interchangeable; asset
        # paths, other sizes, query parameters and order remain significant.
        asset_path, separator, size = parsed.path.rpartition("/")
        path = asset_path + "/0" if separator and size in ("0", "640") else parsed.path
        return parsed.hostname, path, parsed.query
    except ValueError:
        return None


def _check_readback(result, prepared, *, uploaded_urls=None, thumb_id=None):
    items = result.get("news_item")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise DeliveryError("公众号草稿读回的文章数量或格式不符", True)
    article = items[0]
    checks = (("title", "标题", prepared["title"]), ("author", "作者", prepared["author"]))
    for key, label, expected in checks:
        if article.get(key, "") != expected:
            raise DeliveryError(f"公众号草稿读回的{label}与当前快照不符", True)
    # With an empty digest WeChat may generate one from content automatically.
    if prepared["summary"] and article.get("digest") != prepared["summary"]:
        raise DeliveryError("公众号草稿读回的摘要与当前快照不符", True)
    content = article.get("content")
    if not isinstance(content, str):
        raise DeliveryError("公众号草稿读回缺少正文", True)
    actual, expected = _HTMLShape(content), _HTMLShape(prepared["content"])
    if actual.videos!=expected.videos:
        raise DeliveryError('公众号草稿读回的视频缺失或播放器身份改变，请在微信核对，勿重复送稿',True)
    if actual.videos and not all(actual.video_players):
        raise DeliveryError('公众号草稿视频标记存在，但缺少有效播放器地址或显示区域；请修正原草稿，勿重复送稿',True)
    if actual.text != expected.text:
        raise DeliveryError("公众号草稿读回的正文文字与当前快照不符", True)
    if actual.links != expected.links:
        raise DeliveryError("公众号草稿读回的链接地址缺失或改变", True)
    if actual.structure != expected.structure:
        raise DeliveryError("公众号草稿读回的标题、列表或代码块结构改变", True)
    if len(actual.images) != len(prepared["image_paths"]) or any(not _cdn_identity(src) for src in actual.images):
        raise DeliveryError("公众号草稿读回的图片数量或图片地址不符", True)
    if uploaded_urls is not None and [_cdn_identity(src) for src in actual.images] != [_cdn_identity(src) for src in uploaded_urls]:
        raise DeliveryError("公众号草稿读回的正文图片与上传回执不符", True)
    actual_thumb = article.get("thumb_media_id")
    if not isinstance(actual_thumb, str) or not actual_thumb or (thumb_id is not None and actual_thumb != thumb_id):
        raise DeliveryError("公众号草稿读回的封面素材不符", True)


def _receipt(media_id, prepared, error=None, *, verification=None):
    count = len(prepared["image_paths"])
    video_count=len(_HTMLShape(prepared['content']).videos)
    result = {"status": "uncertain" if error else "draft", "external_id": media_id, "url": "",
            "note": (f"草稿编号已保留；{error}。请核对或重新检查该草稿，勿重新发送。" if error else
                     f"已保存到公众号草稿箱，读回核对标题、正文、{count} 张图片"+(f'、{video_count} 段视频播放器地址与显示区域' if video_count else '')+"和封面通过；"+('实际播放仍需微信预览核对；' if video_count else '')+"尚未发表或群发"),
            "verified": not bool(error), "image_count": count}
    if verification is not None:
        result["verification"] = verification
    return result


def send_draft(post, delivery_config, config, data_dir):
    """Upload all images, create exactly one draft, then read it back.

    A returned ``uncertain`` receipt MUST be stored, including external_id.
    A DeliveryError with uncertain=True and no receipt means draft/add may have
    succeeded; inspect the platform rather than automatically calling this again.
    """
    prepared = _prepare_post(post, data_dir)
    client = WeChatClient(config)
    if any(account and account!=client.app_id for account in prepared['video_accounts']):
        raise DeliveryError('正文视频已上传到另一公众号，请切回对应账号或重新关联视频')
    uploaded = {}
    # All local files were checked and captured before the first upload. A failed
    # upload aborts here; successfully uploaded material may remain, but no draft
    # has been requested and there is nothing to publish accidentally.
    for path, image in prepared["images"].items():
        result = client.api("/cgi-bin/media/uploadimg", image=image)
        url = result.get("url")
        if not _cdn_identity(url):
            raise DeliveryError("正文图片上传未返回有效微信图片地址，已停止创建草稿")
        uploaded[path] = url
    cover = client.api("/cgi-bin/material/add_material", image=prepared["cover_image"], query={"type": "image"})
    thumb_id = cover.get("media_id")
    if not isinstance(thumb_id, str) or not thumb_id:
        raise DeliveryError("封面上传未返回素材编号，已停止创建草稿")
    content, ordered_urls = prepared["content"], []
    for path, placeholder in zip(prepared["image_paths"], prepared["placeholders"]):
        url = uploaded[path]
        ordered_urls.append(url)
        content = content.replace(placeholder + '"', html.escape(url, quote=True) + '"')
    article = {"article_type": "news", "title": prepared["title"], "author": prepared["author"],
               "digest": prepared["summary"], "content": content, "thumb_media_id": thumb_id,
               "show_cover_pic": 0 if prepared["cover_path"] in prepared["images"] else 1,
               "need_open_comment": 0, "only_fans_can_comment": 0}
    created = client.api("/cgi-bin/draft/add", {"articles": [article]}, creating=True)
    media_id = created.get("media_id")
    if not isinstance(media_id, str) or not media_id:
        raise DeliveryError("新增草稿响应缺少草稿编号，请核对公众号草稿箱，勿直接重试", True)
    verification = {"media_id": media_id, "image_urls": ordered_urls, "thumb_media_id": thumb_id}
    try:
        result = client.api("/cgi-bin/draft/get", {"media_id": media_id})
        _check_readback(result, prepared, uploaded_urls=ordered_urls, thumb_id=thumb_id)
    except DeliveryError as exc:
        return _receipt(media_id, prepared, str(exc), verification=verification)
    except Exception:
        # Once accepted, a local parser/route bug must not discard the receipt.
        return _receipt(media_id, prepared, "草稿读回检查异常，需要在平台核对", verification=verification)
    return _receipt(media_id, prepared, verification=verification)


def verify_draft(external_id, post, config, data_dir, *, expected_receipt=None):
    """Read a known draft; never upload, create, update or publish anything.

    Save send_draft's verification in job.snapshot.wechat_receipt and pass it
    here (or include that field in post). Without these original upload receipts,
    a later read cannot establish that the remote images/cover match this draft.
    """
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 512 or any(ord(c) < 32 for c in external_id):
        raise DeliveryError("请先填写有效的公众号草稿编号")
    prepared = _prepare_post(post, data_dir)
    verification = expected_receipt if expected_receipt is not None else post.get("wechat_receipt")
    if verification is not None and (not isinstance(verification, dict) or verification.get("media_id") != external_id or
                                    not isinstance(verification.get("image_urls"), list) or
                                    len(verification["image_urls"]) != len(prepared["image_paths"]) or
                                    any(not _cdn_identity(src) for src in verification["image_urls"]) or
                                    not isinstance(verification.get("thumb_media_id"), str) or not verification["thumb_media_id"]):
        return _receipt(external_id, prepared, "保存的图片上传回执不完整或属于另一篇草稿")
    try:
        result = WeChatClient(config).api("/cgi-bin/draft/get", {"media_id": external_id})
        _check_readback(result, prepared, uploaded_urls=verification["image_urls"] if verification else None,
                        thumb_id=verification["thumb_media_id"] if verification else None)
    except DeliveryError as exc:
        return _receipt(external_id, prepared, str(exc), verification=verification)
    except Exception:
        return _receipt(external_id, prepared, "草稿读回检查异常，需要在平台核对", verification=verification)
    if verification is None:
        return _receipt(external_id, prepared, "文字和图片数量已核对，但缺少原始图片上传回执，无法确认正文图片及封面与本次素材一致")
    result = _receipt(external_id, prepared, verification=verification)
    result["note"] = "已读回核对现有公众号草稿：标题、正文、图片上传地址和封面素材编号一致；未重新上传或创建，尚未发表或群发"
    return result


def test_connection(config):
    """Read-only token and draft-count check (does not create test content)."""
    result = WeChatClient(config).api("/cgi-bin/draft/count")
    count = result.get("total_count")
    if type(count) is not int or count < 0:
        raise DeliveryError("公众号草稿计数响应格式异常")
    return {"ok": True, "draft_count": count, "note": "公众号连接与草稿读取权限检查通过；未新建草稿或发表内容"}
