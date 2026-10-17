"""Portable agent client. Token lives in a private config file, not argv/history."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


class Client:
    def __init__(self,config):
        self.url = config['url'].rstrip('/')
        self.token = config['token']
        parsed = urlsplit(self.url)
        import ipaddress
        private = False
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            private = ip.is_loopback or ip in ipaddress.ip_network('100.64.0.0/10')
        except ValueError:
            pass
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Use a service origin without credentials or path')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and private):
            raise ValueError('Use HTTPS or a loopback/Tailscale IP address')
        # Do not send private archive credentials through environment HTTP proxies or redirects.
        self.opener = build_opener(ProxyHandler({}),NoRedirect())

    def request(self,path,data=None,binary=False):
        headers = {'Authorization':'Bearer ' + self.token}
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data,ensure_ascii=False).encode()
        req = Request(self.url + path,body,headers)
        try:
            with self.opener.open(req,timeout=180) as response:
                raw = response.read()
                return (raw,response.headers.get('X-Content-SHA256')) if binary else json.loads(raw)
        except HTTPError as exc:
            try:
                error = json.loads(exc.read()).get('error','request_failed')
            except (ValueError,OSError):
                error = 'request_failed'
            raise RuntimeError(f'HTTP {exc.code}: {error}') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=os.environ.get('ARCHIVE_CLIENT_CONFIG'))
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('rules')
    search = sub.add_parser('search')
    search.add_argument('--project',required=True)
    search.add_argument('--query',default='')
    search.add_argument('--cursor',type=int,default=0)
    for verb in ('get','events','retry','reconcile'):
        sub.add_parser(verb).add_argument('id')
    download = sub.add_parser('download')
    download.add_argument('id')
    download.add_argument('--output',required=True)
    submit = sub.add_parser('submit')
    submit.add_argument('--file',required=True)
    submit.add_argument('--metadata',required=True,help='JSON: request_id, project, topic, title, kind, source, provenance, evidence, environment')
    submit.add_argument('--wait',type=int,default=0,help='Wait up to N seconds for verified archive status')
    args = parser.parse_args()
    if not args.config:
        parser.error('Use --config or ARCHIVE_CLIENT_CONFIG; never put a token in command arguments')
    client = Client(json.loads(Path(args.config).read_text(encoding='utf-8')))
    if args.command == 'rules':
        result = client.request('/v1/rules')
    elif args.command == 'search':
        result = client.request('/v1/items?' + urlencode({'project':args.project,'q':args.query,'cursor':args.cursor}))
    elif args.command == 'submit':
        path = Path(args.file)
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('File exceeds the 10 MiB first-version limit')
        metadata = json.loads(Path(args.metadata).read_text(encoding='utf-8'))
        raw = path.read_bytes()
        metadata.update(filename=path.name,content_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
        result = client.request('/v1/items',metadata)
        deadline = time.monotonic() + max(0,args.wait)
        while result['status'] in {'queued','uploading','verification_pending'} and time.monotonic() < deadline:
            time.sleep(2)
            result = client.request('/v1/items/' + result['id'])
    elif args.command == 'download':
        target = Path(args.output)
        if target.exists():
            raise ValueError('Output exists; choose a new path')
        raw,sha = client.request('/v1/items/' + args.id + '/content',binary=True)
        if not sha or hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('Download checksum mismatch')
        with target.open('xb') as handle:
            handle.write(raw)
        result = {'saved_to':str(target),'sha256':sha,'bytes':len(raw),'verified':True}
    else:
        path = '/v1/items/' + args.id
        if args.command != 'get':
            path += '/' + args.command
        result = client.request(path,{} if args.command in {'retry','reconcile'} else None)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.command == 'submit' and args.wait and result['status'] != 'archived':
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
