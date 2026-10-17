"""Persist bounded model identities for each call without retaining credentials or endpoints.

The reported model is only the API response's ``model`` field. It does not identify
which physical model a gateway ultimately ran. Historical records never consult
the current configuration.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import re
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS model_calls (
 id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
 task TEXT NOT NULL, requested_model TEXT NOT NULL DEFAULT '',
 reported_model TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'api',
 status TEXT NOT NULL, attempt INTEGER NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_model_calls_owner ON model_calls(owner_kind,owner_id,task,started_at);
"""

_response = ContextVar('paperplane_model_response', default=None)
_LABELS = {'chat': '对话安排', 'topic': '选题', 'outline': '提纲', 'body': '正文',
           'repurpose': '平台改编', 'review': '审稿', 'illustration_plan': '配图规划',
           'diagram': '流程图内容', 'cover': '封面', 'illustration': '正文插图与图集'}
_NO_MODEL = {'original': '原始图片', 'uploaded': '人工上传图片', 'upload': '人工上传图片',
             'manual': '人工编辑', 'template': '模板', 'layout': '确定性排版',
             'publish': '送入草稿箱', 'delivery': '送入草稿箱', 'format': '格式检查与修复'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _identity(value):
    """Accept an identifier, never an arbitrary returned object or a URL."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        return ''
    if re.search(r'https?://|bearer\s|(?:api[_-]?key|token|secret)\s*[:=]', value, re.I):
        return ''
    return value


def _route(route, task=''):
    route = route if isinstance(route, dict) else {}
    task = _identity(task or route.get('task') or route.get('model_task'))
    mode = 'program' if route.get('mode') == 'program' else 'api'
    return {'task': task, 'label': _LABELS.get(task, task or '模型调用'),
            'requested_model': '' if mode == 'program' else _identity(route.get('model') or route.get('AI_MODEL') or route.get('IMAGE_MODEL')),
            'reported_model': '', 'mode': mode}


def planned(config, task):
    """Explicitly prospective: this is configuration now, not past provenance."""
    if task in _NO_MODEL:
        return no_model(task, task)
    if task in ('cover', 'illustration'):
        from .image_provider import configuration
        route = configuration(config, task)
    else:
        from .model_routing import route_for
        route = route_for(config, task)
    result = _route(route, task)
    return {**result, 'source': 'planned', 'status': 'planned', 'calls': []}


def no_model(origin='manual', task=''):
    return {**_route({}, task), 'label': _NO_MODEL.get(origin, '人工处理'),
            'source': 'none', 'status': 'not_applicable', 'origin': origin, 'mode': 'none', 'calls': []}


def report_response(payload):
    """Called by adapters only after receiving a provider response."""
    active = _response.get()
    if active is not None and isinstance(payload, dict):
        active['reported_model'] = _identity(payload.get('model'))


def tracked_call(app, route, invoke):
    """Wrap exactly one network attempt (retry loops belong outside this function).

    Route must be the same frozen route used for the request. owner_kind/owner_id
    link it to a task; unrelated and simultaneous calls have isolated response data.
    No exception text, prompt, endpoint, or key is stored.
    """
    route = route if isinstance(route, dict) else {}
    values = _route(route)
    call_id = uuid.uuid4().hex
    owner_kind, owner_id = _identity(route.get('owner_kind')), _identity(route.get('owner_id'))
    with app.db.connect(immediate=True) as db:
        attempt = db.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM model_calls WHERE owner_kind=? AND owner_id=? AND task=?',
                             (owner_kind, owner_id, values['task'])).fetchone()[0]
        db.execute('INSERT INTO model_calls(id,owner_kind,owner_id,task,requested_model,mode,status,attempt,started_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (call_id, owner_kind, owner_id, values['task'], values['requested_model'], values['mode'], 'running', attempt, _now()))
    captured = {'reported_model': ''}
    token = _response.set(captured)
    status = 'failed'
    try:
        result = invoke()
        status = 'completed'
        return result
    finally:
        _response.reset(token)
        with app.db.connect(immediate=True) as db:
            db.execute('UPDATE model_calls SET reported_model=?,status=?,finished_at=? WHERE id=?',
                       (captured['reported_model'], status, _now(), call_id))


def interrupt(app):
    """Invoke once at application start, before starting model workers."""
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE model_calls SET status='interrupted',finished_at=? WHERE status='running'", (_now(),))


def public_for(app, owner_kind, owner_id, *, route=None, task='', origin='', connection=None):
    """Project only safe fields; use historical route/provider when no calls exist."""
    if origin in _NO_MODEL:
        return no_model(origin, task)
    query = 'SELECT id,task,requested_model,reported_model,mode,status,attempt,started_at,finished_at FROM model_calls WHERE owner_kind=? AND owner_id=?'
    params = (owner_kind, owner_id)
    if task:
        query += ' AND task=?'
        params += (task,)
    query += ' ORDER BY started_at,rowid'
    rows = connection.execute(query, params).fetchall() if connection else app.db.rows(query, params)
    calls = [{**dict(row), 'requested_model': _identity(row['requested_model']),
              'reported_model': _identity(row['reported_model'])} for row in rows]
    if calls:
        last = calls[-1]
        return {**_route(last, task or last['task']), 'requested_model': last['requested_model'],
                'reported_model': last['reported_model'], 'mode': last['mode'],
                'source': 'recorded', 'status': last['status'], 'calls': calls}
    values = _route(route, task)
    return {**values, 'source': 'legacy' if values['requested_model'] else 'unknown',
            'status': 'historical', 'calls': []}
