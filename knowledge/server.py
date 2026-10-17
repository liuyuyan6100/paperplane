"""HTTP interface for private archive clients; no browser login or model required."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, quote, urlsplit

from .provider import FeishuCLI, ProviderError
from .service import Archive, MAX_FILE, Problem, SLUG


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, archive):
        self.archive = archive
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'MaterialArchive/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        pass  # No credentials, filenames, queries or content in HTTP logs.

    def send(self, data, status=200, filename=None, checksum=None):
        raw = data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/octet-stream' if filename else 'application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''" + quote(filename,safe=''))
        if checksum:
            self.send_header('X-Content-SHA256',checksum)
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise Problem('chunked_requests_not_supported',400)
        if self.headers.get_content_type() != 'application/json':
            raise Problem('json_required',415)
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1:
            raise Problem('content_length_required',411)
        try:
            size = int(lengths[0])
        except ValueError:
            raise Problem('invalid_content_length',400) from None
        if size < 2 or size > MAX_FILE * 4 // 3 + 100_000:
            raise Problem('request_too_large',413)
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            value = json.loads(raw)
        except (ValueError,OSError):
            raise Problem('invalid_json',400) from None
        if not isinstance(value,dict):
            raise Problem('json_object_required',400)
        return value

    def dispatch(self, write):
        app = self.server.archive
        try:
            url = urlsplit(self.path)
            if url.path == '/healthz' and not write:
                ready = bool(app.worker and app.worker.is_alive())
                self.send({'ok':ready},200 if ready else 503)
                return
            # This is a machine interface. Cookies and browser ambient auth are never accepted.
            if self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise Problem('browser_cross_origin_not_allowed',403)
            auth = self.headers.get('Authorization','')
            if not auth.startswith('Bearer '):
                raise Problem('authentication_required',401)
            agent = app.authenticate(auth[7:])
            if not write and url.path == '/v1/rules':
                self.send(app.rules(agent))
                return
            if url.path == '/v1/items':
                if write:
                    value = app.submit(agent,self.body())
                    self.send(value,200 if value['reused'] else 202)
                else:
                    q = parse_qs(url.query)
                    arg = lambda key, default='':q.get(key,[default])[0]
                    self.send(app.listing(agent,arg('project'),arg('q'),arg('status'),arg('cursor',0),arg('limit',50)))
                return
            match = re.fullmatch(r'/v1/items/([a-f0-9]{32})(?:/(content|events|retry|reconcile))?',url.path)
            if not match:
                raise Problem('not_found',404)
            item_id, action = match.groups()
            if not write:
                if action is None:
                    self.send(app.get(agent,item_id))
                elif action == 'events':
                    self.send(app.events(agent,item_id))
                elif action == 'content':
                    raw,name,sha = app.download(agent,item_id)
                    self.send(raw,filename=name,checksum=sha)
                else:
                    raise Problem('method_not_allowed',405)
            else:
                self.body()
                if action == 'retry':
                    self.send(app.retry(agent,item_id))
                elif action == 'reconcile':
                    self.send(app.reconcile(agent,item_id))
                else:
                    raise Problem('method_not_allowed',405)
        except Problem as exc:
            self.send({'error':exc.code},exc.status)
        except ProviderError as exc:
            self.send({'error':exc.code},502)
        except Exception:
            self.send({'error':'internal_error_check_receipt_before_resubmitting'},500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8797)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if not config.get('agents') or not config.get('projects'):
        raise SystemExit('Configure project-scoped agents and Feishu destinations first')
    seen = set()
    for agent in config['agents']:
        if (not SLUG.fullmatch(agent.get('id','')) or agent['id'] in seen
            or not re.fullmatch(r'[a-f0-9]{64}',agent.get('token_sha256',''))
            or not agent.get('projects') or any(p not in config['projects'] for p in agent['projects'])
            or not set(agent.get('permissions',[])) <= {'read','write','admin'}):
            raise SystemExit('Invalid agent identity, hash, project or permission configuration')
        seen.add(agent['id'])
    for project in config['projects'].values():
        parsed = urlsplit(project['wiki_origin'])
        if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.feishu.cn') or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise SystemExit('Invalid Feishu Wiki origin')
    provider = FeishuCLI(args.data_dir,config.get('cli_command'))
    app = Archive(args.data_dir,config,provider)
    server = Server((args.host,args.port),app)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
