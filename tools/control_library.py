"""Start a shared library on loopback, without changing startup or proxy settings."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configured_root():
    sys.path.insert(0, str(ROOT))
    from studio.paths import default_data_dir
    settings = default_data_dir(ROOT) / 'shared-library.json'
    if not settings.is_file():
        raise RuntimeError('请先在本机数据目录配置 shared-library.json，或用 --root 指定主库')
    root = Path(json.loads(settings.read_text(encoding='utf-8'))['root'])
    if not root.is_absolute():
        raise RuntimeError('共享资料主库必须使用绝对路径')
    return root.resolve()


def health(port=8798):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def start(root):
    root = Path(root).resolve()
    existing = health()
    if existing:
        if existing.get('app') != 'material-library' or Path(existing.get('root', '')).resolve() != root:
            raise RuntimeError('资料库端口属于其他程序或目录，请检查后再启动')
        return existing
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'service.log').open('ab') as log:
        command = [sys.executable, '--library-service', '--library-root', str(root)] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'library.server', '--root', str(root)]
        child = subprocess.Popen(command,
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                 start_new_session=os.name != 'nt')
    for _ in range(30):
        existing = health()
        if existing and existing.get('app') == 'material-library' and Path(existing.get('root', '')).resolve() == root:
            (root / 'service.json').write_text(json.dumps({'pid': child.pid, 'url': 'http://127.0.0.1:8798', 'root': str(root)}), encoding='utf-8')
            return existing
        if child.poll() is not None:
            break
        time.sleep(.2)
    raise RuntimeError('资料库未能启动，请查看 service.log')


def stop(root):
    root = Path(root).resolve()
    existing = health()
    if not existing:
        return {'running': False}
    if existing.get('app') != 'material-library' or Path(existing.get('root', '')).resolve() != root:
        raise RuntimeError('资料库端口属于其他程序或目录，不会停止它')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8798/api/session', timeout=3) as response:
        token = json.load(response)['token']
    req = urllib.request.Request('http://127.0.0.1:8798/api/shutdown', data=b'{}', headers={'Content-Type':'application/json','X-Library-Token':token})
    with opener.open(req, timeout=5) as response:
        json.load(response)
    for _ in range(50):
        if not health():
            return {'running': False}
        time.sleep(.1)
    raise RuntimeError('资料库仍在完成请求，请稍后查看状态')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', choices=('start','stop','status'), default='start')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    args.root = args.root or configured_root()
    result = start(args.root) if args.action == 'start' else stop(args.root) if args.action == 'stop' else health() or {'running':False}
    print(json.dumps(result, ensure_ascii=False))
