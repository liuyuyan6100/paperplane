"""Learning progress only; never dispatches creative or publishing work."""
from .service import Problem
KEY = 'USAGE_GUIDE_WECHAT_V1'
COUNT = 6

def read(config):
    value = config.read().get(KEY, {})
    if not isinstance(value, dict):
        value = {}
    step = value.get('step', 0)
    done = value.get('completed', [])
    if not isinstance(done, list):
        done = []
    return {'step': step if type(step) is int and 0 <= step < COUNT else 0,
            'completed': sorted({i for i in done if type(i) is int and 0 <= i < COUNT}),
            'active': value.get('active') is True}

def update(config, values):
    action = values.get('action')
    if action not in ('start', 'pause', 'goto', 'complete', 'reset'):
        raise Problem('未知的使用引导操作')
    step = values.get('step')
    if action in ('goto', 'complete') and (type(step) is not int or not 0 <= step < COUNT):
        raise Problem('引导步骤无效')
    with config.lock:
        state = read(config)
        if action == 'reset':
            state = {'step': 0, 'completed': [], 'active': True}
        elif action == 'pause':
            state['active'] = False
        elif action == 'start':
            state['active'] = True
        elif action == 'goto':
            state.update(step=step, active=True)
        else:
            state['completed'] = sorted(set(state['completed']) | {step})
            state['step'] = min(step + 1, COUNT - 1)
            state['active'] = len(state['completed']) < COUNT
        config.update({KEY: state})
        return state
