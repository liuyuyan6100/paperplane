"""Feishu native-file adapter. Credentials remain owned by the installed CLI."""
import json
import os
from pathlib import Path
import subprocess
import threading


class ProviderError(Exception):
    def __init__(self, code, *, uncertain=False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


class FeishuCLI:
    def __init__(self, workdir, command=None):
        self.workdir = Path(workdir).resolve()
        self.command = command or ['lark-cli']
        self.lock = threading.Lock()

    def call(self, args, *, mutation=False):
        env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1',
                   LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
        try:
            with self.lock:
                proc = subprocess.run(self.command + args + ['--as', 'user'],
                    cwd=self.workdir, env=env, capture_output=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            raise ProviderError('provider_unreachable', uncertain=mutation) from None
        result = None
        for raw in (proc.stdout, proc.stderr):
            try:
                text = raw.decode('utf-8-sig')
                pos = text.find('{')
                value = json.loads(text[pos:]) if pos >= 0 else None
                if isinstance(value, dict):
                    result = value
                    break
            except (ValueError, UnicodeError):
                pass
        if proc.returncode or not result or result.get('ok') is not True:
            err = (result or {}).get('error', {})
            # Never return command output, which may contain authorization details.
            kind = err.get('subtype', err.get('type', 'unknown'))
            known_rejection = kind in {'permission_denied', 'missing_scope', 'not_found',
                'invalid_parameters', 'invalid_argument', 'not_authenticated',
                'confirmation_required', 'quota_exceeded', 'rate_limit', 'token_missing',
                'token_expired', 'refresh_token_expired', 'token_invalid'}
            code = kind if known_rejection else 'provider_error'
            raise ProviderError(code, uncertain=mutation and not known_rejection)
        return result.get('data', {})

    def privacy(self, project):
        data = self.call(['wiki', 'spaces', 'get', '--params',
                          json.dumps({'space_id': project['space_id']})])
        space = data.get('space', {})
        if space.get('visibility') != 'private' or space.get('open_sharing') != 'closed':
            raise ProviderError('target_not_private')

    def upload(self, path, name, project):
        path = Path(path).resolve().relative_to(self.workdir).as_posix()
        domain, verb = ('markdown', '+create') if name.lower().endswith('.md') else ('drive', '+upload')
        result = self.call([domain, verb, '--file', path, '--name', name,
                           '--wiki-token', project['node_token']], mutation=True)
        if not result.get('file_token'):
            raise ProviderError('missing_upload_receipt', uncertain=True)
        return result['file_token']

    def download(self, token, path):
        output = Path(path).resolve().relative_to(self.workdir).as_posix()
        self.call(['drive', '+download', '--file-token', token, '--output', output])

    def nodes(self, project):
        nodes = []
        for parent in dict.fromkeys([project['node_token']] + project.get('read_nodes', [])):
            data = self.call(['wiki', '+node-list', '--space-id', project['space_id'],
                '--parent-node-token', parent, '--page-all', '--page-limit', '20'])
            if data.get('has_more'):
                raise ProviderError('directory_scan_incomplete')
            nodes.extend(data.get('nodes', []))
        return nodes
