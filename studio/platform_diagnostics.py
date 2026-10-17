"""Fixed, read-only desktop observations; never upload, save, or change job state."""
import re
from .adapters import DeliveryError


def diagnose(app, values):
    from .service import Problem
    channel = values.get('channel')
    if not isinstance(channel, str) or channel not in ('51cto', 'xiaohongshu', 'zhihu', 'devto', 'cnblogs'):
        raise Problem('此渠道没有只读诊断入口')
    expected = {'channel', 'image_id'} if channel == 'zhihu' else {'channel'}
    if set(values) != expected:
        raise Problem('只读诊断参数不正确')
    if channel == 'zhihu' and not isinstance(values.get('image_id'), str):
        raise Problem('缺少已存在的知乎图片编号')
    if channel == 'zhihu' and not re.fullmatch(r'[1-9][0-9]{0,29}', values['image_id']):
        raise Problem('知乎图片编号格式不正确')
    if channel == 'xiaohongshu':
        xhs = app.xhs_drafts
        if not xhs.browser:
            raise Problem('请使用Windows安装版', 409)
        if not xhs.lock.acquire(blocking=False):
            raise Problem('小红书正在处理任务', 409)
        try:
            return {'channel': channel, 'read_only': True, 'diagnostic': xhs.browser.diagnose()}
        except DeliveryError as exc:
            raise Problem(str(exc), 502) from None
        finally:
            xhs.lock.release()
    bridge = app.publishing_bridge
    if not bridge.browser:
        raise Problem('请使用Windows安装版', 409)
    if bridge.busy or not bridge.serial.acquire(blocking=False):
        raise Problem('平台窗口正在处理任务', 409)
    try:
        # A check window cannot navigate or alter an editor with unsaved changes.
        window = (bridge.browser.windows if channel == 'devto' else bridge.browser.check_windows)[channel]
        window._ensure_window(hidden=True)
        if channel == 'zhihu':
            from .zhihu_images import _status_shape
            account = window.call('identity')
            if not account:
                raise Problem('请先在内置知乎窗口登录', 409)
            raw = window.call('image-status', {'id': values['image_id']})
            data = _status_shape(raw)
            if window.call('identity') != account:
                raise Problem('诊断期间知乎账号变化', 409)
        else:
            data = window.call('diagnose')
        return {'channel': channel, 'read_only': True, 'diagnostic': data}
    except DeliveryError as exc:
        raise Problem(str(exc), 502) from None
    finally:
        bridge.serial.release()
