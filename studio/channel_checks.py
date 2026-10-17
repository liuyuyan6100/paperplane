"""Read-only channel diagnostics; never generate delivery receipts or publish."""
import json
from . import platform_catalog as catalog
from .adapters import DeliveryError, request_json
from .db import dumps, now


def check(app, channel):
    from .service import Problem
    if channel not in catalog.BY_ID:
        raise Problem('渠道不存在',404)
    if not app.channel_check_lock.acquire(blocking=False):
        raise Problem('正在验证其他渠道，请稍候',409)
    try:
        result = _check(app,channel)
        result.update(at=now(), channel=channel)
        # Diagnostic evidence remains distinct from real draft/publish receipts.
        with app.db.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       ('channel-check:'+channel,dumps(result)))
        return result
    finally:
        app.channel_check_lock.release()


def result(status, note, action=''):
    return dict(status=status,note=note,action=action)


def _check(app, channel):
    if channel=='local':
        return result('passed','本地资料库可访问，无需远端连接。')
    if channel=='wechat':
        if not app.config.get('WECHAT_APP_ID') or not app.config.get('WECHAT_APP_SECRET'):
            return result('needs_config','缺少公众号 AppID 或 AppSecret，请补充配置。','settings')
        from .wechat import test_connection
        try:
            test_connection(app.config)
        except DeliveryError as exc:
            return result('needs_config',str(exc),'settings')
        # Repair only the mode after credentials and draft-read permission pass.
        with app.db.connect(immediate=True) as db:
            row=db.execute("SELECT config FROM channels WHERE id='wechat'").fetchone()
            config=json.loads(row['config']); changed=config.get('mode')!='api'
            config['mode']='api'
            db.execute("UPDATE channels SET config=? WHERE id='wechat'",(dumps(config),))
        return result('passed',('已自动切换为公众号接口连接。' if changed else '')+'凭据和草稿读取权限通过；上传、送稿和公开发表未在此次测试。')
    if channel=='devto':
        key=app.config.get('DEVTO_API_KEY')
        if not key:return result('needs_config','缺少 DEV API Key，请在设置中填写。','settings')
        try:
            user=request_json('https://dev.to/api/users/me',headers={'api-key':key})
            if not isinstance(user,dict) or not user.get('id'):raise DeliveryError('DEV账号响应未能核实。')
        except DeliveryError as exc:
            return result('needs_config',str(exc),'settings')
        bridge=app.publishing_bridge
        if bridge.browser:
            if bridge.busy or not bridge.serial.acquire(blocking=False):
                return result('busy','DEV API连接通过；平台窗口正在处理任务，请稍后核验图片账号。')
            try:
                window=bridge.browser.windows['devto']
                try:
                    window._ensure_window(hidden=True)
                    account=window.call('identity')
                except DeliveryError as exc:
                    window.account_state='unverified'
                    return result('needs_login','DEV API连接通过；图片账号未能核验：'+str(exc)[:200],'login')
                window.account=account
                window.account_state='verified' if account else 'signed-out'
                window.account_changed()
                if not account:
                    return result('needs_login','DEV API连接通过；请在内置DEV窗口登录后核验图片上传账号。','login')
                if account.get('id')!=str(user['id']):
                    return result('needs_login','DEV内置窗口与API Key所属账号不同，请切回同一账号。','login')
            finally:
                bridge.serial.release()
            if app.config.get('DEVTO_API_KEY') != key:
                return result('needs_config','DEV API Key在核验期间已变化，请重新核验连接。','settings')
            return result('passed','DEV API与图片上传账号一致；此次未上传图片或发送文章。')
        if app.config.get('DEVTO_API_KEY') != key:
            return result('needs_config','DEV API Key在核验期间已变化，请重新核验连接。','settings')
        return result('passed','DEV API账号连接通过；本地图片上传需要Windows安装版登录同一账号。')
    if channel=='xiaohongshu':
        xhs=app.xhs_drafts
        if not xhs.browser:return result('needs_config','小红书需要Windows安装版。','settings')
        if not xhs.lock.acquire(blocking=False):return result('busy','小红书窗口正在处理任务，请稍后继续。')
        try:
            try:account=xhs.browser.verify_identity()
            except DeliveryError as exc:
                xhs.account_state='unverified'
                return result('needs_login','小红书账号暂未能核验：'+str(exc)[:180],'xhs-login')
            from .xhs_drafts import valid_account
            if account is not None and not valid_account(account):
                xhs.account_state='unverified'
                return result('needs_login','小红书账号响应未能核实，请在窗口检查后重试。','xhs-login')
            xhs.account=account
            xhs.account_state='verified' if account else 'signed-out'
            if account:return result('passed','小红书账号核验通过；此次未上传图片或保存草稿。')
            return result('needs_login','请在小红书窗口完成登录。','xhs-login')
        finally:xhs.lock.release()
    bridge=app.publishing_bridge
    if not bridge.browser:
        return result('needs_config','请使用Windows安装版内置窗口验证平台账号。','settings')
    if bridge.busy or not bridge.serial.acquire(blocking=False):
        return result('busy','平台窗口正在执行其他操作，请稍后继续验证。')
    try:
        window=bridge.browser.windows[channel]
        try:
            window._ensure_window(hidden=True)
            account=window.call('identity')
        except DeliveryError as exc:
            window.account_state='unverified'
            return result('needs_login','账号未能核验：'+str(exc)[:240]+' 请在平台窗口检查登录或权限。','login')
        window.account=account
        window.account_state='verified' if account else 'signed-out'
        window.account_changed()
        if not account:return result('needs_login','平台尚未登录，请在内置窗口完成登录。','login')
        if channel in catalog.LOGIN_ONLY_PLATFORMS:
            return result('not_implemented','账号核验通过；图片上传和草稿接口尚未开发，不能通过配置自动接通。')
        note='账号核验通过；此次未测试图片上传或草稿写入。'
        if channel=='cnblogs':note+='博客开通及审核权限仍需在平台后台确认。'
        return result('passed',note)
    finally:
        bridge.serial.release()
