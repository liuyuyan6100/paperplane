"""Upload DEV images with the owner's DEV browser session before API delivery."""
import hashlib
import re
from pathlib import Path
from .adapters import DeliveryError, request_json


def prepare(app, post):
    media=set(re.findall(r'/media/[a-f0-9]{32}\.(?:png|jpg|gif|webp)',post['body']))
    cover=post.get('cover','')
    if cover.startswith('/media/'):media.add(cover)
    if not media:return post
    bridge=app.publishing_bridge
    if not bridge.browser:
        raise DeliveryError('DEV本地图片需要Windows安装版中的DEV登录窗口。')
    if bridge.busy or not bridge.serial.acquire(blocking=False):
        raise DeliveryError('平台窗口正在处理其他任务，请稍后重试；正文尚未发送。')
    bridge.busy=True
    try:
        key=app.config.get('DEVTO_API_KEY')
        if not key:raise DeliveryError('请先配置DEV API Key。')
        user=request_json('https://dev.to/api/users/me',headers={'api-key':key})
        owner=str(user.get('id','')) if isinstance(user,dict) else ''
        if not re.fullmatch(r'[1-9][0-9]*',owner):raise DeliveryError('DEV API账号身份未能核实。')
        account=bridge.browser.login('devto',lambda text:setattr(bridge,'progress',text))
        if not account or account.get('id')!=owner:
            raise DeliveryError('DEV浏览器账号与API Key所属账号不同，请切回同一账号；图片和正文尚未发送。')
        root=(app.db.data_dir/'media').resolve()
        paths={}
        for url in media:
            path=(root/Path(url).name).resolve()
            if (not re.fullmatch(r'/media/[a-f0-9]{32}\.(?:png|jpg|gif|webp)',url)
                    or path.parent!=root or not path.is_file() or not 0<path.stat().st_size<=5_000_000):
                raise DeliveryError('DEV图片缺失、路径无效或超过5MB，正文尚未发送。')
            paths[url]=path
        prepared=dict(post)
        for index,(url,path) in enumerate(sorted(paths.items()),1):
            bridge.progress=f'正在上传DEV图片 {index}/{len(paths)}'
            remote=bridge.browser.upload('devto',path)
            from urllib.parse import urlsplit
            try:
                parsed=urlsplit(remote)
                upload_path=parsed.path.startswith('/uploads/articles/')
                s3=(parsed.hostname in ('dev-to-uploads.s3.amazonaws.com','thepracticaldev.s3.amazonaws.com')
                    or bool(re.fullmatch(r'(?:dev-to-uploads|thepracticaldev)\.s3\.[a-z]{2}-[a-z]+-[1-9]\.amazonaws\.com',parsed.hostname or '')))
                valid=(parsed.scheme=='https' and not parsed.username and not parsed.password and not parsed.port and not parsed.fragment
                       and (parsed.hostname in ('media.dev.to','media2.dev.to') or s3 and upload_path
                            or parsed.hostname=='res.cloudinary.com' and parsed.path.startswith('/practicaldev/')
                            or parsed.hostname=='dev.to' and parsed.path.startswith(('/remoteimages/uploads/articles/','/uploads/articles/'))))
            except (ValueError,TypeError):valid=False
            if not valid:raise DeliveryError('DEV图片上传未返回可信地址，正文尚未发送。')
            prepared['body']=prepared['body'].replace(url,remote)
            if cover==url:prepared['cover']=remote
        if bridge.browser.windows['devto'].call('identity')!=account:
            raise DeliveryError('DEV上传期间账号变化，正文尚未发送。')
        if '/media/' in prepared['body'] or prepared.get('cover','').startswith('/media/'):
            raise DeliveryError('DEV仍有未上传图片，正文尚未发送。')
        prepared['_devto_key_fingerprint']=hashlib.sha256(key.encode()).hexdigest()
        return prepared
    finally:
        bridge.busy=False
        bridge.serial.release()
