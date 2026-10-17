"""Shared online Feishu access. Posts keep links; task snapshots resolve them."""
import json
import urllib.error
import urllib.request
from urllib.parse import quote

from .service import Problem

BASE = 'http://127.0.0.1:8798'
PREFIX = 'feishu-online:'


def request(path, values=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        headers = {}
        raw = None
        if values is not None:
            with opener.open(BASE + '/api/session', timeout=5) as response:
                headers = {'Content-Type': 'application/json', 'X-Library-Token': json.load(response)['token']}
            raw = json.dumps(values, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(BASE + path, raw, headers)
        with opener.open(req, timeout=100) as response:
            content = response.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise Problem('飞书返回内容过大', 413)
        return json.loads(content)
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read(4096)).get('error', '飞书请求失败')
        except (ValueError, OSError):
            error = '飞书请求失败'
        raise Problem(error, exc.code) from None
    except (OSError, ValueError):
        raise Problem('飞书访问服务未启动或请求未完成；写操作请先核对结果', 503) from None


def route_get(path, params):
    return request(path + '?' + '&'.join(quote(k) + '=' + quote(v[0]) for k, v in params.items()))


def reference(app, values):
    from library.feishu import document_url, FeishuError
    try:
        url = document_url(values.get('url'))
    except FeishuError as exc:
        raise Problem(str(exc)) from None
    post = app.save_post({'title': str(values.get('title') or '飞书在线笔记')[:150], 'body': '',
        'stage': 'idea', 'source': PREFIX + url,
        'summary': '飞书在线引用；选为参考素材后，创建生成任务时读取最新正文。'})
    return {'post': post, 'online_reference': True}


def resolve_snapshot(post):
    if not str(post.get('source') or '').startswith(PREFIX):
        return post
    url = post['source'][len(PREFIX):]
    doc = request('/api/feishu/document?url=' + quote(url))
    return {**post, 'body': doc['content'], 'feishu_reference': {
        'url': doc['url'], 'document_id': doc['document_id'], 'revision_id': doc['revision_id']}}
