import argparse
import base64
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from .store import Library, MAX_FILE
from . import feishu


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, library):
        self.library = library
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, data, status=200, kind='application/json; charset=utf-8', name=None):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; object-src 'none'")
        if name:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
        if status >= 400 and self.command == 'POST' and not getattr(self, '_body_read', False):
            self.discard_rejected_body()

    def discard_rejected_body(self):
        """Drain a bounded rejected upload so closing does not reset its response."""
        try:
            remaining = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return
        # Keep the existing upload size ceiling and bound slow/unfinished clients.
        remaining = min(max(remaining, 0), MAX_FILE * 4 // 3 + 10000)
        deadline = time.monotonic() + 1
        try:
            while remaining:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    break
                self.connection.settimeout(budget)
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, mutation):
        try:
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                return self.send({'error': '只允许本机访问'}, 403)
            origin = self.headers.get('Origin')
            if (origin and origin not in {'http://' + h for h in hosts}) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send({'error': '拒绝跨站请求'}, 403)
            params = parse_qs(urlsplit(self.path).query)
            path = urlsplit(self.path).path
            lib = self.server.library
            if mutation:
                if not secrets.compare_digest(self.headers.get('X-Library-Token', ''), self.server.token):
                    return self.send({'error': '页面会话已更新，请刷新'}, 403)
                if self.headers.get_content_type() != 'application/json':
                    return self.send({'error': '需要 JSON 请求'}, 415)
                size = int(self.headers.get('Content-Length', '0'))
                if size < 2 or size > MAX_FILE * 4 // 3 + 10000:
                    return self.send({'error': '请求体积超限'}, 413)
                self.connection.settimeout(30)
                self._body_read = True
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError('请求格式不正确')
                if path == '/api/scan':
                    return self.send(lib.scan())
                if path == '/api/shutdown':
                    self.send({'ok': True})
                    def stop():
                        with lib.lock:
                            self.server.shutdown()
                    threading.Thread(target=stop, daemon=True).start()
                    return
                if path == '/api/feishu/call':
                    return self.send(feishu.call_api(value))
                if path == '/api/upload':
                    return self.send(lib.upload(value, base64.b64decode(value['content'], validate=True)))
                if path == '/api/folder-import':
                    return self.send(lib.import_group(value.get('name')))
                if path == '/api/sources/register':
                    lib.register(value.get('name'), value.get('path'))
                    return self.send({'ok': True, 'scan': lib.scan(value['name'].strip())})
                if path in {'/api/sources/archive', '/api/sources/restore'}:
                    return self.send(lib.change_source(value.get('name'), path.endswith('/restore')))
                match = re.fullmatch(r'/api/items/([a-f0-9]{32})/(archive|restore)', path)
                if match:
                    return self.send(lib.change_item(match[1], match[2] == 'restore'))
                return self.send({'error': '接口不存在'}, 404)
            if path == '/healthz':
                return self.send({'ok': True, 'app': 'material-library', 'root': str(lib.root), 'refresh_mode': 'manual'})
            if path == '/api/session':
                return self.send({'token': self.server.token})
            if path == '/api/items':
                return self.send(lib.listing(params.get('q', [''])[0], params.get('source', [''])[0], params.get('trash', [''])[0] == '1'))
            if path.startswith('/api/feishu/'):
                return self.send(feishu.get_route(path, params))
            match = re.fullmatch(r'/api/items/([a-f0-9]{32})(/content)?', path)
            if match:
                version = params.get('version', [None])[0]
                if match[2]:
                    item, raw = lib.content(match[1], version)
                    return self.send(raw, kind='application/octet-stream', name=item['name'])
                return self.send(lib.get(match[1], version))
            static = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            static.update({'/feishu': ('feishu.html', 'text/html; charset=utf-8'),
                           '/feishu.js': ('feishu.js', 'text/javascript; charset=utf-8')})
            if path in static:
                name, kind = static[path]
                return self.send((Path(__file__).parent / 'web' / name).read_bytes(), kind=kind)
            self.send({'error': '接口不存在'}, 404)
        except KeyError:
            self.send({'error': '资料不存在或字段缺失'}, 404)
        except feishu.FeishuError as exc:
            self.send({'error': str(exc), 'code': exc.code}, exc.status)
        except ValueError as exc:
            self.send({'error': str(exc)}, 400)
        except (TypeError, OSError):
            self.send({'error': '无法处理该资料，请核对文件与请求'}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8798)
    args = parser.parse_args()
    lib = Library(args.root)
    server = Server(('127.0.0.1', args.port), lib)
    print(f'Material Library: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
