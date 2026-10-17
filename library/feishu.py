"""Online Feishu access using the official CLI's user OAuth credential store.

No token or document mirror is kept in the material library. Arguments are
passed directly to the executable, never to a shell.
"""
import json
import html
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


class FeishuError(ValueError):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status, self.code = status, code


def cli_path():
    configured = os.environ.get('FEISHU_CLI_PATH')
    candidates = [configured] if configured else []
    found = shutil.which('lark-cli.exe') or shutil.which('lark-cli')
    if found:
        candidates += [found, str(Path(found).parent / 'node_modules/@larksuite/cli/bin/lark-cli.exe')]
    if os.name == 'nt':
        candidates += [r'C:\nvm4w\nodejs\node_modules\@larksuite\cli\bin\lark-cli.exe']
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and Path(candidate).suffix.lower() not in {'.ps1', '.cmd', '.bat'}:
            return str(Path(candidate).resolve())
    raise FeishuError('未找到飞书官方 CLI，请配置 FEISHU_CLI_PATH', 503, 'cli_missing')


def _run(args, *, body=None, timeout=90):
    env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
    try:
        result = subprocess.run([cli_path(), *args], input=body, capture_output=True,
            env=env, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        # Do not retry writes: a timeout does not prove the server rejected them.
        raise FeishuError('飞书请求未完成；写操作请先核对飞书结果，勿直接重发', 504, 'request_uncertain') from None
    if len(result.stdout) + len(result.stderr) > 8 * 1024 * 1024:
        raise FeishuError('飞书返回内容过大，请缩小读取范围', 413, 'response_too_large')
    for raw in (result.stdout, result.stderr):
        try:
            value = json.loads(raw.decode('utf-8-sig'))
            if isinstance(value, dict):
                return value, result.returncode
        except (UnicodeError, ValueError):
            pass
    raise FeishuError('飞书未返回可识别结果', 502, 'invalid_response')


def _data(args, **kwargs):
    value, code = _run([*args, '--as', 'user'], **kwargs)
    if code or value.get('ok') is not True:
        error = value.get('error') or {}
        subtype = str(error.get('subtype', 'api_error'))[:80]
        auth = subtype in {'not_authenticated', 'token_missing', 'token_expired', 'refresh_token_expired', 'token_invalid'}
        message = '飞书用户登录已过期，请重新授权' if auth else '飞书拒绝此请求，请核对权限及参数'
        # Keep raw upstream details and credential diagnostics out of UI/logs.
        raise FeishuError(message, 401 if auth else 502, subtype)
    return _redact(value.get('data', {}))


def _redact(value):
    if isinstance(value, dict):
        return {k: '[凭据已隐藏]' if k.lower().replace('_', '') in {
            'accesstoken', 'useraccesstoken', 'tenantaccesstoken', 'refreshtoken', 'appsecret',
            'clientsecret', 'authorization'} else _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def status():
    value, code = _run(['auth', 'status', '--json', '--verify'], timeout=25)
    if code:
        raise FeishuError('无法核验飞书登录状态', 503, 'auth_check_failed')
    user = value.get('identities', {}).get('user', {})
    scopes = sorted(set(str(user.get('scope', '')).split()))
    return {'connected': user.get('available') is True and user.get('tokenStatus') == 'valid',
        'user_name': user.get('userName', ''), 'status': user.get('status', 'missing'),
        'token_status': user.get('tokenStatus', 'missing'), 'scopes': scopes, 'scope_count': len(scopes),
        'expires_at': user.get('expiresAt', ''), 'identity': 'user', 'verified': value.get('verified', False)}


def search(query='', page_token=''):
    if not isinstance(query, str) or len(query) > 30 or not isinstance(page_token, str) or len(page_token) > 4096:
        raise FeishuError('搜索词最多 30 个字符')
    args = ['drive', '+search', '--query', query, '--page-size', '20']
    if page_token:
        args += ['--page-token', page_token]
    data = _data(args)
    for row in data.get('results', []):
        meta = row.get('result_meta') or {}
        row['url'] = row.get('url') or meta.get('url', '')
        row['title'] = html.unescape(re.sub(r'<[^>]*>', '', str(row.get('title') or row.get('title_highlighted') or '飞书资料')))
        row['doc_type'] = meta.get('doc_types', row.get('entity_type', ''))
    return data


def document_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise FeishuError('请提供飞书文档或知识库链接')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise FeishuError('文档必须使用飞书 HTTPS 链接')
    host = parsed.hostname or ''
    if not (host.endswith('.feishu.cn') or host.endswith('.larksuite.com')):
        raise FeishuError('仅接受飞书文档链接')
    if not re.fullmatch(r'/(docx|wiki)/[A-Za-z0-9]{5,100}', parsed.path):
        raise FeishuError('当前正文读取支持飞书文档与知识库中的文档；表格可从飞书 API 工具访问')
    return f'https://{host}{parsed.path}'


def read_document(url, scope='full'):
    url = document_url(url)
    if scope not in {'full', 'outline'}:
        raise FeishuError('读取范围无效')
    data = _data(['docs', '+fetch', '--doc', url, '--doc-format', 'markdown', '--scope', scope])
    doc = data.get('document', {})
    if not isinstance(doc.get('content'), str):
        raise FeishuError('飞书没有返回文档正文', 502, 'content_missing')
    return {'url': url, 'document_id': doc.get('document_id', ''), 'revision_id': doc.get('revision_id'),
        'title': doc.get('title', ''), 'content': doc['content'], 'scope': scope, 'online': True}


# Business API routes; credential-minting/authentication endpoints never become
# frontend capabilities. The CLI fixes the upstream domain and user identity.
DOMAINS = {'approval', 'application', 'attendance', 'bitable', 'board', 'calendar', 'contact',
    'doc', 'docx', 'drive', 'event', 'im', 'mail', 'meeting_room', 'mindnote', 'minutes', 'note', 'okr',
    'search', 'sheets', 'slides', 'task', 'vc', 'wiki', 'workplace', 'ehr', 'human_auth', 'personal_settings'}


def call_api(values):
    method, path = values.get('method', 'GET'), values.get('path', '')
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
        raise FeishuError('HTTP 方法无效')
    match = re.fullmatch(r'/open-apis/([a-z_]+)/v[0-9]+(/[A-Za-z0-9_.:-]+)+', str(path))
    if not match or match[1] not in DOMAINS or '..' in path or len(path) > 1000:
        raise FeishuError('请输入飞书业务 API 路径，不接受任意网址或认证端点')
    params, data = values.get('params', {}), values.get('data', {})
    if not isinstance(params, dict) or not isinstance(data, dict):
        raise FeishuError('参数和请求体必须为 JSON 对象')
    if any(k in params for k in {'access_token', 'user_access_token', 'tenant_access_token', 'app_secret'}):
        raise FeishuError('凭据由服务管理，无需填写到请求参数')
    body = json.dumps(data, ensure_ascii=False).encode('utf-8')
    if len(body) > 1024 * 1024 or len(json.dumps(params)) > 16384:
        raise FeishuError('请求过大', 413)
    args = ['api', method, path, '--params', json.dumps(params, ensure_ascii=False)]
    if method != 'GET':
        args += ['--data', '-']
    return _data(args, body=body if method != 'GET' else None)


def get_route(path, params):
    if path == '/api/feishu/status':
        return status()
    if path == '/api/feishu/search':
        return search(params.get('q', [''])[0], params.get('page_token', [''])[0])
    if path == '/api/feishu/document':
        return read_document(params.get('url', [''])[0], params.get('scope', ['full'])[0])
    raise FeishuError('飞书接口不存在', 404)
