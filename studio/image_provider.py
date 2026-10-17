"""Independent OpenAI-compatible image API; bounded responses and real multipart references."""
import base64
import hashlib
import io
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from PIL import Image

from .adapters import DeliveryError, NoRedirect
from .models import endpoint

DEFAULTS = {'IMAGE_BASE_URL': '', 'IMAGE_MODEL': '', 'IMAGE_API_KEY': '', 'IMAGE_REFERENCES': 'edits'}
MAX_BYTES = 5_000_000


def configuration(config, task=None):
    with config.lock:
        values = {k: config.get(k, v) for k, v in DEFAULTS.items()}
        if config.get('IMAGE_USE_SHARED', False) is True:
            values['IMAGE_BASE_URL'] = config.get('AI_BASE_URL')
            values['IMAGE_API_KEY'] = config.get('AI_API_KEY')
        if task in ('cover', 'illustration'):
            values['IMAGE_MODEL'] = config.get('IMAGE_' + task.upper() + '_MODEL') or values['IMAGE_MODEL']
        return values


def fingerprint(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def public_settings(config):
    values = configuration(config)
    return {'base_url': values['IMAGE_BASE_URL'], 'model': values['IMAGE_MODEL'],
            'key_set': bool(values['IMAGE_API_KEY']), 'references': values['IMAGE_REFERENCES'],
            'use_shared': config.get('IMAGE_USE_SHARED', False) is True,
            'configured': bool(values['IMAGE_BASE_URL'] and values['IMAGE_MODEL'])}


def save(config, values):
    from .service import Problem, text_value
    old = configuration(config)
    base = text_value(values.get('base_url', old['IMAGE_BASE_URL']), '图片接口地址', 2000)
    model = text_value(values.get('model', old['IMAGE_MODEL']), '图片模型', 200)
    key = text_value(values.get('api_key', ''), '图片 API Key', 2000)
    mode = values.get('references', old['IMAGE_REFERENCES'])
    if mode not in ('edits', 'none'):
        raise Problem('请选择参考图接口能力')
    if base:
        try:
            base = endpoint(base)
        except DeliveryError as exc:
            raise Problem(str(exc)) from None
    if any(ord(c) < 33 for c in key):
        raise Problem('图片 API Key 不能包含空白字符')
    if base != old['IMAGE_BASE_URL'] and old['IMAGE_API_KEY'] and not key:
        raise Problem('更换图片接口地址时请填写对应 API Key，避免把原密钥发送到新地址')
    config.update({'IMAGE_BASE_URL': base, 'IMAGE_MODEL': model,
                   'IMAGE_API_KEY': key or old['IMAGE_API_KEY'], 'IMAGE_REFERENCES': mode,
                   'IMAGE_USE_SHARED': False})
    return public_settings(config)


def image_bytes(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise DeliveryError('图片响应为空或超过5 MB，请降低生成尺寸或质量后重试')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP') or im.width * im.height > 20_000_000:
                raise ValueError()
            im.verify()
    except Exception:
        raise DeliveryError('接口未返回有效的 PNG、JPEG 或 WebP 图片') from None
    return raw


def fit_portrait(raw):
    """Preserve the complete generated composition on an XHS 1080×1440 canvas."""
    image_bytes(raw)
    with Image.open(io.BytesIO(raw)) as original:
        image = original.convert('RGB')
        color = image.getpixel((0, 0))
        image.thumbnail((1080, 1440), Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', (1080, 1440), color)
        canvas.paste(image, ((1080-image.width)//2, (1440-image.height)//2))
        output = io.BytesIO()
        canvas.save(output, format='PNG', optimize=True)
    return image_bytes(output.getvalue())


def _request(request, limit, timeout=120):
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise DeliveryError('图片接口响应超过大小限制')
        return data
    except urllib.error.HTTPError as exc:
        message = {401: '图片凭据无效，请检查图片 API Key',
                   403: '当前图片密钥或分组未获生图权限，请处理权限后手动重试',
                   404: '图片接口不存在，请核对地址与参考图接口能力',
                   429: '图片服务繁忙或额度受限，请稍后手动重试'}.get(exc.code, f'图片请求失败：HTTP {exc.code}')
        raise DeliveryError(message) from None
    except (OSError, TimeoutError):
        raise DeliveryError('图片请求超时或网络失败，未自动重试；服务可能已计费，请核对后再发起') from None


def generate(config, prompt, size, references):
    base = endpoint(config['IMAGE_BASE_URL'])
    if not config['IMAGE_MODEL']:
        raise DeliveryError('请先单独配置图片模型，文本模型不会用作图片模型')
    if references and config['IMAGE_REFERENCES'] != 'edits':
        raise DeliveryError('当前服务仅配置文生图，不能传入真实参考图；请选择支持 images/edits 的服务')
    headers = {'User-Agent': 'PaperPlaneStudio/1.0', 'Accept': 'application/json'}
    if config['IMAGE_API_KEY']:
        headers['Authorization'] = 'Bearer ' + config['IMAGE_API_KEY']
    fields = {'model': config['IMAGE_MODEL'], 'prompt': prompt, 'size': size, 'n': 1,
              'response_format': 'b64_json'}
    route = '/images/generations'
    if references:
        boundary = 'PaperPlane' + hashlib.sha256(prompt.encode()).hexdigest()[:24]
        chunks = []
        for name, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for index, raw in enumerate(references):
            image_bytes(raw)
            with Image.open(io.BytesIO(raw)) as im:
                fmt = im.format.lower()
            name = 'image' if len(references) == 1 else 'image[]'
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="reference-{index}.{fmt}"\r\nContent-Type: image/{fmt}\r\n\r\n'.encode() + raw + b'\r\n')
        data = b''.join(chunks) + f'--{boundary}--\r\n'.encode()
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        route = '/images/edits'
    else:
        data = json.dumps(fields, ensure_ascii=False).encode()
        headers['Content-Type'] = 'application/json'
    try:
        payload = json.loads(_request(urllib.request.Request(base + route, data=data, headers=headers), 8_000_000))
        from .model_provenance import report_response
        report_response(payload)
        row = payload['data'][0]
        if row.get('b64_json'):
            return image_bytes(base64.b64decode(row['b64_json'], validate=True))
        # Download a returned public image without sending the provider credential.
        url = row.get('url', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        if any(not ipaddress.ip_address(info[4][0]).is_global for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)):
            raise ValueError()
        return image_bytes(_request(urllib.request.Request(url, headers={'Accept': 'image/*'}), MAX_BYTES, 30))
    except (ValueError, KeyError, IndexError, TypeError, OSError):
        raise DeliveryError('图片接口返回格式不兼容，需要 data[0].b64_json 或公开 HTTPS 图片地址') from None
