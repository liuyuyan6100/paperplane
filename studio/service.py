import base64
import json
import re
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import adapters, local_ai, assets, xiaohongshu, network, wechat, profiles
from .config import Config
from .domain.tasks import ExecutionLease
from . import platform_catalog
from .db import Database, decode_post, dumps, event, now, uid
from .markdown import render, safe_url


class Problem(Exception):
    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.status, self.details = status, details


def text_value(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise Problem(f"{label}格式不正确或超过 {limit} 字")
    if required and not value.strip():
        raise Problem(f"请填写{label}")
    return value.strip() if label != "正文" else value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (AttributeError, ValueError, TypeError):
        raise Problem("请选择有效的排期时间（包含时区）") from None


def public_article_url(job):
    """Reuse recognizable article URLs only; a draft/admin URL is not a receipt."""
    url = safe_url(job["url"])
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        host, path = parts.hostname, parts.path
    except ValueError:
        return ""
    patterns = {
        "toutiao": ("www.toutiao.com", r"/article/[1-9]\d*/?"),
        "bilibili-article": ("www.bilibili.com", r"/read/cv[1-9]\d*/?"),
        "jianshu": ("www.jianshu.com", r"/p/[a-zA-Z0-9]+/?"),
        "weibo": ("weibo.com", r"/ttarticle/p/show/?"),
        "51cto": ("blog.51cto.com", r"/(?!blogger/)[a-zA-Z0-9_-]+/[1-9]\d*/?"),
        "segmentfault": ("segmentfault.com", r"/a/[1-9]\d*/?"),
        "oschina": ("my.oschina.net", r"/(?:u/[1-9]\d*|[a-zA-Z0-9_-]+)/blog/[1-9]\d*/?"),
        "cnblogs": ("www.cnblogs.com", r"/[^/]+/(?:p|articles)/\d+(?:\.html)?/?"),
        "zhihu": ("zhuanlan.zhihu.com", r"/p/\d+/?"),
        "juejin": ("juejin.cn", r"/(?:post|spost)/\d+/?"),
        "csdn": ("blog.csdn.net", r"/[^/]+/article/details/\d+/?"),
        "devto": ("dev.to", r"/[^/]+/[^/]+/?"),
        "xiaohongshu": ("www.xiaohongshu.com", r"/(?:explore|discovery/item)/[a-zA-Z0-9]+/?"),
        "wechat": ("mp.weixin.qq.com", r"/s(?:/[^/]+)?/?"),
    }
    expected = patterns.get(job["channel_id"])
    if not expected or host != expected[0] or not re.fullmatch(expected[1], path):
        return ""
    if job["channel_id"] == "weibo" and not re.search(r"(?:^|&)id=[1-9]\d*(?:&|$)", parts.query):
        return ""
    if job["channel_id"] == "devto" and ("temp-slug" in path or path.startswith(("/dashboard/", "/new/", "/settings/"))):
        return ""
    if job["channel_id"] == "wechat" and path.rstrip("/") == "/s" and not parts.query:
        return ""
    return url


class Studio:
    def __init__(self, data_dir):
        self.db = Database(data_dir)
        from . import videos
        videos.init(self)
        self.config = Config(self.db.data_dir)
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.worker = None
        self.last_tick = None
        self.worker_error = ""
        self.ai_lock = threading.Lock()
        self.channel_check_lock = threading.Lock()
        assets.index_existing(self)
        from .workflows import WorkflowManager
        self.workflows = WorkflowManager(self)
        from .topics import TopicManager
        self.topics = TopicManager(self)
        from .repurpose import RepurposeManager
        self.repurpose = RepurposeManager(self)
        from .deliverables import DeliverableManager
        self.deliverables = DeliverableManager(self)
        from .illustrations import IllustrationManager
        self.illustrations = IllustrationManager(self)
        from .reviews import ReviewManager
        self.reviews = ReviewManager(self)
        from .performance import PerformanceManager
        self.performance = PerformanceManager(self)
        from .chat import ChatManager
        self.chat = ChatManager(self)
        from .profile_learning import ProfileLearning
        self.learning = ProfileLearning(self)
        from .editor_assistant import EditorAssistant
        self.editor_assistant = EditorAssistant(self)
        from .profile_sync import ProfileSync
        self.profile_sync = ProfileSync(self)
        from .publishing_bridge import PublishingBridge
        self.publishing_bridge = PublishingBridge(self)
        from .xhs_drafts import XhsDrafts
        self.xhs_drafts = XhsDrafts(self)
        from .publication_sync import PublicationSync
        self.publication_sync = PublicationSync(self)
        from .article_flow import ArticleFlow
        self.article_flow = ArticleFlow(self)
        from .draft_preparation import DraftPreparation
        self.preparation = DraftPreparation(self)

        # 初始化技能系统（可选，通过环境变量控制）
        self.skill_system = None
        try:
            import os
            if os.environ.get('PAPERPLANE_USE_SKILLS', '1').lower() in ('1', 'true', 'yes'):
                from .skills import SkillSystem
                self.skill_system = SkillSystem(self)
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"技能系统已启用: {len(self.skill_system._registry)} 个技能")
        except Exception as exc:
            # 技能系统初始化失败不影响主应用
            import logging
            logging.getLogger(__name__).warning(f"技能系统初始化失败，将使用旧实现: {exc}")

    def post(self, post_id):
        row = self.db.one("SELECT * FROM posts WHERE id=?", (post_id,))
        if not row:
            raise Problem("文章不存在", 404)
        return decode_post(row)

    def save_post(self, values, post_id=None, *, connection=None):
        # A caller may include the same version/history write in its own transaction.
        with (nullcontext(connection) if connection is not None else self.db.connect(immediate=True)) as db:
            old = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone() if post_id else None
            if post_id and not old:
                raise Problem("文章不存在", 404)
            if post_id and db.execute("SELECT 1 FROM preview_article_trash WHERE post_id=?", (post_id,)).fetchone():
                raise Problem("文章在回收站中，请先恢复后再保存", 409)
            if old and values.get("version") != old["version"]:
                raise Problem("文章已在其他页面修改。请复制当前内容后重新打开，避免覆盖新版本。", 409)
            current = decode_post(old) if old else {"title": "", "body": "", "summary": "", "author": "", "cover": "", "tags": [], "source": "", "stage": "idea", "archived": 0}
            for field, label, limit in [("title", "标题", 150), ("body", "正文", 200000), ("summary", "摘要", 300), ("author", "作者", 60), ("cover", "封面", 2000), ("source", "来源", 2000)]:
                if field in values:
                    current[field] = text_value(values[field], label, limit, field == "title")
            if not current["title"]:
                raise Problem("请填写标题")
            tags = values.get("tags", current["tags"])
            if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(t, str) or not t.strip() or len(t) > 30 for t in tags):
                raise Problem("最多填写 12 个标签，每个标签不超过 30 字")
            current["tags"] = list(dict.fromkeys(t.strip() for t in tags))
            current["stage"] = values.get("stage", current["stage"])
            if current["stage"] not in ("idea", "draft", "ready", "published"):
                raise Problem("无效的内容阶段")
            if current["cover"] and not safe_url(current["cover"], True):
                raise Problem("封面需要有效的图片网址或已上传的本地图片")
            current.update(id=post_id or uid(), version=old["version"] + 1 if old else 1, created_at=old["created_at"] if old else now(), updated_at=now())
            if current["stage"] == "ready" and not current["body"].strip():
                raise Problem("正文为空，不能标记为待发布")
            current["xhs"] = xiaohongshu.validate(values.get("xhs", current.get("xhs", {})))
            current["profile_id"] = values.get("profile_id", current.get("profile_id"))
            if current["profile_id"] == "":
                current["profile_id"] = None
            if current["profile_id"] is not None:
                profiles.get(self, current["profile_id"], db)
            columns = ["id", "title", "body", "summary", "author", "tags", "cover", "source", "stage", "version", "created_at", "updated_at", "archived", "xhs"]
            columns.append("profile_id")
            # Lineage is immutable through the ordinary editor.
            for key, default in (("source_post_id", None), ("source_version", 0), ("target_channel", "")):
                current.setdefault(key, default)
                columns.append(key)
            vals = [dumps(current[k]) if k in ("tags", "xhs") else current[k] for k in columns]
            db.execute(f"INSERT INTO posts({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in columns if k not in ("id", "created_at")), vals)
            db.execute("INSERT INTO revisions(post_id,version,snapshot,created_at) VALUES(?,?,?,?)", (current["id"], current["version"], dumps(current), now()))
            if values.get('_edit_origin', 'author') == 'author':
                from .profile_learning import saved_edit
                saved_edit(db, decode_post(old) if old else None, current)
        self.wake.set()  # Recheck an explicitly requested draft after saved corrections.
        return current

    def archive(self, post_id, archived, *, connection=None):
        with (nullcontext(connection) if connection is not None else self.db.connect(immediate=True)) as db:
            if db.execute("SELECT 1 FROM preview_article_trash WHERE post_id=?", (post_id,)).fetchone():
                raise Problem("文章在回收站中，请先恢复", 409)
            if not db.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
                raise Problem("文章不存在", 404)
            if archived and db.execute("SELECT 1 FROM jobs WHERE post_id=? AND deleted_at IS NULL AND status IN ('queued','running','manual','draft','uncertain')", (post_id,)).fetchone():
                raise Problem("这篇文章还有进行中的发布，请先完成或取消任务")
            db.execute("UPDATE posts SET archived=?,version=version+1,updated_at=? WHERE id=?", (int(bool(archived)), now(), post_id))
            if archived:
                db.execute("UPDATE article_draft_intents SET status='canceled',updated_at=? WHERE post_id=? AND status IN ('waiting','checking')", (now(),post_id))
        return {"ok": True}

    def channels(self):
        rows = self.db.rows("SELECT * FROM channels ORDER BY rowid")
        rows = [r for r in rows if r['id'] not in platform_catalog.RETIRED_PLATFORMS]
        for row in rows:
            row["config"] = json.loads(row["config"])
            if row["id"] in platform_catalog.BRIDGE_PLATFORMS and self.publishing_bridge.status()["enabled"]:
                row["kind"] = "bridge"
            row["ready"] = row["kind"] != "devto" or bool(self.config.get("DEVTO_API_KEY"))
            if row['id'] == 'xiaohongshu' and self.xhs_drafts.status()['available']:
                row['kind'] = 'xhs-browser'
            if row["id"] == "wechat" and row["config"].get("mode") == "api":
                row["kind"] = "wechat"
                row["ready"] = bool(self.config.get("WECHAT_APP_ID") and self.config.get("WECHAT_APP_SECRET"))
        return platform_catalog.decorate(self, rows)

    def jobs(self):
        rows = self.db.rows("SELECT j.*,p.title,c.name channel_name,c.kind channel_kind FROM jobs j JOIN posts p ON p.id=j.post_id JOIN channels c ON c.id=j.channel_id ORDER BY j.created_at DESC,j.rowid DESC")
        self.publication_sync.decorate(rows)
        for row in rows:
            row.pop("run_token", None)
            snapshot = json.loads(row.pop("snapshot"))
            row["title"] = snapshot["title"]
            row["public_url"] = public_article_url(row)
            if row["channel_id"] == "wechat" and snapshot.get("delivery_config", {}).get("mode") == "api":
                row["channel_kind"] = "wechat"
            if snapshot.get("delivery_config", {}).get("mode") in ("bridge", "desktop-publishing"):
                row["channel_kind"] = "bridge"
            if snapshot.get('delivery_config', {}).get('mode') == 'xhs-browser':
                row['channel_kind'] = 'xhs-browser'
        return rows

    def state(self):
        from .publication_progress import annotate
        jobs = self.jobs()
        posts = annotate(self, [decode_post(p) for p in self.db.rows("SELECT * FROM posts ORDER BY updated_at DESC")], jobs)
        return {"profile_learning": self.learning.stats() if hasattr(self, "learning") else {}, "posts": posts, "jobs": jobs, "channels": self.channels(), "worker": {"alive": bool(self.worker and self.worker.is_alive()), "last_tick": self.last_tick, "error": self.worker_error}, "ai_ready": bool(self.config.get("AI_PROGRAM")) if self.config.get("AI_MODE", "api") == "program" else bool(self.config.get("AI_BASE_URL") and self.config.get("AI_MODEL"))}

    def preflight(self, post, channel_ids):
        checks = []

        def check(label, passed, detail, warning=False):
            checks.append({"label": label, "level": "pass" if passed else ("warn" if warning else "error"), "detail": "检查通过" if passed else detail})

        check("标题与正文", bool(post["title"].strip() and post["body"].strip()), "标题、正文均需填写")
        check("内容阶段", post["stage"] == "ready" and not post["archived"], "保存为「待发布」后才能进入分发")
        check("发布渠道", bool(channel_ids), "至少选择一个渠道")
        if post.get("target_channel"):
            check("平台稿渠道", channel_ids == [post["target_channel"]], "此稿仅适用于对应平台，请从母稿生成其他平台版本")
            source = self.db.one("SELECT version,archived FROM posts WHERE id=?", (post["source_post_id"],))
            check("母稿版本", bool(source and not source["archived"] and source["version"] == post["source_version"]),
                  "母稿已有修改或归档，此平台稿仍基于旧版本，请核对后继续", True)
        check("摘要", bool(post["summary"].strip()), "建议补充摘要，方便各平台展示", True)
        check("待核实内容", not re.search(r"待核实|TODO|待补充|待填写", post["body"], re.I), "正文中仍有占位内容，发布前请核实", True)
        check("YAML 元数据", not post["body"].lstrip().startswith("---"), "请把 YAML 元数据移到独立字段，避免覆盖渠道发布选项")
        images = re.findall(r"!\[[^\]]*\]\(([^\s)]+)\)", post["body"])
        if post.get("cover"):
            images.append(post["cover"])
        invalid = []
        local = []
        for src in images:
            if src.startswith("/media/"):
                local.append(src)
                if not safe_url(src, True) or not (self.db.data_dir / "media" / Path(src).name).is_file():
                    invalid.append(src)
            elif not safe_url(src, True):
                invalid.append(src)
        check("图片路径", not invalid, "图片需使用已上传的本地图片或 HTTP(S) 图片地址")
        channel_map = {c["id"]: c for c in self.channels()}
        for cid in channel_ids:
            channel = channel_map.get(cid)
            if not channel:
                check("未知渠道", False, "渠道不存在")
                continue
            check(channel["name"] + "连接", bool(channel["enabled"] and channel["ready"]), "请启用渠道并完成必要配置")
            identity = (post["id"], cid, post["version"])
            duplicate = self.db.one("SELECT id FROM jobs WHERE post_id=? AND channel_id=? AND version=? AND status!='canceled' UNION ALL SELECT job_id FROM delivery_tombstones WHERE post_id=? AND channel_id=? AND version=? AND status!='canceled' LIMIT 1", (*identity, *identity))
            check(channel["name"] + "重复检查", not duplicate, "同一稿件版本已有或已清理过发布记录，请先核对平台结果，避免重复发送")
            if cid == "devto":
                check("DEV 标签", len(post["tags"]) <= 4 and all(re.fullmatch(r"[A-Za-z0-9]{1,30}", t) for t in post["tags"]), "DEV 最多支持 4 个英文字母或数字标签")
                check("DEV 图片", not local or self.publishing_bridge.browser is not None, "DEV本地图片需要Windows安装版；送稿时登录与API Key相同的DEV账号，或使用公开图片网址")
            from .videos import MARKER
            if MARKER.search(post['body']) and cid not in ('wechat','local'):
                check('视频平台适配',False,'本篇含本地视频；当前仅本地预览与微信公众号接通，请先为该平台另行适配视频')
            if cid == "wechat":
                from .writing_rules import footer_satisfied
                profile = profiles.get(self, post['profile_id']) if post.get('profile_id') else None
                check('公众号文末署名入口', footer_satisfied(post['body'], profile), '正文末尾缺少此画像已设置的公众号署名或博客入口，请补齐后继续')
                check("公众号摘要", len(post["summary"]) <= 120, "公众号摘要请控制在 120 字以内")
                if channel["kind"] == "wechat":
                    try:
                        wechat.validate_post(post, self.db.data_dir)
                        check("公众号图文素材", True, "")
                    except adapters.DeliveryError as exc:
                        check("公众号图文素材", False, str(exc))
                    check("公众号草稿", True, "")
            if cid == "xiaohongshu":
                note = post.get("xhs", {})
                photos = note.get("photos", [])
                check("小红书图文", bool(note.get("title", "").strip() and note.get("caption", "").strip()), "请在编辑器的「小红书图文」中填写独立标题和文案")
                check("小红书图片", 1 <= len(photos) <= 18, "请选择 1–18 张图片，第一张为封面")
                check("小红书原图", all(re.fullmatch(r"/media/[a-f0-9]{32}\.(png|jpg|gif|webp)", p) and (self.db.data_dir / "media" / Path(p).name).is_file() for p in photos), "图片文件缺失，请恢复原图或重新选择")
                check("小红书标题", 1 <= len(note.get("title", "").strip()) <= 20, "小红书标题需为 1–20 字，请先修改平台标题；母稿标题保留")
                check("小红书文案建议", len(note.get("caption", "")) <= 1000, "建议文案控制在 1000 字以内，较长内容可转成图卡，最终以平台提示为准", True)
                check("小红书配图建议", len(photos) >= 3, "当前图片较少，可补充过程图片或正文图卡", True)
                if channel['kind'] == 'xhs-browser':
                    from .xhs_drafts import validate_note
                    try:
                        validate_note(post, self.db.data_dir)
                        check('小红书自动送草稿', True, '')
                    except adapters.DeliveryError as exc:
                        check('小红书自动送草稿', False, str(exc))
            if channel["kind"] == "manual":
                check(channel["name"] + "人工收尾", False, "排期到点后生成待办；在平台粘贴内容、发表，再回填链接", True)
                if local:
                    check("本地图片上传", False, "本地图片随发布包导出，需要在目标平台重新上传", True)
        return {"ok": not any(x["level"] == "error" for x in checks), "checks": checks}

    def enqueue(self, post_id, values):
        post = self.post(post_id)
        if values.get("version") != post["version"]:
            raise Problem("稿件已发生变化，请重新打开发布检查", 409)
        channels = values.get("channels", [])
        if not isinstance(channels, list) or len(channels) > len(platform_catalog.CATALOG) or any(not isinstance(x, str) for x in channels):
            raise Problem("渠道格式不正确")
        channels = list(dict.fromkeys(channels))
        post = self.reviews.format(post_id, {'post_version': post['version']})['post']
        report = self.preflight(post, channels)
        if not report["ok"]:
            raise Problem("发布检查未通过", 422, report)
        if any(c["level"] == "warn" for c in report["checks"]) and values.get("ack_warnings") is not True:
            raise Problem("请阅读并确认发布检查中的提醒", 422, report)
        due = timestamp(values["due_at"]) if values.get("due_at") else now()
        if values.get("due_at") and due <= now():
            raise Problem("定时时间必须晚于当前时间")
        run_id, ids = uid(), []
        try:
            with self.db.connect(immediate=True) as db:
                fresh = db.execute("SELECT version,archived FROM posts WHERE id=?", (post_id,)).fetchone()
                if fresh["version"] != post["version"] or fresh["archived"]:
                    raise Problem("稿件已发生变化，请重新检查", 409)
                if values.get("_article_flow"):
                    existing = db.execute("SELECT id FROM jobs WHERE post_id=? AND channel_id='wechat' AND status!='canceled' UNION ALL SELECT job_id FROM delivery_tombstones WHERE post_id=? AND channel_id='wechat' AND status!='canceled' LIMIT 1", (post_id,post_id)).fetchone()
                    if existing:
                        raise Problem("此文章已有公众号送稿记录，请核对原草稿，未重复新建",409)
                for cid in channels:
                    if db.execute("SELECT 1 FROM delivery_tombstones WHERE post_id=? AND channel_id=? AND version=? AND status!='canceled'", (post_id, cid, post["version"])).fetchone():
                        raise Problem("该版本的分发记录已清理，仍保留防重复标记，请先核对平台结果", 409)
                    channel = db.execute("SELECT * FROM channels WHERE id=? AND enabled=1", (cid,)).fetchone()
                    if not channel:
                        raise Problem("渠道设置已变化，请重新检查", 409)
                    job_id = uid()
                    # Snapshot channel configuration as well as content.
                    from .videos import listing
                    snapshot = {**post, "_videos": listing(self,post['id'],db), "delivery_config": json.loads(channel["config"])}
                    if cid in platform_catalog.BRIDGE_PLATFORMS and self.publishing_bridge.status()["enabled"]:
                        snapshot["delivery_config"]["mode"] = "desktop-publishing" if self.publishing_bridge.browser else "bridge"
                        account = self.publishing_bridge.status()["accounts"].get(cid)
                        if account: snapshot["delivery_config"]["account_id"] = account["id"]
                    if cid == 'xiaohongshu' and self.xhs_drafts.status()['available']:
                        snapshot['delivery_config'].update(mode='xhs-browser', publish=False)
                        if self.xhs_drafts.account:
                            snapshot['delivery_config']['account_id'] = self.xhs_drafts.account['id']
                    if values.get('publish_public') is True and cid=='devto':
                        snapshot['delivery_config']['publish']=True
                    db.execute("INSERT INTO jobs(id,run_id,post_id,channel_id,version,snapshot,status,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)", (job_id, run_id, post_id, cid, post["version"], dumps(snapshot), due, now(), now()))
                    event(db, job_id, "queued", "已保存稿件快照，等待执行" if due <= now() else "已排期，到点后由本机服务执行")
                    ids.append(job_id)
        except sqlite3.IntegrityError:
            raise Problem("该版本已有发布任务，请查看发布队列", 409) from None
        self.wake.set()
        return {"run_id": run_id, "ids": ids}

    def job_detail(self, job_id):
        job = self.db.one("SELECT j.*,c.name channel_name,c.kind channel_kind FROM jobs j JOIN channels c ON c.id=j.channel_id WHERE j.id=?", (job_id,))
        if not job:
            raise Problem("任务不存在", 404)
        job.pop("run_token", None)
        job["snapshot"] = json.loads(job["snapshot"])
        if job["channel_id"] == "wechat" and job["snapshot"].get("delivery_config", {}).get("mode") == "api":
            job["channel_kind"] = "wechat"
        if job["snapshot"].get("delivery_config", {}).get("mode") in ("bridge", "desktop-publishing"):
            job["channel_kind"] = "bridge"
        if job['snapshot'].get('delivery_config', {}).get('mode') == 'xhs-browser':
            job['channel_kind'] = 'xhs-browser'
        if job['channel_id'] == 'xiaohongshu':
            job['xhs_browser'] = self.xhs_drafts.status()
        job["events"] = self.db.rows("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,))
        job["public_url"] = public_article_url(job)
        return self.publication_sync.decorate([job])[0]

    @staticmethod
    def _change_job_recycle_state(db, job, action):
        state, job_id = job["status"], job["id"]
        if action == "delete":
            deleted_at = now()
            new = "canceled" if state == "queued" else state
            db.execute("UPDATE jobs SET deleted_at=?,status=?,updated_at=? WHERE id=?", (deleted_at, new, deleted_at, job_id))
            message = "已移入回收站，同时取消尚未执行的排期" if state == "queued" else "已移入回收站，保留发布回执与历史数据"
        else:
            # Even inconsistent imported records must never resume sending on restore.
            new = "canceled" if state == "queued" else "uncertain" if state == "running" else state
            db.execute("UPDATE jobs SET deleted_at=NULL,status=?,updated_at=? WHERE id=?", (new, now(), job_id))
            message = "已从回收站恢复；已取消的任务不会自动执行" if new == "canceled" else "已从回收站恢复，未重新发送内容"
        event(db, job_id, new, message)

    def jobs_batch(self, values):
        action, ids = values.get("action"), values.get("ids")
        if action not in ("delete", "restore", "purge"):
            raise Problem("请选择有效的批量操作")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 1000
                or any(not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id) for job_id in ids)
                or len(set(ids)) != len(ids)):
            raise Problem("请选择 1–1000 条不同的有效发布记录")
        if action == "purge" and values.get("confirmed") is not True:
            raise Problem("请确认彻底清理所选记录；清理后不能从回收站恢复")
        with self.db.connect(immediate=True) as db:
            # Validate the complete selection before touching any row.
            jobs = [db.execute("SELECT id,post_id,channel_id,version,legacy_key,status,deleted_at FROM jobs WHERE id=?", (job_id,)).fetchone() for job_id in ids]
            if any(job is None for job in jobs):
                raise Problem("所选记录已变化或不存在，请刷新后重新选择；本次未作更改", 404)
            for job in jobs:
                if job["status"] == "running":
                    raise Problem("所选记录中有正在执行的任务，请等待执行结果；本次未作更改", 409)
                if action == "delete" and job["deleted_at"] is not None:
                    raise Problem("所选记录已进入回收站，请刷新后重新选择；本次未作更改", 409)
                if action != "delete" and job["deleted_at"] is None:
                    raise Problem("只能恢复或彻底清理回收站内的记录；本次未作更改", 409)
            for job in jobs:
                if action != "purge":
                    self._change_job_recycle_state(db, job, action)
                    continue
                db.execute("INSERT INTO delivery_tombstones(job_id,post_id,channel_id,version,legacy_key,status,purged_at) VALUES(?,?,?,?,?,?,?)",
                           (job["id"], job["post_id"], job["channel_id"], job["version"], job["legacy_key"], job["status"], now()))
                db.execute("DELETE FROM events WHERE job_id=?", (job["id"],))
                db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
        return {"ok": True, "ids": ids, "count": len(ids), "action": action}

    def job_action(self, job_id, action, values):
        with self.db.connect(immediate=True) as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise Problem("任务不存在", 404)
            state = job["status"]
            if action == "delete":
                if state == "running":
                    raise Problem("任务正在执行，请等待执行结果后再删除", 409)
                if job["deleted_at"] is not None:
                    return {"ok": True}
                self._change_job_recycle_state(db, job, action)
                return {"ok": True}
            if action == "restore":
                if job["deleted_at"] is None:
                    return {"ok": True}
                self._change_job_recycle_state(db, job, action)
                return {"ok": True}
            if job["deleted_at"] is not None:
                raise Problem("任务已在回收站，请先恢复再操作", 409)
            note = text_value(values.get("note", ""), "备注", 2000)
            url = values.get("url", "")
            if action == "cancel":
                if state not in ("queued", "failed", "manual"):
                    raise Problem("当前状态不能取消；已发出请求的任务需要先核对平台结果", 409)
                new, message = "canceled", "已取消任务"
            elif action == "retry":
                if state != "failed":
                    raise Problem("只有明确失败的任务可以重试；结果待核对的任务请先核实", 409)
                new, message = "queued", "重新加入队列，仍使用原稿件快照"
            elif action == "confirm":
                if state not in ("manual", "draft", "uncertain"):
                    raise Problem("当前状态不能确认发布", 409)
                url = text_value(url, "公开链接", 4000)
                if url and not safe_url(url):
                    raise Problem("公开链接格式不正确，请填写 HTTP(S) 链接或留空")
                if url and job['channel_id'] in ('csdn', 'juejin', 'zhihu', 'cnblogs', 'oschina', 'segmentfault', '51cto', 'devto', 'xiaohongshu', 'wechat', 'toutiao', 'bilibili-article', 'jianshu', 'weibo') and not public_article_url({'channel_id': job['channel_id'], 'url': url}):
                    raise Problem("请填写该平台的公开文章链接，不能使用编辑页或草稿地址")
                url = url or public_article_url(job)
                new, message = "published", "已由用户确认已发布" + ("，已关联文章链接" if url else "，暂未记录公开链接")
                if job["url"] and job["url"] != url:
                    event(db, job_id, state, "确认发布前的平台记录：" + job["url"])
                db.execute("UPDATE jobs SET url=? WHERE id=?", (url, job_id))
            elif action == "link":
                if state != "published":
                    raise Problem("只有已发布记录可以补充公开链接", 409)
                url = text_value(url, "公开链接", 4000, required=True)
                if not public_article_url({"channel_id": job["channel_id"], "url": url}):
                    raise Problem("请填写该平台的公开文章链接，不能使用编辑页或草稿地址")
                if job["url"] != url:
                    event(db, job_id, state, "用户更新公开链接；原地址：" + (job["url"] or "未记录"))
                    db.execute("UPDATE jobs SET url=?,updated_at=? WHERE id=?", (url, now(), job_id))
                return {"ok": True}
            elif action == "not_received":
                if state != "uncertain" or values.get("verified") is not True:
                    raise Problem("请先在目标平台确认未收到这篇文章", 409)
                new, message = "failed", "用户已在平台核实未接收，可手动重试"
            elif action == "metrics":
                if state != "published":
                    raise Problem("只有已发布文章可以录入效果数据")
                metrics = [values.get(k, 0) for k in ("reads", "likes", "comments")]
                if any(type(n) is not int or not 0 <= n <= 1_000_000_000 for n in metrics):
                    raise Problem("数据必须是 0 到 10 亿之间的整数")
                from .performance import save_sample
                save_sample(db, job, {**values, 'source_note': '手动录入（发布记录详情）'})
                db.execute("UPDATE jobs SET reads=?,likes=?,comments=?,metrics_at=?,note=?,updated_at=? WHERE id=?", (*metrics, now(), note, now(), job_id))
                event(db, job_id, state, "效果数据已更新")
                return {"ok": True}
            else:
                raise Problem("未知任务操作")
            db.execute("UPDATE jobs SET status=?,run_token='',updated_at=?,error='',note=?,url=CASE WHEN ?!='' THEN ? ELSE url END,due_at=CASE WHEN ?='queued' THEN ? ELSE due_at END WHERE id=?", (new, now(), note or message, url, url, new, now(), job_id))
            event(db, job_id, new, message)
        self.wake.set()
        return {"ok": True}

    def recover(self):
        with self.db.connect(immediate=True) as db:
            for job in db.execute("SELECT * FROM jobs WHERE status='running' AND deleted_at IS NULL").fetchall():
                status = "queued" if job["channel_id"] == "local" else "uncertain"
                message = "服务重启，恢复本地发布" if status == "queued" else "服务在执行时中断，请先核对平台是否收到内容"
                db.execute("UPDATE jobs SET status=?,run_token='',error=?,updated_at=? WHERE id=?", (status, message, now(), job["id"]))
                event(db, job["id"], status, message)

    def tick(self):
        if self.stop.is_set():
            return False
        self.last_tick = now()
        with self.db.connect(immediate=True) as db:
            job = db.execute("SELECT j.*,c.kind,c.enabled FROM jobs j JOIN channels c ON c.id=j.channel_id WHERE j.status='queued' AND j.deleted_at IS NULL AND j.due_at<=? ORDER BY j.due_at,j.rowid LIMIT 1", (now(),)).fetchone()
            if not job:
                return False
            job = dict(job)
            lease = ExecutionLease.next(job["attempts"])
            db.execute("UPDATE jobs SET status='running',run_token=?,attempts=attempts+1,updated_at=? WHERE id=?", (lease.token, now(), job["id"]))
            event(db, job["id"], "running", "开始执行渠道分发")
        snapshot = json.loads(job["snapshot"])
        delivery_kind = "wechat" if job["channel_id"] == "wechat" and snapshot.get("delivery_config", {}).get("mode") == "api" else job["kind"]
        if snapshot.get("delivery_config", {}).get("mode") in ("bridge", "desktop-publishing"):
            delivery_kind = "bridge"
        if snapshot.get('delivery_config', {}).get('mode') == 'xhs-browser':
            delivery_kind = 'xhs-browser'
        fingerprint = platform_catalog.credential_fingerprint(self, job["channel_id"])
        try:
            if self.stop.is_set():
                return True
            if not job["enabled"]:
                raise adapters.DeliveryError("渠道已停用，请重新启用后重试")
            if job["kind"] == "local":
                with self.db.connect(immediate=True) as db:
                    if not lease.owns(db.execute("SELECT status,run_token FROM jobs WHERE id=?",(job["id"],)).fetchone()):
                        return True
                    db.execute("INSERT OR IGNORE INTO publications(job_id,post_id,snapshot,published_at) VALUES(?,?,?,?)", (job["id"], job["post_id"], job["snapshot"], now()))
                    db.execute("UPDATE jobs SET status='published',url=?,error='',note='已发布到本地专栏',updated_at=? WHERE id=?", ("/read/" + job["id"], now(), job["id"]))
                    event(db, job["id"], "published", "已发布到本地专栏，可查看完整文章")
                return True
            elif delivery_kind == "wechat":
                result = wechat.send_draft(snapshot, snapshot.get("delivery_config", {}), self.config, self.db.data_dir)
            elif delivery_kind == "bridge":
                if snapshot.get("delivery_config", {}).get("mode") == "desktop-publishing":
                    result = self.publishing_bridge.send_desktop(snapshot, job["channel_id"], job["id"])
                else:
                    result = self.publishing_bridge.send(snapshot, job["channel_id"])
            elif delivery_kind == 'xhs-browser':
                result = self.xhs_drafts.send(snapshot, job['id'])
            elif job["kind"] == "manual":
                result = {"status": "manual", "note": "发布包已就绪，请打开平台发表，再回填链接"}
            elif job["kind"] == "devto":
                from .devto_images import prepare
                prepared = prepare(self, snapshot)
                result = adapters.devto_send(prepared, snapshot.get("delivery_config", {}), self.config)
            else:
                raise adapters.DeliveryError("不支持的渠道类型")
            with self.db.connect(immediate=True) as db:
                current = db.execute("SELECT status,run_token FROM jobs WHERE id=?",(job["id"],)).fetchone()
                if not lease.owns(current):
                    from .infrastructure.delivery_execution import retain_late_result
                    retain_late_result(db,job["id"],lease,result)
                    return True
                if delivery_kind == 'bridge' and result.get('desktop_receipt'):
                    snapshot['desktop_receipt'] = result['desktop_receipt']
                    db.execute('UPDATE jobs SET snapshot=? WHERE id=?', (dumps(snapshot), job['id']))
                if delivery_kind == 'xhs-browser' and result.get('xhs_receipt'):
                    snapshot['xhs_receipt'] = result['xhs_receipt']
                    db.execute('UPDATE jobs SET snapshot=? WHERE id=?', (dumps(snapshot), job['id']))
                if delivery_kind == "wechat" and result.get("verification"):
                    snapshot["wechat_receipt"] = result["verification"]
                    db.execute("UPDATE jobs SET snapshot=? WHERE id=?", (dumps(snapshot), job["id"]))
                db.execute("UPDATE jobs SET status=?,url=?,external_id=?,note=?,error='',updated_at=? WHERE id=?", (result["status"], result.get("url", ""), result.get("external_id", ""), result["note"], now(), job["id"]))
                event(db, job["id"], result["status"], result["note"])
                if delivery_kind in ("wechat", "bridge", "devto", 'xhs-browser'):
                    platform_catalog.record_success(self, db, job["channel_id"], result, result.get('fingerprint', fingerprint), job["id"])
        except Exception as exc:
            known = isinstance(exc, adapters.DeliveryError)
            uncertain = exc.uncertain if known else delivery_kind not in ("local", "manual")
            state = "uncertain" if uncertain else "failed"
            message = str(exc) if known else "执行异常，请检查本地服务并核对渠道结果"
            with self.db.connect(immediate=True) as db:
                if not lease.owns(db.execute("SELECT status,run_token FROM jobs WHERE id=?",(job["id"],)).fetchone()):
                    return True
                db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?", (state, message, now(), job["id"]))
                event(db, job["id"], state, message)
        return True

    def start_worker(self):
        from .model_provenance import interrupt
        interrupt(self)
        self.recover()
        self.workflows.start()
        self.chat.start()
        self.profile_sync.start()
        self.publishing_bridge.start()
        self.publication_sync.start()

        def loop():
            while not self.stop.is_set():
                try:
                    self.article_flow.resume_due()
                    worked = self.tick()
                    self.worker_error = ""
                except Exception:
                    self.worker_error = "调度器遇到存储错误，正在等待恢复"
                    worked = False
                if not worked:
                    self.wake.wait(2)
                    self.wake.clear()
        self.worker = threading.Thread(target=loop, name="studio-scheduler", daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        with self.db.connect(immediate=True) as db:
            for job in db.execute("SELECT id FROM jobs WHERE status='running' AND channel_id!='local' AND deleted_at IS NULL").fetchall():
                message = '服务停止时任务尚未确认，请先核对原平台结果'
                db.execute("UPDATE jobs SET status='uncertain',run_token='',error=?,updated_at=? WHERE id=?",(message,now(),job['id']))
                event(db,job['id'],'uncertain','关闭时保留待核对状态，不自动重新发送')
        self.publication_sync.close()
        self.xhs_drafts.close()
        self.chat.stop()
        self.profile_sync.close()
        self.publishing_bridge.close()
        self.workflows.stop()
        self.learning.interrupt()
        self.editor_assistant.interrupt()
        self.preparation.interrupt()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=45)
        network.close_routes()

    def upload(self, values):
        fields = assets.metadata(values)
        try:
            raw = base64.b64decode(values.get("data", ""), validate=True)
        except (ValueError, TypeError):
            raise Problem("图片编码无效") from None
        if not raw or len(raw) > 5_000_000:
            raise Problem("图片大小需要在 5 MB 以内")
        ext = None
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = "png"
        elif raw.startswith(b"\xff\xd8\xff"):
            ext = "jpg"
        elif raw.startswith((b"GIF87a", b"GIF89a")):
            ext = "gif"
        elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            ext = "webp"
        if not ext:
            raise Problem("支持 PNG、JPG、GIF、WebP 图片")
        folder = self.db.data_dir / "media"
        folder.mkdir(exist_ok=True)
        name = uid() + "." + ext
        file = folder / name
        file.write_bytes(raw)
        try:
            with self.db.connect() as db:
                db.execute("INSERT INTO assets(id,url,title,collection,step,description,origin,created_at) VALUES(?,?,?,?,?,?,?,?)", (file.stem, "/media/" + name, fields["title"], fields["collection"], fields["step"], fields["description"], fields["origin"], now()))
        except Exception:
            file.unlink(missing_ok=True)
            raise
        return {"url": "/media/" + name, "id": file.stem}

    def generate_draft(self, post, instruction, structured=False):
        snapshot = (post["_profile_snapshot"] if "_profile_snapshot" in post else
                    profiles.snapshot(self, post["profile_id"], str(post) + instruction, post.get("target_channel", "")) if post.get("profile_id") else None)
        instruction = profiles.prompt(snapshot) + instruction
        if not self.ai_lock.acquire(blocking=False):
            raise Problem("已有模型请求正在执行，请等待完成", 409)
        try:
            from .model_routing import resolve
            configuration = resolve(self.config, post)
            from .model_provenance import tracked_call
            frozen = post.get('_model_route') or {}
            task = post.get('_model_task') or (post.get('_writing_phase') if post.get('_writing_phase') in ('outline', 'body') else 'body')
            route = {'task': task, 'model': configuration['AI_MODEL'], 'mode': configuration['AI_MODE'],
                     'owner_kind': frozen.get('owner_kind') or ('posts' if post.get('id') else 'utility'),
                     'owner_id': frozen.get('owner_id') or post.get('id') or uid()}
            if configuration['AI_MODE'] == "program":
                from .writing_rules import reliability_instruction
                output_rule = "只返回编辑要求指定的 JSON" if structured else "按本次编辑要求返回 Markdown 提纲或正文；要求仅生成提纲时，不写完整正文"
                prompt = (f"你是中文内容编辑，{output_rule}，不执行工具、不修改文件、不发布。"
                          + reliability_instruction(None if structured else post.get('_writing_phase') or post.get('_model_task')) + '\n'
                          +
                          f"标题：{post['title']}\n摘要：{post['summary']}\n素材：\n{post['body']}\n编辑要求：{instruction}")
                return tracked_call(self, route, lambda: local_ai.run(prompt, self.config, self.db.data_dir, stop_event=self.stop))
            for attempt in range(2):
                try:
                    if structured:
                        return tracked_call(self, route, lambda: adapters.ai_draft(post, instruction, configuration, structured=True))
                    return tracked_call(self, route, lambda: adapters.ai_draft(post, instruction, configuration))
                except adapters.DeliveryError as exc:
                    if attempt or not exc.retryable:
                        raise
                    if self.stop.wait(1):
                        raise adapters.DeliveryError('模型调用已停止，未继续自动重试') from None
        finally:
            self.ai_lock.release()

    def settings(self):
        return {**self.base_settings(), **{key.lower(): self.config.get(key, default) for key, default in local_ai.DEFAULTS.items()},
                **{key.lower(): self.config.get(key, default) for key, default in network.DEFAULTS.items()},
                "wechat_app_id": self.config.get("WECHAT_APP_ID"), "wechat_secret_set": bool(self.config.get("WECHAT_APP_SECRET")),
                "ai_programs": local_ai.discover(),
                "ai_program_allowed": __import__('os').environ.get("PAPERPLANE_ALLOW_PROGRAM") != "0"}

    def base_settings(self):
        return {"channels": self.channels(), "ai_base_url": self.config.get("AI_BASE_URL"), "ai_model": self.config.get("AI_MODEL"), "ai_key_set": bool(self.config.get("AI_API_KEY")), "devto_key_set": bool(self.config.get("DEVTO_API_KEY")), "data_dir": str(self.db.data_dir), "credential_protection": "Windows 当前用户加密" if __import__('os').name == 'nt' else "仅当前系统用户可读", "version": __import__("desktop_version").VERSION}

    def available_models(self, values):
        from .models import available
        try:
            with self.config.lock:
                config = {key: self.config.get(key) for key in ("AI_BASE_URL", "AI_API_KEY")}
            return available(config, values)
        except adapters.DeliveryError as exc:
            raise Problem(str(exc), 400) from None

    def save_settings(self, values):
        changes = {}
        for key in local_ai.DEFAULTS:
            if key in values:
                changes[key] = text_value(values[key], "本地模型配置", 16000)
        if changes:
            merged = {key: self.config.get(key, default) for key, default in local_ai.DEFAULTS.items()}
            merged.update(changes)
            try:
                local_ai.validate(merged)
            except adapters.DeliveryError as exc:
                raise Problem(str(exc)) from None
        for key in ("DEVTO_API_KEY", "AI_API_KEY", "AI_BASE_URL", "AI_MODEL", "WECHAT_APP_ID", "WECHAT_APP_SECRET", *network.DEFAULTS):
            if key in values:
                value = text_value(values[key], "配置", 2000)
                if (key.endswith("API_KEY") or key == "WECHAT_APP_SECRET") and not value:
                    continue  # Blank password fields preserve existing credentials.
                changes[key] = value
        if any(key in changes for key in network.DEFAULTS):
            merged_route = {key: self.config.get(key, default) for key, default in network.DEFAULTS.items()}
            merged_route.update(changes)
            try:
                network.validate_route(merged_route)
            except adapters.DeliveryError as exc:
                raise Problem(str(exc)) from None
        if "AI_BASE_URL" in changes and changes["AI_BASE_URL"]:
            from urllib.parse import urlsplit
            p = urlsplit(changes["AI_BASE_URL"])
            if not p.hostname or p.username or p.password or p.query or p.fragment or not (p.scheme == "https" or p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")):
                raise Problem("接口地址需要 HTTPS，本机模型可以使用 HTTP；不要附带查询参数")
        channels = values.get("channels", [])
        if not isinstance(channels, list):
            raise Problem("渠道设置格式不正确")
        for ch in channels:
            if not isinstance(ch, dict) or ch.get("id") not in {c["id"] for c in self.channels()} or type(ch.get("enabled")) is not bool or (ch["id"] == "devto" and type(ch.get("publish")) is not bool):
                raise Problem("渠道设置格式不正确")
            if ch["id"] == "wechat" and ch.get("mode", "manual") not in ("api", "manual"):
                raise Problem("公众号支持自动送入草稿箱或导出发布包")
        if changes:
            self.config.update(changes)
        with self.db.connect(immediate=True) as db:
            for ch in channels:
                row = db.execute("SELECT config FROM channels WHERE id=?", (ch["id"],)).fetchone()
                config = json.loads(row[0])
                if ch["id"] == "devto":
                    config["publish"] = bool(ch["publish"])
                if ch["id"] == "wechat":
                    config["mode"] = ch.get("mode", config.get("mode", "manual"))
                db.execute("UPDATE channels SET enabled=?,config=? WHERE id=?", (int(ch["enabled"]), dumps(config), ch["id"]))
        if any(key.startswith("WECHAT_") for key in changes) or any(ch["id"] == "wechat" for ch in channels):
            self.article_flow.settings_changed()
        return self.settings()

    def recheck_wechat(self, job_id):
        baseline = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        job = self.job_detail(job_id)
        if job["deleted_at"] or job["channel_kind"] != "wechat" or job["status"] not in ("draft", "uncertain") or not job["external_id"]:
            raise Problem("只能核对已有微信草稿编号且未回收的记录", 409)
        result = wechat.verify_draft(job["external_id"], job["snapshot"], self.config, self.db.data_dir,
                                     expected_receipt=job["snapshot"].get("wechat_receipt"))
        with self.db.connect(immediate=True) as db:
            fresh = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            keys = ('status','external_id','attempts','run_token','updated_at','deleted_at')
            if not fresh or any(fresh[key] != baseline[key] for key in keys) or fresh['snapshot'] != baseline['snapshot']:
                raise Problem("任务状态已变化，未覆盖最新结果", 409)
            db.execute("UPDATE jobs SET status=?,note=?,error='',updated_at=? WHERE id=?", (result["status"], result["note"], now(), job_id))
            event(db, job_id, result["status"], result["note"])
        return self.job_detail(job_id)

    def sync_metrics(self, job_id):
        job = self.job_detail(job_id)
        if job["deleted_at"] is not None:
            raise Problem("任务已在回收站，请先恢复再操作", 409)
        if job["channel_kind"] != "devto" or not job["external_id"] or job["status"] not in ("published", "draft"):
            raise Problem("仅支持同步已收到平台编号的 DEV 文章")
        try:
            data = adapters.devto_metrics(job["external_id"], self.config)
        except adapters.DeliveryError as exc:
            raise Problem(str(exc), 502) from None
        published = bool(data.get("published_at") or data.get("published_timestamp"))
        with self.db.connect(immediate=True) as db:
            fresh = db.execute("SELECT deleted_at FROM jobs WHERE id=?", (job_id,)).fetchone()
            if fresh is None:
                raise Problem("任务已被彻底清理，未写入同步结果", 404)
            if fresh["deleted_at"] is not None:
                raise Problem("任务已在回收站，请先恢复再操作", 409)
            state = "published" if published else "draft"
            metrics_saved = published and any(data.get(k) is not None for k in ('page_views_count', 'positive_reactions_count', 'comments_count'))
            if metrics_saved:
                db.execute("UPDATE jobs SET status=?,reads=?,likes=?,comments=?,metrics_at=?,url=?,updated_at=? WHERE id=?", (state, max(0, int(data.get("page_views_count") or 0)), max(0, int(data.get("positive_reactions_count") or 0)), max(0, int(data.get("comments_count") or 0)), now(), safe_url(data.get("url", "")), now(), job_id))
                from .performance import save_sample
                save_sample(db, {**job, 'status': state}, {
                    'reads': data.get('page_views_count'), 'likes': data.get('positive_reactions_count'),
                    'comments': data.get('comments_count'), 'source_note': 'DEV 官方文章详情接口',
                    'note': ''}, source='devto')
            else:
                db.execute('UPDATE jobs SET status=?,url=?,updated_at=? WHERE id=?', (state, safe_url(data.get('url', '')), now(), job_id))
            event(db, job_id, state, "已同步 DEV 状态与效果数据" if metrics_saved else "已同步 DEV 状态，本次未取得公开文章效果数据")
        return {"ok": True, "metrics_saved": bool(metrics_saved)}
