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


def read_close_state(window):
    """Keep an unreadable bridge distinct from an actual unsaved draft."""
    try:
        value = window.evaluate_js(
            "(() => { const bridge = window.PaperPlaneDesktop; "
            "if (!bridge || typeof bridge.hasUnsaved !== 'function') return null; "
            "return {dirty: bridge.hasUnsaved(), "
            "areas: bridge.getUnsavedAreas?.() ?? []}; })()"
        )
    except Exception:
        return None
    if not isinstance(value, dict) or type(value.get("dirty")) is not bool:
        return None
    areas = value.get("areas")
    return {
        "dirty": value["dirty"],
        "areas": [item[:120] for item in areas[:8] if isinstance(item, str)]
        if isinstance(areas, list) else [],
    }


def close_message(state, active_tasks=False):
    if state is None:
        message = (
            "暂时无法读取页面的保存状态，不能据此判断文章未保存。\n"
            "已保存的文章会保留；完全退出可能丢失尚未保存的输入。\n"
            "点“是”缩到托盘保留当前页面；点“否”仍然完全退出；点“取消”返回检查。"
        )
    elif state["dirty"]:
        areas = state["areas"] or ["当前页面输入（位置未提供）"]
        message = (
            "仍有未保存或未完成提交的内容：\n• " + "\n• ".join(areas)
            + "\n已保存的文章会保留。\n"
            "点“是”缩到托盘保留当前输入；点“否”放弃尚未保存的输入并完全退出；"
            "点“取消”返回检查。"
        )
    else:
        message = "点“是”缩到托盘继续运行；点“否”完全退出；点“取消”返回工作台。"
    if active_tasks:
        message += "\n本机任务尚未完成，完全退出会中断任务。"
    return message


def choose_close(preferences, state, active_tasks, ask, *, exit_only=False):
    behavior = "exit" if exit_only else preferences.get()["close_behavior"]
    if behavior == "tray":
        return "tray"
    if behavior == "exit" and state is not None and not state["dirty"] and not active_tasks:
        return "exit"
    message = close_message(state, active_tasks)
    if behavior == "ask":
        message += "\n这次选择会被记住，之后可在“设置 → 关闭窗口行为”修改。"
    choice = ask(message)
    if choice is None:
        return "cancel"
    result = "tray" if choice else "exit"
    if behavior == "ask":
        preferences.update(result)
    return result


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

    def resolve_close(exit_only=False):
        try:
            state = read_close_state(window)
            def ask(message):
                tk = Tk()
                tk.withdraw()
                try:
                    return messagebox.askyesnocancel(TITLE, message, parent=tk)
                finally:
                    tk.destroy()
            try:
                choice = choose_close(
                    service.preferences, state, bool(service.active_tasks()), ask,
                    exit_only=exit_only,
                )
            except Exception:
                tk = Tk()
                tk.withdraw()
                try:
                    messagebox.showerror(TITLE, "关闭习惯设置未能读取或保存，当前页面保留，请稍后重试。", parent=tk)
                finally:
                    tk.destroy()
                return
            if choice == "cancel":
                window.show()
            elif choice == "tray":
                window.hide()
            else:
                exiting.set()
                window.destroy()
        finally:
            closing.release()

    def close(exit_only=False):
        if exiting.is_set():
            return True
        if closing.acquire(False):
            threading.Thread(target=resolve_close, args=(exit_only,), daemon=True).start()
        return False

    window.events.closing += close
    tray = pystray.Icon(
        "paperplane-preview",
        Image.open(root / "web/icon-192.png"),
        TITLE,
        pystray.Menu(
            pystray.MenuItem("打开纸飞机架构验证版", lambda *_: window.show(), default=True),
            pystray.MenuItem("退出验证版", lambda *_: close(exit_only=True)),
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
