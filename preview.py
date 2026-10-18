"""Independently identified M1 desktop preview and local development entrypoint."""

import argparse
import json
import os
import signal
import threading
import webbrowser
from pathlib import Path

from studio.api.preview import PREVIEW_VERSION
from studio.preview_runtime import PreviewService, preview_data_dir

TITLE = "纸飞机 · 架构验证版"


def desktop(service):
    from tkinter import Tk, messagebox

    import pystray
    import webview
    from PIL import Image

    from desktop_publishing import DesktopPublishingBrowser
    from desktop_xhs import DesktopXhsBrowser

    root = Path(__file__).resolve().parent
    publishing = DesktopPublishingBrowser(service.data_dir / "platform-accounts.json")
    xhs = DesktopXhsBrowser()

    def configure(app):
        app.publishing_bridge.browser = publishing
        app.xhs_drafts.browser = xhs

    service.configure = configure
    service.start()
    window = webview.create_window(TITLE, service.url, width=1440, height=940, min_size=(900, 650))
    window.events.shown += publishing.ready.set
    window.events.shown += xhs.ready.set
    exiting = threading.Event()
    closing = threading.Lock()

    def resolve_close():
        try:
            try:
                dirty = window.evaluate_js("window.PaperPlaneDesktop?.hasUnsaved?.() ?? null")
            except Exception:
                dirty = None
            if dirty is not False:
                tk = Tk()
                tk.withdraw()
                try:
                    messagebox.showinfo(TITLE, "请先保存当前修改，再关闭验证版。", parent=tk)
                finally:
                    tk.destroy()
                return
            tk = Tk()
            tk.withdraw()
            try:
                choice = messagebox.askyesnocancel(
                    TITLE,
                    "点“是”缩到托盘继续运行；点“否”完全退出。"
                    + (
                        "\n本机任务尚未完成，完全退出会中断任务。" if service.active_tasks() else ""
                    ),
                    parent=tk,
                )
            finally:
                tk.destroy()
            if choice is None:
                return
            if choice:
                window.hide()
            else:
                exiting.set()
                window.destroy()
        finally:
            closing.release()

    def close():
        if exiting.is_set():
            return True
        if closing.acquire(False):
            threading.Thread(target=resolve_close, daemon=True).start()
        return False

    window.events.closing += close
    tray = pystray.Icon(
        "paperplane-preview",
        Image.open(root / "web/icon-192.png"),
        TITLE,
        pystray.Menu(
            pystray.MenuItem("打开文章编辑验证版", lambda *_: window.show(), default=True),
            pystray.MenuItem("退出验证版", lambda *_: close()),
        ),
    )
    threading.Thread(target=tray.run, name="preview-tray", daemon=True).start()
    try:
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(service.data_dir / "browser"),
            icon=str(root / "web/icon.ico"),
        )
    finally:
        tray.stop()
        service.stop()


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--data-dir", type=Path, default=preview_data_dir())
    parser.add_argument("--serve", action="store_true", help="仅运行本机服务")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    mutex = None
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "PaperPlaneStudioPreview")
        # The data-directory lock governs instances. This mutex only lets the
        # preview installer detect any running preview executable.
    service = PreviewService(
        args.data_dir,
        reuse_settings=not (args.serve or args.smoke_test)
        and args.data_dir.resolve() == preview_data_dir().resolve(),
    )
    if args.serve or args.smoke_test:
        import httpx

        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            service.start()
            if args.smoke_test:
                with httpx.Client(trust_env=False) as client:
                    health = client.get(service.origin + "/preview-healthz").json()
                    page = client.get(service.url)
                assert health["ok"] and health["worker_alive"] and page.status_code == 200
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "version": PREVIEW_VERSION,
                            "frontend": page.status_code,
                        }
                    )
                )
            else:
                print(
                    json.dumps(
                        {
                            "url": service.url,
                            "version": PREVIEW_VERSION,
                            "data_dir": str(service.data_dir),
                        }
                    ),
                    flush=True,
                )
                if args.open:
                    webbrowser.open(service.url)
                stop.wait()
        finally:
            service.stop()
    else:
        desktop(service)
    if mutex:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
