"""Zhihu OSS upload. Only redacted image protocol diagnostics leave memory."""
import base64
import hashlib
import hmac
import io
import re
import time
from email.utils import formatdate
from urllib.parse import urlsplit, urlunsplit, parse_qsl, unquote
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from .adapters import DeliveryError, NoRedirect


def _object_key(value):
    # The same key is used for PUT and CDN reads; image extensions are valid.
    return (isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,200}', value)
            and value not in ('.', '..'))


PRIVATE_IMAGE_HOST = 'pic-private.zhihu.com'
PUBLIC_IMAGE_HOST = r'pic(?:[1-4ab]|x)\.zhimg\.com'
PUBLIC_IMAGE_QUERY = {'source', 'width', 'height', 'quality', 'format', 'imageView2',
                      'imageMogr2', 'x-oss-process', 'protocol', 'no_change'}
PRIVATE_IMAGE_QUERY = {'animatedImageAutoPlay', 'animatedImagePlayCount', 'auth_key',
                       'expiration', 'incremental', 'overTime', 'precoder', 'protocol',
                       'retryCount', 'sampling', 'sceneCode', 'source', 'no_change', 'draft_token'}


def _image_path(path, private=False):
    # RFC3986 path characters include the ':' and '%' used by the new renderer.
    pattern = r"/[a-zA-Z0-9_./~!$&'()*+,;=:@%\-]+" if private else r'/[a-zA-Z0-9_./~\-]+'
    if not isinstance(path, str) or not 1 < len(path) <= 2048 or not re.fullmatch(pattern, path):
        return False
    if re.search(r'%(?![a-fA-F0-9]{2})', path):
        return False
    decoded = unquote(path)
    if re.search(r'[\s\\\x00-\x1f\x7f?#]', decoded):
        return False
    parts = decoded.split('/')[1:]
    return 1 <= len(parts) <= 5 and all(part and part not in ('.', '..') for part in parts)


def _cdn_url(value):
    """Accept exact official references; private signatures stay in Zhihu content only."""
    if (not isinstance(value, str) or len(value) > 4096
            or re.search(r'[\s\\\x00-\x1f\x7f]', value)):
        return None
    try:
        parsed = urlsplit('https:' + value if value.startswith('//') else value)
        private = parsed.hostname == PRIVATE_IMAGE_HOST
        if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port
                or parsed.fragment or not (private or re.fullmatch(PUBLIC_IMAGE_HOST, parsed.hostname or ''))
                or (private and parsed.scheme != 'https') or not _image_path(parsed.path, private)):
            return None
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=24)
        allowed = PRIVATE_IMAGE_QUERY if private else PUBLIC_IMAGE_QUERY
        if len({key for key, _ in query}) != len(query):
            return None
        if any(key not in allowed or not re.fullmatch(r'[a-zA-Z0-9_.,~:/!@%+=()\-]{0,1024}', item)
               for key, item in query):
            return None
        values = dict(query)
        if ('protocol' in values and values['protocol'] != 'v2') or ('no_change' in values and values['no_change'] != '1'):
            return None
        if private:
            if values.get('protocol') != 'v2' or not values.get('auth_key'):
                return None
            if not re.fullmatch(r'-1|[0-9]{1,12}', values.get('expiration', '')):
                return None
            if 'draft_token' in values and not re.fullmatch(r'[0-9]{1,40}', values['draft_token']):
                return None
        return urlunsplit(('https', parsed.hostname, parsed.path, parsed.query, ''))
    except ValueError:
        return None


def _private_expired(value):
    try:
        parsed = urlsplit(value)
        if parsed.hostname != PRIVATE_IMAGE_HOST:
            return False
        expiration = int(dict(parse_qsl(parsed.query))['expiration'])
        # Official editor uses Unix seconds; -1 explicitly disables expiration.
        return expiration >= 0 and time.time() >= expiration
    except (ValueError, KeyError, TypeError):
        return True


def _url_shape(value):
    result = {'type': type(value).__name__}
    if not isinstance(value, str):
        return result
    result['length'] = len(value)
    canonical = _cdn_url(value)
    result['valid'] = bool(canonical)
    try:
        parsed = urlsplit('https:' + value if value.startswith('//') else value)
        result['scheme'] = parsed.scheme if parsed.scheme in ('http', 'https') else 'other'
        result['scheme_relative'] = value.startswith('//')
        result['allowed_host'] = parsed.hostname == PRIVATE_IMAGE_HOST or bool(re.fullmatch(PUBLIC_IMAGE_HOST, parsed.hostname or ''))
        result['private'] = parsed.hostname == PRIVATE_IMAGE_HOST
        if result['private']:
            result['expired'] = _private_expired(value)
        result['path_segments'] = len(parsed.path.split('/')) - 1
        result['query_present'] = bool(parsed.query)
        result['query_length'] = len(parsed.query)
        # Report bounded protocol metadata even when a host is not yet supported.
        # Never include query values, userinfo, the full URL or upload grants.
        host = parsed.hostname or ''
        if len(host) <= 253 and re.fullmatch(r'[a-zA-Z0-9.-]+', host):
            result['host'] = host
        if _image_path(parsed.path, parsed.hostname == PRIVATE_IMAGE_HOST):
            result['path'] = parsed.path
        keys = [key for key, _ in parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=24)]
        result['query_keys'] = sorted({key if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{0,63}', key) else '[other]' for key in keys})
    except ValueError:
        result['parse_failed'] = True
    return result


def _status_shape(value):
    """An allowlist, never raw bodies, tokens, headers or arbitrary string values."""
    if not isinstance(value, dict):
        return {'type': type(value).__name__}
    result = {'type': 'dict', 'fields': sorted(k for k in value if k in
        ('status', 'state', 'original_hash', 'object_key', 'image_id', 'url', 'src', 'original_url', 'original_src', 'data', 'image'))}
    for key in result['fields']:
        item = value[key]
        result[key + '_type'] = type(item).__name__
        if key in ('status', 'state'):
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                result[key] = item
            elif item in ('completed', 'processing', 'pending', 'success', 'failed', 'error', 'rejected', 'uploading', 'ready'):
                result[key] = item
            elif isinstance(item, str):
                result[key + '_length'] = len(item)
        elif key in ('url', 'src', 'original_url', 'original_src'):
            result[key + '_shape'] = _url_shape(item)
        elif key in ('original_hash', 'object_key'):
            result[key + '_valid'] = bool(_object_key(item))
            if isinstance(item, str):
                result[key + '_length'] = len(item)
    return result


def _candidates(result, file, extension):
    records = [result] if isinstance(result, dict) else []
    if records:
        records += [result[key] for key in ('data', 'image') if isinstance(result.get(key), dict)]
    candidates = []
    has_exact_reference = any(isinstance(record.get(field), str) and record[field]
                              for record in records for field in ('original_url', 'url', 'src', 'original_src'))
    for record in records:
        # Zhihu's current official editor (module 21941) keeps src/original_src
        # unchanged for protocol=v2 or no_change=1; legacy *src fields need the
        # upload MIME extension. The API's exact host/path, never a guessed hash,
        # is the basis of these candidates. Every candidate still must decode.
        src = _cdn_url(record.get('src'))
        src_query = dict(parse_qsl(urlsplit(src).query)) if src else {}
        src_modern = src_query.get('protocol') == 'v2' or src_query.get('no_change') == '1'
        source_fields = ('src', 'original_src') if src_modern else ('original_src', 'src')
        for field in ('original_url', 'url') + source_fields:
            url = _cdn_url(record.get(field))
            if url:
                parsed = urlsplit(url)
                query = dict(parse_qsl(parsed.query))
                modern = query.get('protocol') == 'v2' or query.get('no_change') == '1'
                suffix = '.jpeg' if extension == '.jpg' else extension
                if (field.endswith('src') and not modern and suffix in ('.png', '.jpeg', '.gif', '.webp')
                        and not re.search(r'\.(?:png|jpe?g|gif|webp)$', parsed.path, re.I)):
                    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path + suffix, parsed.query, ''))
                candidates.append((field, url))
        if has_exact_reference:
            continue
        for field in ('original_hash', 'object_key'):
            key = record.get(field)
            if _object_key(key):
                suffix = '.gif' if extension == '.gif' and not key.lower().endswith('.gif') else ''
                candidates.append((field, 'https://pic4.zhimg.com/' + key + suffix))
    key = file.get('object_key')
    if not has_exact_reference and _object_key(key):
        suffix = '.gif' if extension == '.gif' and not key.lower().endswith('.gif') else ''
        candidates.append(('upload_file.object_key', 'https://pic4.zhimg.com/' + key + suffix))
    unique = {}
    for source, url in candidates:
        unique.setdefault(url, source)
    return [(source, url) for url, source in unique.items()]


def _verify_cdn(url):
    """Decode an exact official image reference, without account cookies or headers."""
    url = _cdn_url(url)
    if not url:
        return {'ready': False, 'reason': 'invalid-cdn-path'}
    if _private_expired(url):
        return {'ready': False, 'reason': 'expired-private-reference'}
    try:
        request = Request(url, headers={'Referer': 'https://zhuanlan.zhihu.com/',
                          'User-Agent': 'PaperPlaneStudio/1.0', 'Accept': 'image/*'})
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            status = response.status
            mime = response.headers.get('Content-Type', '').split(';', 1)[0].lower()
            if status != 200 or mime not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                return {'ready': False, 'reason': 'not-image', 'http_status': status, 'content_type': mime[:60]}
            data = response.read(8_000_001)
        if not 0 < len(data) <= 8_000_000:
            return {'ready': False, 'reason': 'invalid-image-size', 'bytes': len(data)}
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image.verify()
        return {'ready': width > 0 and height > 0, 'reason': 'decoded',
                'http_status': status, 'content_type': mime, 'bytes': len(data), 'width': width, 'height': height}
    except HTTPError as exc:
        return {'ready': False, 'reason': 'http-error', 'http_status': exc.code}
    except Exception:
        return {'ready': False, 'reason': 'read-or-decode-failed'}


def upload(window, path):
    stage = '申请图片上传'
    diagnostics = {'stage': stage, 'host': 'zhihu-pics-upload.zhimg.com', 'status': 'running', 'polls': [], 'checks': []}
    window.upload_diagnostics = diagnostics
    try:
        data = path.read_bytes()
        if not 0 < len(data) <= 5_000_000:
            raise DeliveryError('图片为空或超过5MB。')
        grant = window.call('upload-grant', {'hash': hashlib.md5(data).hexdigest()})
        file = grant['upload_file']
        diagnostics['grant_file'] = _status_shape(file)
        image_id = str(file['image_id'])
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', image_id):
            raise DeliveryError('图片编号无效。')
        diagnostics['image_id'] = image_id
        if str(file['state']) != '1':
            key, token = file['object_key'], grant['upload_token']
            # Fixed OSS host and a single opaque key; never follow grant-bearing redirects.
            if not _object_key(key):
                raise DeliveryError('图片对象路径无效。')
            if not all(isinstance(token.get(k), str) and token[k] and not any(c in token[k] for c in '\r\n') for k in ('access_id', 'access_key', 'access_token')):
                raise DeliveryError('图片临时凭证无效。')
            mime = 'image/' + ('jpeg' if path.suffix.lower() in ('.jpg', '.jpeg') else path.suffix[1:].lower())
            date = formatdate(usegmt=True)
            headers = {'x-oss-date': date, 'x-oss-security-token': token['access_token'], 'x-oss-user-agent': 'aliyun-sdk-js/6.8.0'}
            canonical = ''.join(k + ':' + headers[k] + '\n' for k in sorted(headers))
            sign = 'PUT\n\n' + mime + '\n' + date + '\n' + canonical + '/zhihu-pics/' + key
            signature = base64.b64encode(hmac.digest(token['access_key'].encode(), sign.encode(), 'sha1')).decode()
            headers.update({'Authorization': 'OSS ' + token['access_id'] + ':' + signature,
                            'Content-Type': mime, 'Origin': 'https://zhuanlan.zhihu.com', 'Referer': 'https://zhuanlan.zhihu.com/'})
            stage = '上传图片文件'
            req = Request('https://zhihu-pics-upload.zhimg.com/' + key, data=data, method='PUT', headers=headers)
            with build_opener(NoRedirect()).open(req, timeout=40) as response:
                status = getattr(response, 'status', None)
                if isinstance(status, int):
                    diagnostics['upload_http_status'] = status
                response.read(4096)
        stage = '确认图片保存'
        native_checked = set()
        for attempt in range(10):
            result = window.call('image-status', {'id': image_id})
            diagnostics['polls'].append(_status_shape(result))
            if isinstance(result, dict) and result.get('status') in ('failed', 'error', 'rejected'):
                raise DeliveryError('知乎明确报告图片处理失败，正文未提交。')
            candidates = _candidates(result, file, path.suffix.lower())
            for source, url in candidates:
                # A pending upload's object path is a fallback only; let processing finish first.
                if source == 'upload_file.object_key' and attempt < 9:
                    continue
                checked = {'attempt': attempt + 1, 'source': source, 'path': urlsplit(url).path, 'host': urlsplit(url).hostname}
                if _private_expired(url):
                    checked['browser'] = {'ready': False, 'reason': 'expired-private-reference'}
                    diagnostics['checks'].append(checked)
                    continue
                proof = window.call('image-verify', {'url': url, 'diagnostics': True})
                ready = proof is True or isinstance(proof, dict) and proof.get('ready') is True
                if isinstance(proof, dict):
                    checked['browser'] = {k: proof[k] for k in ('ready', 'reason', 'width', 'height') if k in proof}
                else:
                    checked['browser'] = {'ready': ready}
                if not ready and url not in native_checked:
                    native_checked.add(url)
                    checked['native'] = _verify_cdn(url)
                    ready = checked['native']['ready']
                diagnostics['checks'].append(checked)
                if ready:
                    diagnostics.update(stage=stage, status='success')
                    return url
            if window.stopped.wait(1):
                raise DeliveryError('图片上传已停止。')
        raise DeliveryError('知乎尚未确认图片处理完成，正文未提交。请查看渠道上传诊断中的状态字段与图片路径。')
    except Exception as exc:
        diagnostics.update(stage=stage, status='failed')
        detail = str(exc) if isinstance(exc, DeliveryError) else ('HTTP ' + str(exc.code) if isinstance(exc, HTTPError) else '图片服务响应无效或连接失败。')
        raise DeliveryError('知乎' + stage + '失败：' + detail) from None
