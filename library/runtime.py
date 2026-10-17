"""Library server owned by a desktop session, never a detached executable."""
import threading
from pathlib import Path

from .server import Server
from .store import Library


class DesktopLibrary:
    def __init__(self, root, port=8798):
        self.root = Path(root).resolve()
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        from tools.control_library import health
        existing = health(self.port)
        if existing:
            if (existing.get('app') != 'material-library'
                    or Path(existing.get('root', '')).resolve() != self.root):
                raise RuntimeError('资料库端口属于其他程序或目录，请检查后再启动')
            # Independently started shared services keep their own lifetime.
            return
        self.server = Server(('127.0.0.1', self.port), Library(self.root))
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': 0.1},
            name='paperplane-library', daemon=True)
        self.thread.start()

    def stop(self):
        if self.server is None:
            return
        # Let a write holding the library lock complete before closing.
        with self.server.library.lock:
            if self.thread and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join()
        self.thread = None
        self.server = None
