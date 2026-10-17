"""Owner-only offline provisioning. The API never accepts platform tokens or config edits."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets

from .service import SLUG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    sub = parser.add_subparsers(dest='command',required=True)
    issue = sub.add_parser('issue-agent')
    issue.add_argument('--id',required=True)
    issue.add_argument('--projects',required=True,help='Comma-separated configured project IDs')
    issue.add_argument('--read-only',action='store_true')
    issue.add_argument('--url',required=True)
    issue.add_argument('--client-output',required=True)
    disable = sub.add_parser('disable-agent')
    disable.add_argument('--id',required=True)
    args = parser.parse_args()
    path = Path(args.config)
    config = json.loads(path.read_text(encoding='utf-8'))
    if not SLUG.fullmatch(args.id):
        parser.error('Agent ID must be a short lowercase identifier')
    match = next((a for a in config['agents'] if a['id'] == args.id),None)
    if args.command == 'issue-agent':
        if match:
            parser.error('Agent already exists; do not silently rotate an active credential')
        projects = list(dict.fromkeys(args.projects.split(',')))
        if not projects or any(p not in config['projects'] for p in projects):
            parser.error('Use existing, explicit project IDs')
        target = Path(args.client_output)
        token = secrets.token_urlsafe(36)
        client = {'url':args.url,'token':token}
        from .client import Client
        Client(client)  # Validate the transport origin before issuing credentials.
        fd = os.open(target,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(client,handle,indent=2)
        config['agents'].append({'id':args.id,'projects':projects,
            'permissions':['read'] if args.read_only else ['read','write'],
            'token_sha256':hashlib.sha256(token.encode()).hexdigest()})
    else:
        if not match:
            parser.error('Unknown agent')
        match['disabled'] = True
    # In-place update keeps the existing Docker bind mount; restart applies changes.
    # Keep a rollback copy restricted to the owner; it contains hashes, not plain tokens.
    backup = path.with_suffix('.before-agent-change.json')
    backup.write_bytes(path.read_bytes())
    backup.chmod(0o600)
    with path.open('w',encoding='utf-8') as handle:
        json.dump(config,handle,ensure_ascii=False,indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    print(json.dumps({'agent':args.id,'operation':args.command,
        'restart_required':True,'instruction':'Restart the archive container, then verify this agent scope.'}))


if __name__ == '__main__':
    main()
