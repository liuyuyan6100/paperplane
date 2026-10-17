#!/usr/bin/env python3
"""Windows desktop shell for the existing PaperPlane Studio web application."""

import argparse
import json
import os
import sys
import threading
import tkinter as tk
import traceback
import subprocess
from pathlib import Path
from tkinter import messagebox, simpledialog
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from datetime import datetime, timezone

from desktop_core import Settings, normalize_server_url, set_windows_autostart, user_data_dir
from run import InstanceLock
from studio.server import Server
from studio.service import Studio
from desktop_updates import Updater, UpdateError
from desktop_version import VERSION


APP_NAME = "纸飞机 · 工作台"
ROOT = Path(__file__).resolve().parent


def dialog(callback):
    root = tk.Tk()
    root.withdraw()
    try:
        return callback(root)
    finally:
        root.destroy()


def server_prompt(current="", first=False):
    def ask(root):
        title = "首次设置服务器" if first else "服务器连接"
        hint = ("本机可直接使用，点“取消”跳过服务器设置。\n\n"
                "服务器地址请填完整 HTTPS 首页网址，不要只填 IP。\n"
                "你的 VPS 纸飞机地址：https://studio.example.com/\n"
                "其他服务器示例：https://studio.example.com\n\n"
                "保存地址后，在“运行位置 → 服务器”连接，并完成服务器登录。\n"
                "账号画像可在“画像管理 → 云端同步”连接后双向同步。文章、素材各自保存。\n"
                "以后可在“设置 → 服务器连接”修改地址。")
        return simpledialog.askstring(title, hint, initialvalue=current, parent=root)
    return dialog(ask)


def alert(title, message, warning=False):
    def show(root):
        fn = messagebox.showwarning if warning else messagebox.showinfo
        return fn(title, message, parent=root)
    return dialog(show)


def remote_reachable(url):
    """An auth challenge is reachable; a transport failure is not."""
    opener = build_opener(ProxyHandler())
    try:
        with opener.open(Request(url + "/healthz", headers={"Accept": "application/json"}), timeout=5) as response:
            if response.status != 200:
                return False
            try:
                return json.load(response).get("ok") is True
            except (ValueError, AttributeError):
                return False
    except HTTPError as exc:
        return exc.code in (401, 403)
    except (URLError, OSError, TimeoutError):
        return False


class LocalService:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.lock = None
        self.app = None
        self.server = None
        self.thread = None
        self.configure = None
        self.library = None

    def start(self):
        self.lock = InstanceLock(self.data_dir)
        try:
            self.app = Studio(self.data_dir)
            if self.configure:
                self.configure(self.app)
            library_settings = self.data_dir / 'shared-library.json'
            if library_settings.is_file():
                from library.runtime import DesktopLibrary
                self.library = DesktopLibrary(json.loads(library_settings.read_text(encoding='utf-8'))['root'])
                self.library.start()
            self.server = Server(("127.0.0.1", 0), self.app)
            self.app.start_worker()
            self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
            self.thread.start()
        except Exception:
            self.stop()
            raise
        return self.url

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def active_tasks(self):
        if not self.app:
            return False
        row = self.app.db.one("SELECT (SELECT COUNT(*) FROM jobs WHERE status='running') + (SELECT COUNT(*) FROM workflows WHERE status='running') + (SELECT COUNT(*) FROM topic_plans WHERE status='running') + (SELECT COUNT(*) FROM repurpose_variants WHERE status IN ('queued','running')) + (SELECT COUNT(*) FROM illustration_plans WHERE status IN ('queued','running')) + (SELECT COUNT(*) FROM illustration_candidates WHERE status IN ('queued','running')) + (SELECT COUNT(*) FROM editorial_reviews WHERE status IN ('queued','running')) AS n")
        return bool(row and row["n"])

    def stop(self):
        if self.server:
            if self.thread:
                self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None
        if self.app:
            self.app.close()
            self.app = None
        if self.library:
            self.library.stop()
            self.library = None
        if self.lock:
            self.lock.close()
            self.lock = None


class DesktopClient:
    def __init__(self, data_dir, start_hidden=False):
        self.data_dir = Path(data_dir)
        self.settings = Settings(self.data_dir / "client-settings.json")
        self.local = LocalService(self.data_dir / "local")
        self.start_hidden = start_hidden
        self.window = None
        self.tray = None
        self.exiting = False
        self.monitor_stop = threading.Event()
        self.monitor = None
        self.task_monitor = None
        self.fell_back_from_server = False
        self.last_stash_path = None
        self.window_visible = not start_hidden
        self.close_lock = threading.Lock()
        self.close_thread = None
        self.updater = Updater(self.data_dir)
        self.updater.on_download = lambda: self.update_action("install")
        self.update_window = None

    def update_action(self, action, values=None):
        if action == "configure":
            self.updater.configure(values or {})
            return self.updater.start("check")
        if action == "open":
            self.show_updates()
            return self.updater.status()
        if action in {"check", "download"}:
            return self.updater.start(action)
        if action != "install":
            raise UpdateError("未知更新操作")
        if self.updater.status()["status"] in {"checking", "downloading", "installing"}:
            raise UpdateError("正在处理更新，请稍后再试")
        self.updater.installer()
        if not self.close_lock.acquire(blocking=False):
            raise UpdateError("正在处理窗口关闭，请稍后再试")

        def install():
            try:
                if self.local.active_tasks():
                    self.updater.set(status="downloaded", message="本机任务正在运行，请完成后再打开安装向导")
                    return
                if not self.check_unsaved():
                    self.updater.set(status="downloaded", message="请先保存当前稿件，再打开安装向导")
                    return
                if self.local.active_tasks():
                    self.updater.set(status="downloaded", message="本机任务已开始，请完成后再打开安装向导")
                    return
                package = self.updater.installer()
                if not getattr(sys, "frozen", False):
                    raise UpdateError("请在已安装的客户端中打开安装向导")
                subprocess.Popen([str(package), "/DIR=" + str(Path(sys.executable).parent), "/SP-", "/NORESTART"])
                self.exiting = True
                if self.update_window:
                    self.update_window.destroy()
                self.window.destroy()
            except UpdateError as exc:
                self.updater.set(status="error", message=str(exc))
            except Exception:
                self.updater.set(status="error", message="无法打开安装向导，请重新下载或稍后重试")
            finally:
                self.close_lock.release()

        self.updater.set(status="installing", message="正在检查稿件和任务，然后打开安装向导…")
        threading.Thread(target=install, name="paperplane-install", daemon=True).start()
        return self.updater.status()

    def show_updates(self):
        import webview
        if self.update_window:
            self.update_window.show()
            return
        self.update_window = webview.create_window(
            f"纸飞机 · 软件更新 · {VERSION}", self.local.url + "/#updates", width=620, height=680, min_size=(400, 500))
        self.update_window.events.closed += lambda: setattr(self, "update_window", None)
        self.updater.start("check")

    def first_run(self):
        if self.settings.values["first_server_prompt_shown"]:
            return
        answer = server_prompt(first=True)
        self.settings.update(first_server_prompt_shown=True)
        if answer and answer.strip():
            try:
                self.settings.update(server_url=normalize_server_url(answer))
                alert("服务器地址已保存", "当前仍默认使用本机。进入“运行位置 → 服务器”即可连接并登录服务器工作台。\n账号画像可在“画像管理 → 云端同步”配置；文章和素材各自保存。")
            except ValueError as exc:
                alert("服务器地址未保存", str(exc), warning=True)

    def current_url(self):
        values = self.settings.values
        return values["server_url"] if values["mode"] == "server" else self.local.url

    def check_unsaved(self):
        if not self.window:
            return True
        try:
            result = self.window.evaluate_js("window.PaperPlaneDesktop ? window.PaperPlaneDesktop.hasUnsaved() : (typeof dirty === 'boolean' ? dirty : null)")
        except Exception:
            # Unknown page state should not silently discard a draft.
            alert("无法确认编辑状态", "请先在工作台保存当前稿件，再切换连接。", warning=True)
            return False
        if result is None:
            alert("无法确认编辑状态", "当前服务器页面尚未提供客户端编辑状态接口。请先保存稿件，再切换连接。", warning=True)
            return False
        if result:
            alert("先保存稿件", "当前文章有未保存的修改。请先保存，再切换本机或服务器。", warning=True)
            return False
        return True

    def prepare_manual_switch(self):
        if not self.window:
            return True
        try:
            dirty = self.window.evaluate_js("window.PaperPlaneDesktop ? window.PaperPlaneDesktop.hasUnsaved() : (typeof dirty === 'boolean' ? dirty : null)")
        except Exception:
            dirty = None
        if dirty is None:
            alert("无法确认编辑状态", "请先保存当前稿件，再切换连接。", warning=True)
            return False
        if not dirty:
            return True

        def choose(root):
            return messagebox.askyesnocancel("切换运行位置", "当前稿件有未保存修改。点“是”保存到原所在端后切换；点“否”放弃修改后切换；点“取消”留在当前端。", parent=root)

        choice = dialog(choose)
        if choice is None:
            return False
        if choice is False:
            return True
        done = threading.Event()
        result = {}

        def on_saved(value):
            result["value"] = value
            done.set()

        try:
            self.window.evaluate_js("(async () => { try { await saveEditor(true); return {ok: !dirty}; } catch (error) { return {ok:false, error:String(error)}; } })()", callback=on_saved)
        except Exception:
            alert("保存失败", "请在当前页面手动保存后重试切换。", warning=True)
            return False
        if not done.wait(45) or not isinstance(result.get("value"), dict) or not result["value"].get("ok"):
            alert("保存未完成", "稿件仍留在原端。请确认页面显示“已保存”后再切换。", warning=True)
            return False
        return True

    def stash_unsaved(self):
        """Preserve unsaved remote text before an automatic local fallback."""
        self.last_stash_path = None
        try:
            state = self.window.evaluate_js("window.PaperPlaneDesktop ? {supported:true, dirty:window.PaperPlaneDesktop.hasUnsaved(), draft:window.PaperPlaneDesktop.snapshot()} : (typeof dirty === 'boolean' ? {supported:true, dirty:dirty, draft:dirty && typeof editorValues === 'function' && editor ? {...editorValues(), post_id:editor.id, saved_version:editor.version} : null} : {supported:false})")
        except Exception:
            return False
        if not isinstance(state, dict) or not state.get("supported"):
            return False
        if not state.get("dirty"):
            return True  # The editor reported a clean state.
        snapshot = state.get("draft")
        if not isinstance(snapshot, dict) or not snapshot.get("body") and not snapshot.get("title"):
            return False
        folder = self.data_dir / "draft-recovery"
        folder.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        target = folder / name
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"origin": self.settings.values["server_url"], "saved_at": datetime.now(timezone.utc).isoformat(), "draft": snapshot}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        self.last_stash_path = target
        return True

    def switch(self, mode, automatic=False):
        if mode not in ("local", "server"):
            raise ValueError(mode)
        if self.settings.values["mode"] == mode:
            return True
        if not automatic and not self.prepare_manual_switch():
            return False
        if automatic and self.window and not self.stash_unsaved():
            self.notice("服务器连接中断，但当前编辑内容无法安全暂存。请保留此窗口，先复制未保存文字。")
            return False
        if mode == "server":
            url = self.settings.values["server_url"]
            if not url:
                alert("尚未配置服务器", "请先在“设置 → 服务器连接”填写地址。", warning=True)
                return False
            if not remote_reachable(url):
                alert("服务器连接失败", "当前保持本机运行。请检查服务器地址和网络连接。", warning=True)
                return False
        self.settings.update(mode=mode)
        if self.window:
            self.window.load_url(self.current_url())
            self.window.set_title(APP_NAME + (" · 服务器" if mode == "server" else " · 本机"))
        if automatic:
            self.fell_back_from_server = True
            extra = "未保存正文已暂存于本机，请从“设置 → 暂存稿目录”查看。" if self.last_stash_path else ""
            self.notice("服务器连接中断，已自动切换为本机运行。两端数据各自保存。" + extra)
        elif mode == "server":
            self.fell_back_from_server = False
        return True

    def notice(self, message):
        if self.tray:
            try:
                self.tray.notify(message, APP_NAME)
            except Exception:
                pass
        alert(APP_NAME, message, warning=True)

    def configure_server(self):
        answer = server_prompt(self.settings.values["server_url"])
        if answer is None:
            return
        if not answer.strip():
            alert("地址未修改", "留空不会删除当前配置；需要切回本机可使用“运行位置 → 本机”。")
            return
        try:
            url = normalize_server_url(answer)
        except ValueError as exc:
            alert("地址无效", str(exc), warning=True)
            return
        if self.settings.values["mode"] == "server" and url != self.settings.values["server_url"]:
            if not self.prepare_manual_switch():
                return
            if not remote_reachable(url):
                alert("服务器连接失败", "新地址暂时无法连接，当前服务器地址没有修改。", warning=True)
                return
        self.settings.update(server_url=url)
        if self.settings.values["mode"] == "server":
            self.window.load_url(url)
        alert("服务器地址已保存", "可在“运行位置 → 服务器”连接；账号画像可在“画像管理 → 云端同步”配置；文章、素材和模型配置由两端各自保存。")

    def toggle_autostart(self):
        if not getattr(sys, "frozen", False):
            alert("请先安装客户端", "开机启动需要指向已安装的客户端程序。", warning=True)
            return
        enabled = not self.settings.values["autostart"]
        set_windows_autostart(enabled, sys.executable)
        self.settings.update(autostart=enabled)
        alert("开机启动", "已开启；下次开机只进入系统托盘。" if enabled else "已关闭。")

    def open_recovery_folder(self):
        folder = self.data_dir / "draft-recovery"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def show(self):
        self.window.show()
        self.window_visible = True

    def on_close(self):
        if self.exiting:
            if self.local.app:
                self.local.app.xhs_drafts.close()
            return True
        # WinForms invokes closing synchronously on the UI thread. Never wait
        # for JavaScript there: WebView2 needs that same thread to return it.
        self.start_close()
        return False

    def start_close(self, exit_only=False):
        if self.exiting or not self.close_lock.acquire(blocking=False):
            return False

        def resolve():
            try:
                if exit_only:
                    self.finish_exit()
                else:
                    self.resolve_close()
            finally:
                self.close_lock.release()

        self.close_thread = threading.Thread(target=resolve, name="paperplane-close", daemon=True)
        self.close_thread.start()
        return False

    def resolve_close(self):
        behavior = self.settings.values["close_behavior"]
        if behavior == "ask":
            def choose(root):
                return messagebox.askyesnocancel("关闭工作台", "点“是”缩到托盘继续运行；点“否”完全退出。选择会被记住。", parent=root)
            choice = dialog(choose)
            if choice is None:
                return False
            behavior = "tray" if choice else "exit"
            self.settings.update(close_behavior=behavior)
        if behavior == "tray":
            self.window.hide()
            self.window_visible = False
            return False
        return self.finish_exit()

    def request_exit(self, from_window=False):
        return self.start_close(exit_only=True)

    def finish_exit(self):
        if self.local.active_tasks():
            def choose(root):
                return messagebox.askyesno("本机任务正在运行", "完全退出会中断本机任务。是否缩到托盘，让任务继续运行？", parent=root)
            if dialog(choose):
                self.window.hide()
                self.window_visible = False
                return False
        if not self.check_unsaved():
            return False
        self.exiting = True
        if self.update_window:
            self.update_window.destroy()
        self.window.destroy()
        return True

    def monitor_connection(self):
        while not self.monitor_stop.wait(15):
            server_url = self.settings.values["server_url"]
            if not server_url:
                continue
            if self.settings.values["mode"] == "server":
                if not remote_reachable(server_url):
                    self.switch("local", automatic=True)
            elif self.fell_back_from_server and remote_reachable(server_url):
                self.fell_back_from_server = False
                if self.tray:
                    try:
                        self.tray.notify("服务器连接已恢复。当前仍在本机，需手动切回。", APP_NAME)
                    except Exception:
                        pass

    def monitor_tasks(self):
        job_id = self.local.app.db.one("SELECT COALESCE(MAX(id),0) AS n FROM events")["n"]
        workflow_id = self.local.app.db.one("SELECT COALESCE(MAX(id),0) AS n FROM workflow_events")["n"]
        variants = {r['id']: r['status'] for r in self.local.app.db.rows('SELECT id,status FROM repurpose_variants UNION ALL SELECT id,status FROM illustration_plans UNION ALL SELECT id,status FROM illustration_candidates UNION ALL SELECT id,status FROM editorial_reviews')}
        while not self.monitor_stop.wait(3):
            try:
                jobs = self.local.app.db.rows("SELECT id,status FROM events WHERE id>? ORDER BY id", (job_id,))
                workflows = self.local.app.db.rows("SELECT id,status FROM workflow_events WHERE id>? ORDER BY id", (workflow_id,))
                if jobs:
                    job_id = jobs[-1]["id"]
                if workflows:
                    workflow_id = workflows[-1]["id"]
                current_variants = {r['id']: r['status'] for r in self.local.app.db.rows('SELECT id,status FROM repurpose_variants UNION ALL SELECT id,status FROM illustration_plans UNION ALL SELECT id,status FROM illustration_candidates UNION ALL SELECT id,status FROM editorial_reviews')}
                variant_attention = any(status in ('failed', 'interrupted', 'review') and variants.get(key) != status
                                        for key, status in current_variants.items())
                variants = current_variants
                if not self.window_visible and self.tray:
                    if any(row["status"] in ("failed", "uncertain", "manual") for row in jobs):
                        self.tray.notify("发布任务需要处理，请打开工作台查看任务列表。", APP_NAME)
                    if any(row["status"] in ("failed", "interrupted", "review") for row in workflows):
                        self.tray.notify("创作任务需要处理，请打开工作台查看。", APP_NAME)
                    if variant_attention:
                        self.tray.notify("创作、配图或审稿需要审阅或处理，请打开工作台查看。", APP_NAME)
            except Exception:
                # Notifications must never stop the local worker.
                continue

    def run(self):
        import webview
        from webview.menu import MenuAction
        import pystray
        from PIL import Image

        self.first_run()
        from desktop_xhs import DesktopXhsBrowser
        from desktop_publishing import DesktopPublishingBrowser
        xhs_browser = DesktopXhsBrowser()
        publishing_browser = DesktopPublishingBrowser(self.data_dir / 'platform-accounts.json')
        def configure(app):
            app.xhs_drafts.browser = xhs_browser
            app.publishing_bridge.browser = publishing_browser
        self.local.configure = configure
        self.local.start()
        recovery_folder = self.data_dir / "draft-recovery"
        if recovery_folder.is_dir() and any(recovery_folder.glob("*.json")):
            alert("发现断线暂存稿", "本机保留了之前未保存的服务器稿件。可在“设置 → 暂存稿目录”查看；当前版本尚不自动回写原稿。", warning=True)
        if self.settings.values["mode"] == "server" and not remote_reachable(self.settings.values["server_url"]):
            self.settings.update(mode="local")
            alert("已切换到本机", "服务器暂时无法连接，工作台将在本机打开。", warning=True)

        if getattr(sys, "frozen", False):
            self.local.server.desktop_updater = self.updater
            self.local.server.desktop_update_action = self.update_action
            self.updater.start("check")

        icon_path = ROOT / "web" / "icon-192.png"
        self.window = webview.create_window(
            APP_NAME + (" · 服务器" if self.settings.values["mode"] == "server" else " · 本机"), self.current_url(), width=1280, height=850,
            min_size=(900, 600), hidden=self.start_hidden,
            menu=[
                webview.Menu("运行位置", [
                    MenuAction("本机", lambda: self.switch("local")),
                    MenuAction("服务器", lambda: self.switch("server")),
                ]),
                webview.Menu("设置", [
                    MenuAction("软件更新", self.show_updates),
                    MenuAction("服务器连接", self.configure_server),
                    MenuAction("暂存稿目录", self.open_recovery_folder),
                    MenuAction("切换开机启动", self.toggle_autostart),
                    MenuAction("关闭窗口行为重新选择", lambda: self.settings.update(close_behavior="ask")),
                ]),
            ],
        )
        self.window.events.closing += self.on_close
        self.window.events.shown += xhs_browser.ready.set
        self.window.events.shown += publishing_browser.ready.set
        self.tray = pystray.Icon(
            "paperplane", Image.open(icon_path), APP_NAME,
            pystray.Menu(
                pystray.MenuItem("打开工作台", lambda *_: self.show(), default=True),
                pystray.MenuItem("使用本机", lambda *_: self.switch("local")),
                pystray.MenuItem("连接服务器", lambda *_: self.switch("server")),
                pystray.MenuItem("服务器连接设置", lambda *_: self.configure_server()),
                pystray.MenuItem("退出", lambda *_: self.request_exit()),
            ),
        )
        tray_thread = threading.Thread(target=self.tray.run, name="paperplane-tray", daemon=True)
        tray_thread.start()
        self.monitor = threading.Thread(target=self.monitor_connection, name="paperplane-connection", daemon=True)
        self.monitor.start()
        self.task_monitor = threading.Thread(target=self.monitor_tasks, name="paperplane-task-notices", daemon=True)
        self.task_monitor.start()
        try:
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(self.data_dir / "browser"), icon=str(ROOT / "web" / "icon.ico"))
        finally:
            self.updater.stop.set()
            self.monitor_stop.set()
            if self.tray:
                self.tray.stop()
            self.local.stop()


def main():
    parser = argparse.ArgumentParser(description=APP_NAME + " Windows 客户端")
    parser.add_argument("--data-dir", type=Path, default=user_data_dir())
    parser.add_argument("--hidden", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="启动并检查本机服务，不打开图形界面")
    parser.add_argument('--library-service', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--library-root', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--library-port', type=int, default=8798, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.library_service:
        if not args.library_root:
            parser.error('library-root is required')
        from library.store import Library
        from library.server import Server as LibraryServer
        library = Library(args.library_root)
        server = LibraryServer(('127.0.0.1', args.library_port), library)
        (library.root / 'service.json').write_text(json.dumps({'pid':os.getpid(),'url':f'http://127.0.0.1:{server.server_port}','root':str(library.root)}),encoding='utf-8')
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return 0
    if args.smoke_test:
        service = LocalService(args.data_dir / "local")
        try:
            url = service.start()
            opener = build_opener(ProxyHandler({}))
            with opener.open(url + "/api/health", timeout=5) as response:
                data = json.load(response)
            with opener.open(url + "/", timeout=5) as response:
                page_ok = response.status == 200 and b"<html" in response.read(1000).lower()
            ok = data.get("app") == "paperplane-studio" and page_ok
            skill_names = []
            if os.environ.get('PAPERPLANE_USE_SKILLS', '1').lower() in ('1', 'true', 'yes'):
                from studio.skill_executors import get_executor
                system = service.app.skill_system
                required = {'generate-outline', 'review-draft', 'expand-section', 'extract-materials'}
                skill_names = sorted(s['name'] for s in system.list_skills()) if system else []
                ok = ok and required.issubset(skill_names)
                if ok:
                    for name, prompt in (('generate-outline', 'outline'), ('review-draft', 'review'),
                                         ('expand-section', 'main')):
                        system._load_skill(name)
                        get_executor(name)(service.app, system._loaded[name]).load_resource(prompt)
            receipt = {"ok": ok, "url": url, "skills": skill_names}
            (args.data_dir / 'smoke-result.json').write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
            print(json.dumps(receipt, ensure_ascii=False))
            return 0 if ok else 1
        finally:
            service.stop()
    try:
        DesktopClient(args.data_dir, args.hidden).run()
    except Exception as exc:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        (args.data_dir / "desktop-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        print(traceback.format_exc(), file=sys.stderr)
        alert("客户端启动失败", "请查看客户端数据目录中的 desktop-error.log。", warning=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
