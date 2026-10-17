"""Loopback-only client; shared files never supply a network destination."""
import base64
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

BASE = 'http://127.0.0.1:8798'
MAX_BYTES = 32 * 1024 * 1024


def request(path, binary=False):
    from .service import Problem
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + path, timeout=10) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        return raw if binary else json.loads(raw)
    except (OSError, ValueError):
        raise Problem('共享资料服务未启动或资料不可读取，请打开本机资料库后重试', 503) from None


def listing(query='', source=''):
    return request('/api/items?q=' + quote(query) + '&source=' + quote(source))


def add_files(action, values):
    """Forward owner-authorized uploads to the existing shared catalogue."""
    from .service import Problem
    routes = {'folder-import': '/api/folder-import', 'upload': '/api/upload'}
    if action not in routes:
        raise Problem('未知资料导入操作')
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + '/api/session', timeout=5) as response:
            token = json.load(response)['token']
        body = json.dumps(values, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(BASE + routes[action], data=body, headers={
            'Content-Type': 'application/json', 'X-Library-Token': token})
        with opener.open(req, timeout=30) as response:
            result = response.read(1024 * 1024 + 1)
        if len(result) > 1024 * 1024:
            raise ValueError()
        return json.loads(result)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read(4096)).get('error', '资料导入失败')
        except (OSError, ValueError):
            message = '资料导入失败'
        raise Problem(message, exc.code) from None
    except (OSError, ValueError, KeyError):
        raise Problem('共享资料服务未启动或导入未完成，请稍后重试', 503) from None


def material(item_id, version):
    from .service import Problem
    if not re.fullmatch('[a-f0-9]{32}', str(item_id)) or not isinstance(version, int) or version < 1:
        raise Problem('资料编号或版本无效')
    path = '/api/items/' + item_id + '?version=' + str(version)
    item = request(path)
    raw = request('/api/items/' + item_id + '/content?version=' + str(version), True)
    if hashlib.sha256(raw).hexdigest() != item['sha256']:
        raise Problem('资料校验失败', 502)
    return item, raw


def import_material(app, values):
    from .service import Problem
    item, raw = material(values.get('id'), values.get('version'))
    suffix = Path(item['name']).suffix.lower()
    source = f"共享资料 {item['id']} v{item['version']} · {item['source']}/{item['relative_path']} · SHA256 {item['sha256']}"
    if suffix in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}:
        image = app.upload({'title': item['name'][:150], 'origin': 'imported', 'description': source,
                            'data': base64.b64encode(raw).decode()})
        return {'kind': 'image', 'image': image, 'source': source}
    if suffix not in {'.md', '.txt', '.csv'}:
        raise Problem('此文件请下载后使用；笔记可存入选题素材，图片可导入图片素材')
    try:
        body = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise Problem('此笔记不是 UTF-8，请下载查看') from None
    post = app.save_post({'title': Path(item['name']).stem[:150], 'body': body,
                         'summary': '', 'stage': 'idea', 'source': source})
    return {'kind': 'note', 'post': post, 'source': source}
