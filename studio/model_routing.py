"""Function-specific models over the saved connection; credentials stay machine-local."""
import hashlib
import json

from .adapters import DeliveryError
from .models import endpoint

TEXT_TASKS = {
    'chat': '对话安排',
    'topic': '选题', 'outline': '提纲', 'body': '正文', 'repurpose': '平台改编',
    'review': '审稿', 'illustration_plan': '配图规划', 'diagram': '流程图内容',
}
IMAGE_TASKS = {'cover': '封面', 'illustration': '正文插图与图集'}
PENDING = {'script': '口播脚本工作流', 'transcription': '语音转写',
           'speech': '口播配音', 'video': '视频', 'music': '音乐'}


def model_key(task):
    if task not in TEXT_TASKS:
        raise DeliveryError('此功能尚未接入模型调用')
    return 'AI_' + task.upper() + '_MODEL'


def text_configuration(config, task):
    with config.lock:
        return {'AI_MODE': config.get('AI_MODE', 'api'),
                'AI_BASE_URL': config.get('AI_BASE_URL'), 'AI_API_KEY': config.get('AI_API_KEY'),
                'AI_MODEL': config.get(model_key(task)) or config.get('AI_MODEL')}


def signature(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def route_for(config, task, owner_kind='', owner_id=''):
    values = text_configuration(config, task)
    return {'task': task, 'model': values['AI_MODEL'], 'base_url': values['AI_BASE_URL'],
            'mode': values['AI_MODE'], 'owner_kind': owner_kind, 'owner_id': owner_id}


def capture(app, table, row, context, task, token):
    """Capture each actual generation start, including a user's manual retry."""
    route = route_for(app.config, task, table, row['id'])
    context['model_route'] = route
    # Callers only pass constant table names, never client-controlled identifiers.
    if table not in ('topic_plans', 'repurpose_variants', 'editorial_reviews', 'illustration_plans', 'performance_reviews'):
        raise ValueError('Invalid model snapshot table')
    from .db import dumps
    with app.db.connect(immediate=True) as db:
        db.execute(f'UPDATE {table} SET snapshot=? WHERE id=? AND run_token=?',
                   (dumps(context), row['id'], token))
    return route


def resolve(config, post):
    task = post.get('_model_task') or (post.get('_writing_phase') if post.get('_writing_phase') in ('outline', 'body') else 'body')
    values = text_configuration(config, task)
    route = post.get('_model_route')
    if route:
        if route.get('task') != task or route.get('base_url') != values['AI_BASE_URL'] or route.get('mode') != values['AI_MODE']:
            raise DeliveryError('模型连接已变化，请检查设置后手动重试；未向新地址发送原凭据')
        values['AI_MODEL'] = route['model']
    return values


def _text(value, label, limit=200):
    from .service import text_value, Problem
    value = text_value(value, label, limit)
    if any(ord(c) < 32 for c in value):
        raise Problem(label + '不能包含控制字符')
    return value


def save(config, values):
    from .service import Problem
    if not isinstance(values, dict) or set(values) - {'connection', 'text_models', 'image'}:
        raise Problem('模型设置格式不正确')
    with config.lock:
        changes = {}
        connection = values.get('connection', {})
        if not isinstance(connection, dict) or set(connection) - {'base_url', 'api_key', 'model', 'mode'}:
            raise Problem('统一连接格式不正确')
        if 'mode' in connection:
            if connection['mode'] != 'api':
                raise Problem('首次配置仅支持模型接口（API）')
            changes['AI_MODE'] = 'api'
        base = _text(connection.get('base_url', config.get('AI_BASE_URL')), '统一接口地址', 2000)
        if base:
            try:
                base = endpoint(base)
            except DeliveryError as exc:
                raise Problem(str(exc)) from None
        key = _text(connection.get('api_key', ''), '统一 API Key', 2000)
        old_base = str(config.get('AI_BASE_URL')).strip().rstrip('/')
        if base != old_base and config.get('AI_API_KEY') and not key:
            raise Problem('更换统一接口地址时请填写新接口的 API Key，避免发送原密钥')
        if any(c.isspace() for c in key):
            raise Problem('统一 API Key 不能包含空白字符')
        changes.update(AI_BASE_URL=base, AI_MODEL=_text(connection.get('model', config.get('AI_MODEL')), '默认文字模型'))
        if key:
            changes['AI_API_KEY'] = key
        text_models = values.get('text_models', {})
        if not isinstance(text_models, dict) or set(text_models) - set(TEXT_TASKS):
            raise Problem('请选择已接入的文字功能')
        for task, model in text_models.items():
            changes[model_key(task)] = _text(model, TEXT_TASKS[task] + '模型')
        image = values.get('image', {})
        if not isinstance(image, dict) or set(image) - {'use_shared', 'base_url', 'api_key', 'model', 'cover_model', 'illustration_model', 'references'}:
            raise Problem('图片模型设置格式不正确')
        if image:
            use_shared = image.get('use_shared', config.get('IMAGE_USE_SHARED', False))
            if type(use_shared) is not bool:
                raise Problem('请选择图片连接方式')
            mode = image.get('references', config.get('IMAGE_REFERENCES', 'edits'))
            if mode not in ('edits', 'none'):
                raise Problem('请选择图片参考图能力')
            image_base = _text(image.get('base_url', config.get('IMAGE_BASE_URL')), '独立图片接口地址', 2000)
            if image_base:
                try:
                    image_base = endpoint(image_base)
                except DeliveryError as exc:
                    raise Problem(str(exc)) from None
            image_key = _text(image.get('api_key', ''), '图片 API Key', 2000)
            if any(c.isspace() for c in image_key):
                raise Problem('图片 API Key 不能包含空白字符')
            if image_base != str(config.get('IMAGE_BASE_URL')).rstrip('/') and config.get('IMAGE_API_KEY') and not image_key:
                raise Problem('更换独立图片地址时请填写对应 API Key')
            changes.update(IMAGE_USE_SHARED=use_shared, IMAGE_BASE_URL=image_base, IMAGE_REFERENCES=mode)
            for field, setting in [('model', 'IMAGE_MODEL'), ('cover_model', 'IMAGE_COVER_MODEL'), ('illustration_model', 'IMAGE_ILLUSTRATION_MODEL')]:
                if field in image:
                    changes[setting] = _text(image[field], '图片模型')
            if image_key:
                changes['IMAGE_API_KEY'] = image_key
        # Validation is complete before the single atomic private-config update.
        config.update(changes)
    return public_settings(config)


def public_settings(config):
    from .model_setup import inspect, usable, image_ready
    from .image_provider import configuration
    with config.lock:
        tests = config.get('MODEL_TEST_RESULTS', {})
        if not isinstance(tests, dict):
            tests = {}
        setup = inspect(config)
        connection_ready = not setup['read_error'] and not any(item['field'] != 'ai-model' for item in setup['missing'])
        tasks = []
        for task, label in TEXT_TASKS.items():
            values = text_configuration(config, task)
            configured = connection_ready and usable(values['AI_MODEL'])
            test = tests.get(task, {})
            tested = test.get('signature') == signature(values) and test.get('passed') is True
            tasks.append({'id': task, 'label': label, 'model': config.get(model_key(task)),
                          'effective_model': values['AI_MODEL'], 'configured': configured,
                          'tested': tested, 'test_note': test.get('note', '') if tested else ''})
        images = []
        for task, label in IMAGE_TASKS.items():
            values = configuration(config, task)
            test = tests.get(task, {})
            tested = test.get('signature') == signature(values) and test.get('passed') is True
            images.append({'id': task, 'label': label, 'model': config.get('IMAGE_' + task.upper() + '_MODEL'),
                           'effective_model': values['IMAGE_MODEL'],
                           'configured': image_ready(values),
                           'tested': tested, 'test_note': test.get('note', '') if tested else ''})
        return {'setup': setup, 'connection': {'base_url': config.get('AI_BASE_URL'), 'model': config.get('AI_MODEL'),
                    'key_set': bool(config.get('AI_API_KEY'))}, 'text_tasks': tasks, 'image_tasks': images,
                'image': {'use_shared': config.get('IMAGE_USE_SHARED', False) is True,
                          'base_url': config.get('IMAGE_BASE_URL'), 'key_set': bool(config.get('IMAGE_API_KEY')),
                          'model': config.get('IMAGE_MODEL'), 'references': config.get('IMAGE_REFERENCES', 'edits')},
                'pending': [{'id': task, 'label': label, 'status': '待接入'} for task, label in PENDING.items()],
                'mode': config.get('AI_MODE', 'api')}


def test(app, task):
    from .service import Problem
    from . import image_provider
    if not isinstance(task, str) or task not in TEXT_TASKS and task not in IMAGE_TASKS:
        raise Problem('此功能待接入，不能自测')
    if app.config.get('AI_MODE', 'api') != 'api' and task in TEXT_TASKS:
        raise Problem('按功能分配模型需要先切换到 API 模式')
    values = text_configuration(app.config, task) if task in TEXT_TASKS else image_provider.configuration(app.config, task)
    if not app.ai_lock.acquire(blocking=False):
        raise Problem('已有模型请求正在执行，请稍后自测', 409)
    try:
        if task in TEXT_TASKS:
            from .adapters import ai_draft
            body = ai_draft({'title': '连接测试', 'summary': '', 'body': ''}, '只回复：连接成功', values)
            note = '文字调用成功；仅验证连接，不代表内容质量。'
        else:
            image_provider.generate(values, '纯白背景中央一个蓝色圆形，无文字。', '1024x1024', [])
            body = ''
            note = '单张文生图返回有效图片；参考图编辑尚未自测。'
        with app.config.lock:
            results = app.config.get('MODEL_TEST_RESULTS', {})
            results = dict(results) if isinstance(results, dict) else {}
            results[task] = {'signature': signature(values), 'passed': True, 'note': note}
            app.config.update({'MODEL_TEST_RESULTS': results})
        return {'task': task, 'model': values.get('AI_MODEL', values.get('IMAGE_MODEL')), 'note': note, 'body': body[:500]}
    except DeliveryError as exc:
        with app.config.lock:
            results = app.config.get('MODEL_TEST_RESULTS', {})
            results = dict(results) if isinstance(results, dict) else {}
            results.pop(task, None)
            app.config.update({'MODEL_TEST_RESULTS': results})
        raise Problem(str(exc)) from None
    finally:
        app.ai_lock.release()
