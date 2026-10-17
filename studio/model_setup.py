"""Read-only first-use checks. Never probe providers or return credentials."""
from urllib.parse import urlsplit
from .models import endpoint
from .adapters import DeliveryError

_PLACEHOLDERS = {'replace_me', 'your_api_key', 'your-api-key', 'your_key', 'api_key_here',
                 'sk-xxx', 'sk-xxxx', 'sk-your-key', 'your-model', 'model-id', '填写模型 id'}

def usable(value):
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    return (value.lower() not in _PLACEHOLDERS and 'replace_me' not in value.lower()
            and not all(c in '*•●' for c in value) and not any(ord(c) < 32 for c in value))

def inspect(config):
    missing = []
    try:
        with config.lock:
            mode = config.get('AI_MODE', 'api')
            base = config.get('AI_BASE_URL')
            model = config.get('AI_MODEL')
            key = config.get('AI_API_KEY')
            dismissed = config.get('MODEL_SETUP_DISMISSED', False) is True
    except (ValueError, OSError, RuntimeError):
        return {'configured': False, 'should_prompt': False, 'missing': [
            {'field': 'ai-key', 'label': '无法读取本机模型配置，请在设置中检查'}], 'read_error': True}
    if mode != 'api':
        missing.append({'field': 'ai-mode', 'label': '调用方式请选择模型接口（API）'})
    try:
        normalized = endpoint(base)
        hostname = urlsplit(normalized).hostname or ''
        if hostname.endswith(('.example', '.invalid')) or hostname in ('example.com', 'api.example.com'):
            raise DeliveryError('placeholder')
    except DeliveryError:
        hostname = ''
        missing.append({'field': 'ai-base', 'label': '填写有效的模型接口地址'})
    local = hostname in ('localhost', '127.0.0.1', '::1')
    if not local and (not usable(key) or any(c.isspace() for c in key)):
        missing.append({'field': 'ai-key', 'label': '填写有效的模型调用 API Key'})
    elif key and (not usable(key) or any(c.isspace() for c in key)):
        missing.append({'field': 'ai-key', 'label': '替换无效或占位的 API Key'})
    if not usable(model):
        missing.append({'field': 'ai-model', 'label': '填写默认文字模型名称'})
    return {'configured': not missing, 'should_prompt': bool(missing) and not dismissed,
            'missing': missing, 'read_error': False}

def dismiss(config):
    config.update({'MODEL_SETUP_DISMISSED': True})
    return inspect(config)


def image_ready(values):
    """Validate the effective image connection, including shared credentials."""
    try:
        base = endpoint(values.get('IMAGE_BASE_URL'))
        hostname = urlsplit(base).hostname or ''
    except DeliveryError:
        return False
    key = values.get('IMAGE_API_KEY') or ''
    if hostname.endswith(('.example', '.invalid')) or hostname in ('example.com','api.example.com'):
        return False
    if not usable(values.get('IMAGE_MODEL')):
        return False
    return (not key and hostname in ('localhost','127.0.0.1','::1')) or (usable(key) and not any(c.isspace() for c in key))
