import base64
import hashlib
import html
import io
import json
import mimetypes
import re
import secrets
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import adapters
from .db import dumps
from .markdown import render
from .service import Problem

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def article_html(post, back=True):
    title = html.escape(post["title"])
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title} · 纸飞机</title><link rel="stylesheet" href="/style.css"></head><body class="reader">'
            + ('<nav><a href="/feed">← 本地专栏</a><a href="/">打开工作台</a></nav>' if back else '')
            + f'<article class="prose"><div class="eyebrow">纸飞机 / 本地专栏</div><h1>{title}</h1><p class="muted">{html.escape(post.get("author", ""))}</p>'
            + render(post["body"]) + '</article></body></html>')


def export_package(job, data_dir):
    if job.get("channel_id") == "xiaohongshu":
        from .xiaohongshu import package
        return package(job, data_dir)
    post = job["snapshot"]
    out = io.BytesIO()
    body = post["body"]
    images = set(re.findall(r"/media/[a-f0-9]{32}\.(?:png|jpg|gif|webp)", body + " " + post.get("cover", "")))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        videos=set(re.findall(r'/video/[a-f0-9]{32}\.mp4',body))
        for video in videos:
            file=data_dir/'videos'/Path(video).name
            if not file.is_file():raise Problem('导出的视频文件缺失，请恢复视频素材')
            archive.write(file,'videos/'+file.name);body=body.replace(video,'videos/'+file.name)
        for image in images:
            path = data_dir / "media" / Path(image).name
            if path.is_file():
                archive.write(path, "images/" + path.name)
                body = body.replace(image, "images/" + path.name)
        archive.writestr("article.md", "# " + post["title"] + "\n\n" + body)
        export_post = {**post, "body": body}
        # Relative images are safe in a downloaded file; renderer intentionally rejects them for web content.
        content = article_html(post, False)
        for video in videos:content=content.replace(video,'videos/'+Path(video).name)
        for image in images:
            content = content.replace(image, "images/" + Path(image).name)
        content = content.replace('<link rel="stylesheet" href="/style.css">', '<style>body{max-width:760px;margin:40px auto;padding:24px;font:17px/1.9 system-ui;color:#202938}img{max-width:100%}pre{overflow:auto;background:#f1f5f9;padding:18px}blockquote{border-left:3px solid #2563eb;padding-left:18px}a{color:#2563eb}</style>')
        archive.writestr("article.html", content)
        archive.writestr("metadata.json", dumps({k: post.get(k) for k in ("title", "author", "summary", "tags", "cover", "source", "version")}))
        archive.writestr("README.txt", "发布包来自纸飞机工作台，任务编号：" + job["id"] + "\n使用 article.md 或 article.html 发布文章。本地图片在 images 目录，请在目标平台重新上传。发表后回到工作台登记实际链接。\n")
    return out.getvalue()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, studio, access=None):
        self.studio = studio
        self.access = access
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperPlane/1.0"

    def log_message(self, format, *args):
        # Do not log query strings, request bodies, credentials or generated content.
        return

    def send(self, data, status=200, content_type="application/json; charset=utf-8", download=None):
        if isinstance(data, (dict, list)):
            data = dumps(data).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: http: data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'; object-src 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except ConnectionError:
            pass

    def guarded(self, mutation=False):
        port = self.server.server_port
        access = self.server.access
        allowed_hosts = {access.host} if access else {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed_hosts:
            raise Problem("请使用工作台的正式访问地址" if access else "只允许从本机地址访问", 403)
        if access and not access.verify(self.headers.get("Cf-Access-Jwt-Assertion", "")):
            raise Problem("登录已过期，请保留未保存文本，并在新窗口重新登录工作台", 401)
        origin = self.headers.get("Origin")
        allowed_origins = {access.origin} if access else {"http://" + h for h in allowed_hosts}
        if origin and origin not in allowed_origins or (access and mutation and origin not in allowed_origins):
            raise Problem("拒绝跨站请求", 403)
        # A valid cloud login can redirect a navigation back from the identity
        # provider. Reads still require a verified owner JWT; writes stay same-origin.
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and (mutation or not access):
            raise Problem("拒绝跨站请求", 403)
        if mutation:
            if not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.token):
                raise Problem("页面会话已更新，请刷新后重试", 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise Problem("请求必须使用 JSON", 415)

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise Problem("无效请求长度") from None
        library_upload = urlsplit(self.path).path == '/api/library/upload' and not self.server.access
        video_upload=bool(re.fullmatch(r'/api/posts/[a-f0-9]{32}/videos',urlsplit(self.path).path))
        limit = (10*1024*1024*4//3+20000) if video_upload else (32 * 1024 * 1024 * 4 // 3 + 10000 if library_upload else 10_000_000)
        if size < 0 or size > limit:
            raise Problem("单个资料不能超过 32 MiB" if library_upload else "请求不能超过 10 MB", 413)
        self.connection.settimeout(30)
        try:
            value = json.loads(self.rfile.read(size))
        except (ValueError, OSError):
            raise Problem("JSON 格式无效") from None
        if not isinstance(value, dict):
            raise Problem("请求内容必须是 JSON 对象")
        return value

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, mutation):
        try:
            if not mutation and self.path == "/healthz":
                self.send({"ok": bool(self.server.studio.worker and self.server.studio.worker.is_alive())})
                return
            if urlsplit(self.path).path == '/api/profile-sync':
                access = self.server.access
                if not mutation or not access or self.headers.get('Host') != access.host or not access.verify_sync(self.headers.get('Cf-Access-Jwt-Assertion', ''), self.headers.get('X-PaperPlane-Device', ''), self.headers.get('X-PaperPlane-Sync-Key', '')):
                    raise Problem('同步设备未经授权', 401)
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise Problem('请求必须使用 JSON', 415)
                from .profile_sync import exchange
                self.send(exchange(self.server.studio, self.body()))
                return
            self.guarded(mutation)
            path = urlsplit(self.path).path
            result = self.route_post(path, self.body()) if mutation else self.route_get(path)
            if result is not None:
                self.send(result)
        except Problem as exc:
            self.send({"error": str(exc), "details": exc.details}, exc.status)
        except adapters.DeliveryError as exc:
            self.send({"error": str(exc)}, 502)
        except Exception:
            self.send({"error": "本地服务处理失败。内容未确认保存时请保留编辑文本，稍后重试。"}, 500)

    def route_get(self, path):
        app = self.server.studio
        video_list=re.fullmatch(r'/api/posts/([a-f0-9]{32})/videos',path)
        if video_list:
            from .videos import listing
            app.post(video_list[1]);return {'videos':listing(app,video_list[1])}
        video_file=re.fullmatch(r'/video/([a-f0-9]{32})\.mp4',path)
        if video_file:
            from .videos import get
            get(app,video_file[1]);file=app.db.data_dir/'videos'/(video_file[1]+'.mp4')
            if file.is_symlink() or file.resolve().parent != (app.db.data_dir/'videos').resolve() or not file.is_file():raise Problem('视频文件不存在',404)
            total=file.stat().st_size;start,end=0,total-1;status=200
            value=self.headers.get('Range')
            if value:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
                if not match or not any(match.groups()):raise Problem('视频范围无效',416)
                if match[1]:start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
                else:start=max(0,total-int(match[2]))
                if not 0<=start<=end<total:raise Problem('视频范围无效',416)
                status=206
            self.send_response(status);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
            self.end_headers()
            try:
                with file.open('rb') as f:f.seek(start);self.wfile.write(f.read(end-start+1))
            except ConnectionError:pass
            return None
        assist = re.fullmatch(r'/api/editor-assistant/(post|outline|body|variant|plan|image)/([a-f0-9]{32})', path)
        if assist:
            return app.editor_assistant.get(*assist.groups())
        if path == '/api/model-provenance/planned':
            from .model_provenance import planned
            from .model_routing import TEXT_TASKS, IMAGE_TASKS
            return {'tasks': [planned(app.config, t) for t in (*TEXT_TASKS, *IMAGE_TASKS)]}
        flow_match = re.fullmatch(r'/api/posts/([a-f0-9]{32})/flow', path)
        if flow_match:
            return app.article_flow.get(flow_match[1])
        post_learning = re.fullmatch(r'/api/posts/([a-f0-9]{32})/learning', path)
        if post_learning:
            post = app.post(post_learning[1])
            data = app.learning.get(post['profile_id']) if post.get('profile_id') else None
            return {'profile_id':post.get('profile_id'), 'events':[e for e in data['events'] if e['post_id']==post['id']][:10] if data else [],
                    'excluded':bool(app.db.one('SELECT 1 FROM profile_learning_exclusions WHERE post_id=? AND excluded=1', (post['id'],))),
                    'enabled':bool(data and data['settings']['enabled'])}
        if path == '/api/profile-sync/status':
            return app.profile_sync.status()
        if path == '/api/chat':
            from urllib.parse import parse_qs
            visibility=parse_qs(urlsplit(self.path).query).get('visibility',['active'])[0]
            return {'conversations': app.chat.listing(visibility), 'default_profile_id':app.chat.default_profile()}
        chat_match = re.fullmatch(r'/api/chat/([a-f0-9]{32})', path)
        if chat_match:
            return app.chat.get(chat_match[1])
        if path == "/api/desktop/update":
            updater = getattr(self.server, "desktop_updater", None)
            return updater.status() if updater else {"enabled": False}
        if path.startswith('/api/feishu/'):
            if self.server.access:
                raise Problem('飞书访问服务当前在本机；云端尚未绑定用户授权', 503)
            from urllib.parse import parse_qs
            from .feishu import route_get
            return route_get(path, parse_qs(urlsplit(self.path).query))
        if path == "/api/library":
            if self.server.access:
                raise Problem("共享资料主库在本地 PC，请在本机工作台使用", 503)
            from urllib.parse import parse_qs
            from .shared_library import listing
            params = parse_qs(urlsplit(self.path).query)
            return listing(params.get("q", [""])[0], params.get("source", [""])[0])
        if path == "/api/health":
            from desktop_version import VERSION
            return {"app": "paperplane-studio", "version": VERSION, "worker_alive": bool(app.worker and app.worker.is_alive()), "data_dir": str(app.db.data_dir)}
        if path == "/api/session":
            return {"token": self.server.token, "deployment": "cloud" if self.server.access else "local"}
        if path == "/api/state":
            from .profiles import listing
            return {**app.state(), "profiles": listing(app)}
        learning_match = re.fullmatch(r'/api/profiles/([a-f0-9]{32})/learning', path)
        if learning_match:
            return app.learning.get(learning_match[1])
        memory_match = re.fullmatch(r"/api/profiles/([a-f0-9]{32})/memories(?:/([a-f0-9]{32})/history)?", path)
        if memory_match:
            from . import memories
            if memory_match[2]:
                return {"revisions": memories.revisions(app, memory_match[1], memory_match[2])}
            return {"memories": memories.listing(app, memory_match[1])}
        if path == "/api/profiles":
            from .profiles import listing
            return {"profiles": listing(app)}
        match = re.fullmatch(r'/api/profiles/([a-f0-9]{32})/history(?:/([1-9][0-9]{0,8}))?', path)
        if match:
            from . import profiles
            return profiles.revision(app, match[1], int(match[2])) if match[2] else profiles.history(app, match[1])
        match = re.fullmatch(r"/api/profiles/([a-f0-9]{32})", path)
        if match:
            from .profiles import get
            return get(app, match[1])
        if path == "/api/publishing-bridge":
            return app.publishing_bridge.status()
        if path == '/api/xhs-drafts':
            return app.xhs_drafts.status()
        if path == '/xhs-drafts.js':
            return self.send((WEB / 'xhs-drafts.js').read_bytes(), content_type='text/javascript; charset=utf-8')
        if path == "/api/platforms":
            return {"platforms": app.channels()}
        if path == "/api/settings":
            return {**app.settings(), "deployment": "cloud" if self.server.access else "local"}
        if path == '/api/usage-guide':
            from .usage_guide import read
            return read(app.config)
        if path == '/api/model-setup':
            from .model_setup import inspect
            return inspect(app.config)
        if path == '/api/model-settings':
            from .model_routing import public_settings
            return public_settings(app.config)
        if path == "/api/topics":
            return {"plans": app.topics.list()}
        if path == '/api/illustrations/settings':
            from .image_provider import public_settings
            return public_settings(app.config)
        if path == '/api/reviews':
            from urllib.parse import parse_qs
            params = parse_qs(urlsplit(self.path).query)
            return app.reviews.listing(params.get('post_id', [None])[0])
        if path == '/api/performance':
            return app.performance.overview()
        match = re.fullmatch(r'/api/performance/jobs/([a-f0-9]{32})', path)
        if match:
            return {'samples': app.performance.samples(match[1])}
        match = re.fullmatch(r'/api/performance/reviews/([a-f0-9]{32})', path)
        if match:
            return app.performance.get(match[1])
        match = re.fullmatch(r'/api/reviews/([a-f0-9]{32})', path)
        if match:
            return app.reviews.get(match[1])
        if path == '/api/illustrations':
            from urllib.parse import parse_qs
            params = parse_qs(urlsplit(self.path).query)
            archived = params.get('archived', ['0'])[0]
            if archived not in ('0', '1'):
                raise Problem('归档筛选值不正确', 400)
            return {'plans': app.illustrations.listing(params.get('post_id', [None])[0], archived == '1')}
        match = re.fullmatch(r'/api/illustrations/([a-f0-9]{32})', path)
        if match:
            return app.illustrations.get(match[1])
        if path == "/api/repurpose":
            from urllib.parse import parse_qs
            params = parse_qs(urlsplit(self.path).query)
            return {"variants": app.repurpose.list(params.get("post_id", [None])[0])}
        match = re.fullmatch(r"/api/repurpose/([a-f0-9]{32})", path)
        if match:
            return app.repurpose.get(match[1])
        match = re.fullmatch(r"/api/topics/([a-f0-9]{32})", path)
        if match:
            return app.topics.get(match[1])
        if path == "/api/workflows":
            from urllib.parse import parse_qs
            params = parse_qs(urlsplit(self.path).query)
            return {"workflows": app.workflows.list(params.get("post_id", [None])[0])}
        match = re.fullmatch(r"/api/workflows/([a-f0-9]{32})", path)
        if match:
            return app.workflows.get(match[1])
        if path == "/api/assets":
            from .assets import listing
            return listing(app)
        if path == "/api/legacy":
            from .migration import inspect_legacy
            return inspect_legacy(ROOT)
        if re.fullmatch(r"/api/posts/[a-f0-9]{32}/revisions", path):
            return {"revisions": app.db.rows("SELECT version,snapshot,created_at FROM revisions WHERE post_id=? ORDER BY version DESC LIMIT 30", (path.split('/')[3],))}
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(/package|/markdown|/html)?", path)
        if match:
            job = app.job_detail(match[1])
            if match[2] and not job["snapshot"]["body"].strip():
                raise Problem("此历史记录不含文章正文，请在内容库恢复文章并导入原始 Markdown", 422)
            if match[2] == "/package":
                self.send(export_package(job, app.db.data_dir), content_type="application/zip", download="paperplane-" + job["id"][:8] + ".zip")
                return None
            if match[2] == "/markdown":
                self.send("# " + job["snapshot"]["title"] + "\n\n" + job["snapshot"]["body"], content_type="text/markdown; charset=utf-8", download="article.md")
                return None
            if match[2] == "/html":
                return {"html": render(job["snapshot"]["body"])}
            return job
        if path == "/feed":
            rows = app.db.rows("SELECT * FROM publications ORDER BY published_at DESC")
            cards = []
            for row in rows:
                p = json.loads(row["snapshot"])
                cards.append(f'<a class="feed-card" href="/read/{row["job_id"]}"><small>{html.escape(row["published_at"][:10])} · v{p["version"]}</small><h2>{html.escape(p["title"])}</h2><p>{html.escape(p["summary"])}</p><span>阅读全文 →</span></a>')
            self.send('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>本地专栏 · 纸飞机</title><link rel="stylesheet" href="/style.css"></head><body class="reader"><nav><strong>纸飞机 / 本地专栏</strong><a href="/">打开工作台 →</a></nav><h1>写下来的，都在这里。</h1><p class="muted">' + ('此专栏保存在云端，登录工作台后可阅读。' if self.server.access else '此专栏保存在当前电脑，仅本机可访问。') + '</p><div class="feed-grid">' + ("".join(cards) or '<p>还没有文章。在工作台选择「本地专栏」即可发布第一篇。</p>') + '</div></body></html>', content_type="text/html; charset=utf-8")
            return None
        match = re.fullmatch(r"/read/([a-f0-9]{32})", path)
        if match:
            row = app.db.one("SELECT snapshot FROM publications WHERE job_id=?", (match[1],))
            if not row:
                raise Problem("已发布文章不存在", 404)
            self.send(article_html(json.loads(row["snapshot"])), content_type="text/html; charset=utf-8")
            return None
        match = re.fullmatch(r"/media/([a-f0-9]{32}\.(png|jpg|gif|webp))", path)
        if match:
            file = app.db.data_dir / "media" / match[1]
            if not file.is_file():
                raise Problem("图片不存在", 404)
            self.send(file.read_bytes(), content_type=mimetypes.guess_type(file)[0])
            return None
        static = {"/": "index.html", "/app.js": "app.js", "/model-provenance.js": "model-provenance.js", "/article-flow.js": "article-flow.js", "/assets.js": "assets.js", "/xhs.js": "xhs.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg",
                  "/profiles.js": "profiles.js", "/memories.js": "memories.js", "/profile-learning.js": "profile-learning.js", "/performance.js": "performance.js", "/profile-sync.js": "profile-sync.js", "/topics.js": "topics.js", "/repurpose.js": "repurpose.js", "/platforms.js": "platforms.js", "/illustrations.js": "illustrations.js", "/reviews.js": "reviews.js", "/shared-library.js": "shared-library.js", "/model-settings.js": "model-settings.js", "/model-wizard.js": "model-wizard.js", "/usage-guide.js": "usage-guide.js", "/chat.js": "chat.js",
                  "/desktop-updates.js": "desktop-updates.js", "/editor-assistant.js": "editor-assistant.js", "/videos.js":"videos.js",
                  "/workflows.js": "workflows.js", "/workflows.css": "workflows.css", "/wechat-settings.js": "wechat-settings.js",
                  "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js", "/pwa.js": "pwa.js",
                  "/mobile.css": "mobile.css", "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png"}
        if path in static:
            file = WEB / static[path]
            self.send(file.read_bytes(), content_type=(mimetypes.guess_type(file)[0] or "application/octet-stream") + ("; charset=utf-8" if file.suffix != '.svg' else ''))
            return None
        raise Problem("页面不存在", 404)

    def route_post(self, path, values):
        video_import=re.fullmatch(r'/api/posts/([a-f0-9]{32})/videos',path)
        video_action=re.fullmatch(r'/api/videos/([a-f0-9]{32})(/wechat|/wechat-existing|/wechat-draft)?',path)
        if video_import:
            from .videos import upload
            return upload(self.server.studio,video_import[1],values)
        if video_action:
            from .videos import update,prepare_wechat,associate_material,associate_draft
            if video_action[2]=='/wechat-draft':return associate_draft(self.server.studio,video_action[1],values)
            if video_action[2]=='/wechat-existing':return associate_material(self.server.studio,video_action[1],values)
            return prepare_wechat(self.server.studio,video_action[1]) if video_action[2] else update(self.server.studio,video_action[1],values)
        assist = re.fullmatch(r'/api/editor-assistant/(post|outline|body|variant|plan|image)/([a-f0-9]{32})', path)
        if assist:
            return self.server.studio.editor_assistant.send(*assist.groups(), values)
        assist_action = re.fullmatch(r'/api/editor-assistant/turns/([a-f0-9]{32})/(accept|retry)', path)
        if assist_action:
            manager = self.server.studio.editor_assistant
            return manager.accept(assist_action[1], values) if assist_action[2] == 'accept' else manager.retry(assist_action[1])
        if path.startswith('/api/profile-sync/'):
            if self.server.access:
                raise Problem('请在 PC 本机工作台配置同步', 409)
            sync = self.server.studio.profile_sync
            if path == '/api/profile-sync/run':
                return sync.run()
            if path == '/api/profile-sync/resolve':
                return sync.resolve(values)
            if path == '/api/profile-sync/settings':
                return sync.configure(values)
        if path.startswith("/api/desktop/update/"):
            callback = getattr(self.server, "desktop_update_action", None)
            if not callback:
                raise Problem("请在已安装的客户端中使用软件更新", 404)
            from desktop_updates import UpdateError
            try:
                action = path.rsplit("/", 1)[-1]
                return callback(action, values) if action == "configure" else callback(action)
            except UpdateError as exc:
                raise Problem(str(exc), 400) from None
        if path in {'/api/feishu/call', '/api/feishu/reference'}:
            if self.server.access:
                raise Problem('云端尚未绑定飞书用户授权', 503)
            from .feishu import request, reference
            return reference(self.server.studio, values) if path.endswith('/reference') else request(path, values)
        if path in {"/api/library/folder-import", "/api/library/upload"}:
            if self.server.access:
                raise Problem("共享资料主库在本地 PC，请在本机工作台使用", 503)
            from .shared_library import add_files
            return add_files(path.rsplit('/', 1)[-1], values)
        if path == "/api/library/import":
            if self.server.access:
                raise Problem("共享资料主库在本地 PC，请在本机工作台使用", 503)
            from .shared_library import import_material
            return import_material(self.server.studio, values)
        app = self.server.studio
        if path == '/api/chat':
            return app.chat.create(values)
        chat_match = re.fullmatch(r'/api/chat/([a-f0-9]{32})/(messages|action|profile|organize)', path)
        if chat_match:
            return {'messages':app.chat.send,'action':app.chat.action,'profile':app.chat.set_profile,'organize':app.chat.organize}[chat_match[2]](chat_match[1], values)
        if path == "/api/shutdown":
            if self.server.access:
                raise Problem("云端服务由 Docker Compose 管理", 403)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return {"ok": True}
        if path == "/api/posts":
            return app.save_post(values)
        learning_match = re.fullmatch(r'/api/profiles/([a-f0-9]{32})/learning/(settings|organize|exclude|confirm-many|events/[a-f0-9]{32}|resolve/[a-f0-9]{32})', path)
        if learning_match:
            profile_id, action = learning_match.groups()
            if action == 'settings':
                return app.learning.options(profile_id, values)
            if action in ('organize','exclude'):
                return getattr(app.learning, action)(profile_id, values)
            if action == 'confirm-many':
                return app.learning.confirm_many(profile_id, values)
            group, ident = action.split('/')
            return app.learning.resolve(profile_id, ident, values) if group == 'resolve' else app.learning.action(profile_id, ident, values)
        memory_match = re.fullmatch(r"/api/profiles/([a-f0-9]{32})/memories(?:/(extract|[a-f0-9]{32}))?", path)
        if memory_match:
            from . import memories
            if memory_match[2] == "extract":
                return memories.extract(app, memory_match[1], values)
            if memory_match[2]:
                return memories.update(app, memory_match[1], memory_match[2], values)
            return memories.propose(app, memory_match[1], values)
        match = re.fullmatch(r'/api/profiles/([a-f0-9]{32})/(restore|duplicate)', path)
        if match:
            from . import profiles
            return getattr(profiles, match[2])(app, match[1], values)
        if path == "/api/profiles":
            from .profiles import save
            return save(app, values)
        match = re.fullmatch(r"/api/profiles/([a-f0-9]{32})", path)
        if match:
            from .profiles import save
            return save(app, values, match[1])
        if path == "/api/preview":
            from .service import text_value
            return {"html": render(text_value(values.get("body", ""), "正文", 200000))}
        if path == "/api/media":
            return app.upload(values)
        if re.fullmatch(r"/api/assets/[a-f0-9]{32}", path):
            from .assets import update
            return update(app, path.split("/")[-1], values)
        if path == "/api/ai/diagram":
            from .assets import diagram
            from .service import text_value
            from .model_provenance import public_for, no_model
            post = app.post(values.get("post_id"))
            result = diagram(app, post, text_value(values.get("instruction", "提炼文章流程"), "配图要求", 3000))
            return {**result, 'model_provenance': public_for(app, 'posts', post['id'], task='diagram'),
                    'layout_model': no_model('layout')}
        if path == '/api/publishing-bridge/open':
            if self.server.access:
                raise Problem('平台窗口仅支持 Windows 安装版的本机工作台。',403)
            return app.publishing_bridge.launch(values.get('platform'),values.get('job_id'))
        if path in ("/api/publishing-bridge/setup", "/api/publishing-bridge/check"):
            if self.server.access:
                raise Problem("浏览器送稿仅支持本机工作台", 403)
            return app.publishing_bridge.setup() if path.endswith("/setup") else {**app.publishing_bridge.status(), "accounts": app.publishing_bridge.accounts()}
        if path == '/api/xhs-drafts/open' or re.fullmatch(r'/api/jobs/[a-f0-9]{32}/xhs-(send|open)', path):
            if self.server.access:
                raise Problem('小红书本地草稿仅能从 Windows 安装版的本机工作台打开。', 403)
            if path == '/api/xhs-drafts/open':
                return app.xhs_drafts.launch()
            job_id = path.split('/')[3]
            return app.xhs_drafts.queue_existing(job_id, values) if path.endswith('xhs-send') else app.xhs_drafts.launch(job_id)
        if path == '/api/platforms/diagnose':
            if self.server.access:
                raise Problem('平台只读诊断仅支持本机工作台。',403)
            from .platform_diagnostics import diagnose
            return diagnose(app,values)
        if path == '/api/platforms/verify':
            if self.server.access:
                raise Problem('渠道自动处理仅支持本机工作台。',403)
            from .channel_checks import check
            if set(values) != {'channel'} or not isinstance(values.get('channel'),str):
                raise Problem('请选择单个待验证渠道')
            return check(app,values['channel'])
        if path == "/api/platforms":
            from .platform_catalog import BY_ID
            changes = values.get('channels')
            if set(values) != {'channels'} or not isinstance(changes,list) or len(changes)>len(BY_ID):
                raise Problem('只允许修改平台启用状态')
            if any(not isinstance(c,dict) or set(c)!={'id','enabled'} or not isinstance(c['id'],str) or c['id'] not in BY_ID or type(c['enabled']) is not bool for c in changes):
                raise Problem('平台设置格式不正确')
            if len({c['id'] for c in changes}) != len(changes):
                raise Problem('平台不能重复')
            with app.db.connect(immediate=True) as db:
                for c in changes:
                    db.execute('UPDATE channels SET enabled=? WHERE id=?',(int(c['enabled']),c['id']))
            return {'platforms':app.channels()}
        if path == "/api/settings":
            return app.save_settings(values)
        if path == '/api/model-settings':
            from .model_routing import save
            return save(app.config, values)
        if path == '/api/usage-guide':
            from .usage_guide import update
            return update(app.config, values)
        if path == '/api/model-setup/dismiss':
            from .model_setup import dismiss
            return dismiss(app.config)
        if path == '/api/model-settings/test':
            from .model_routing import test
            return test(app, values.get('task'))
        if path == "/api/wechat/test":
            from .wechat import test_connection
            return test_connection(app.config)
        if path == "/api/topics":
            return app.topics.create(values)
        if path == '/api/illustrations/settings':
            from .image_provider import save
            return save(app.config, values)
        if path == '/api/illustrations/aiclaw-preset':
            from .illustrations import aiclaw_preset
            return aiclaw_preset(app)
        if path == '/api/illustrations/models':
            from .image_provider import configuration
            from .models import available
            if values.get('connection') == 'independent':
                with app.config.lock:
                    settings = {key: app.config.get(key) for key in ('IMAGE_BASE_URL', 'IMAGE_API_KEY')}
            else:
                settings = configuration(app.config)
            class ImageConfig:
                def get(self, key, default=''):
                    return settings.get({'AI_BASE_URL':'IMAGE_BASE_URL','AI_API_KEY':'IMAGE_API_KEY'}.get(key,key),default)
            return available(ImageConfig(), values)
        if path == '/api/illustrations':
            return app.illustrations.create(values)
        if path == '/api/performance/reviews':
            return app.performance.create(values)
        match = re.fullmatch(r'/api/performance/jobs/([a-f0-9]{32})', path)
        if match:
            return app.performance.add_sample(match[1], values)
        match = re.fullmatch(r'/api/performance/reviews/([a-f0-9]{32})/(retry|propose)', path)
        if match:
            return getattr(app.performance, match[2])(match[1], values)
        if path == '/api/reviews':
            return app.reviews.create(values)
        match = re.fullmatch(r'/api/reviews/([a-f0-9]{32})/(retry|apply)', path)
        if match:
            return getattr(app.reviews, match[2])(match[1], values)
        match = re.fullmatch(r'/api/posts/([a-f0-9]{32})/format/(repair|undo)', path)
        if match:
            return getattr(app.reviews, 'format' if match[2] == 'repair' else 'undo')(match[1], values)
        match = re.fullmatch(r'/api/illustrations/([a-f0-9]{32})/(edit|improve|generate|adopt|archive|import_original|preview)', path)
        if match:
            return getattr(app.illustrations, match[2])(match[1], values)
        if path == "/api/repurpose":
            return app.repurpose.create(values)
        delivery_match = re.fullmatch(r"/api/repurpose/([a-f0-9]{32})/deliver", path)
        if delivery_match:
            from .distribution import dispatch
            return dispatch(app, delivery_match[1]) or {"error": "请先审阅并采用平台稿"}
        if path == "/api/repurpose/continue":
            return app.repurpose.continue_platforms(values)
        match = re.fullmatch(r"/api/repurpose/([a-f0-9]{32})/retry-images", path)
        if match:
            task=app.repurpose.get(match[1])
            if task['status']!='review' or values.get('version')!=task['version']:
                raise Problem('平台稿已变化，请重新打开后重试',409)
            app.deliverables.enable(match[1],retry=True)
            return app.repurpose.get(match[1])
        match = re.fullmatch(r"/api/repurpose/([a-f0-9]{32})/(edit|apply|retry)", path)
        if match:
            return app.repurpose.retry(match[1]) if match[2] == "retry" else getattr(app.repurpose, match[2])(match[1], values)
        match = re.fullmatch(r"/api/topics/([a-f0-9]{32})/(retry|select)", path)
        if match:
            return app.topics.select(match[1], values) if match[2] == "select" else app.topics.retry(match[1])
        if path == "/api/workflows":
            return app.workflows.create(values)
        match = re.fullmatch(r"/api/workflows/([a-f0-9]{32})/(cancel|retry|apply|outline_edit|confirm|result_edit)", path)
        if match:
            if match[2] in ("apply", "outline_edit", "confirm", "result_edit"):
                return getattr(app.workflows, match[2])(match[1], values)
            return getattr(app.workflows, match[2])(match[1])
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/draft-recheck", path)
        if match:
            from .draft_reconciliation import reconcile
            return reconcile(app, match[1], values)
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/publication-recheck", path)
        if match:
            return app.publication_sync.request(match[1])
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/recheck", path)
        if match:
            return app.recheck_wechat(match[1])
        if path == "/api/backup":
            from .migration import backup_bundle
            archive = backup_bundle(app)
            self.send(archive, content_type="application/zip", download="paperplane-backup.zip")
            return None
        if path == "/api/legacy/import":
            from .migration import import_legacy
            return import_legacy(app, ROOT)
        if path == "/api/ai/test":
            return {"body": app.generate_draft({"title": "连接测试", "summary": "", "body": ""}, "只回复：连接成功")}
        if path == "/api/ai/models":
            return app.available_models(values)
        if path == "/api/ai/draft":
            raise Problem("请使用创作任务：生成提纲并确认后，再生成正文", 409)
        flow_match = re.fullmatch(r"/api/posts/([a-f0-9]{32})/wechat-draft(?:/(cancel))?", path)
        if flow_match:
            return app.article_flow.cancel(flow_match[1], values) if flow_match[2] else app.article_flow.request(flow_match[1], values)
        preparation=re.fullmatch(r'/api/posts/([a-f0-9]{32})/preparation',path)
        if preparation:return app.preparation.request(preparation[1],values)
        preparation=re.fullmatch(r'/api/preparations/([a-f0-9]{32})/confirm',path)
        if preparation:return app.preparation.confirm(preparation[1],values)
        match = re.fullmatch(r"/api/posts/([a-f0-9]{32})/register-publication", path)
        if match:
            from .publication_progress import register
            return register(app, match[1], values)
        match = re.fullmatch(r"/api/posts/([a-f0-9]{32})(/check|/publish|/archive)?", path)
        if match:
            if match[2] == "/check":
                channels = values.get("channels", [])
                if not isinstance(channels, list) or len(channels) > len(app.channels()) or any(not isinstance(c, str) for c in channels):
                    raise Problem("渠道格式不正确")
                post = app.post(match[1])
                repair = app.reviews.format(match[1], {'post_version': values.get('version', post['version'])})
                return {**app.preflight(repair['post'], channels), 'post': repair['post'], 'repaired': repair['repaired']}
            if match[2] == "/publish":
                return app.enqueue(match[1], values)
            if match[2] == "/archive":
                return app.archive(match[1], values.get("archived", True))
            return app.save_post(values, match[1])
        if path == "/api/jobs/batch":
            return app.jobs_batch(values)
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/(cancel|retry|confirm|link|not_received|metrics|sync|delete|restore)", path)
        if match:
            return app.sync_metrics(match[1]) if match[2] == "sync" else app.job_action(match[1], match[2], values)
        raise Problem("接口不存在", 404)
