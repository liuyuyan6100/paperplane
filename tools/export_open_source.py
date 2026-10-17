"""Export an allowlisted source snapshot, excluding private history and user data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES=('studio','web','library','knowledge','skills','agents','tests')
FILES=('run.py','desktop.py','desktop_core.py','desktop_publishing.py','desktop_updates.py','desktop_xhs.py','desktop_version.py','private_update_server.py','release_protocol.py','desktop-installer.iss','PaperPlaneStudio.spec','requirements.txt','requirements-cloud.txt','requirements-desktop.txt','requirements-test.txt','pytest.ini','.gitattributes','.dockerignore','.env.cloud.example','Dockerfile','Dockerfile.knowledge','Dockerfile.knowledge.dockerignore','Dockerfile.updates','Dockerfile.updates.dockerignore','compose.yaml','compose.knowledge.yaml','compose.updates.yaml','LICENSE','NOTICE.md')
TOOLS=('build_desktop.ps1','control_studio.py','control_library.py','restore_studio.py','make_desktop_icon.py','make_app_icons.py','upgrade_paths.cs','prepare_desktop_upgrade.ps1','publish_desktop_release.py','init_private_updates.py','export_open_source.py','verify_skill_phase4.py')
DOCS=('USER_GUIDE.md','PROJECT_COMPARISON.md','DATA_AND_SECURITY.md','DEPLOYMENT.md')
IGNORED_PARTS={'__pycache__','.pytest_cache','node_modules','.git'}
EXTENSIONS={'.py','.js','.css','.html','.json','.md','.txt','.yaml','.yml','.png','.svg','.webmanifest','.cjs'}

def export(destination):
    destination=Path(destination).resolve()
    if destination==ROOT or ROOT in destination.parents:
        raise ValueError('Export outside the private source checkout')
    destination.mkdir(parents=True,exist_ok=False)
    manifest=[]
    def copy(source,relative=None):
        relative=relative or source.relative_to(ROOT)
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        manifest.append({'path':str(relative).replace('\\','/'),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    for name in DIRECTORIES:
        for source in (ROOT/name).rglob('*'):
            if source.is_file() and not IGNORED_PARTS.intersection(source.relative_to(ROOT).parts) and source.suffix in EXTENSIONS:
                copy(source)
    for name in FILES:copy(ROOT/name)
    for name in TOOLS:copy(ROOT/'tools'/name)
    for name in DOCS:copy(ROOT/'docs'/name)
    for source in (ROOT/'docs/visual-styles/aiclaw-warm-engineering-v1').rglob('*'):
        if source.is_file():copy(source)
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    (destination/'README.md').write_text(readme,encoding='utf-8')
    (destination/'.gitignore').write_text('\n'.join(('.venv/','venv/','__pycache__/','*.py[cod]','.pytest_cache/','.coverage','htmlcov/','data/','data-*/','backups/','test-results/','build/','dist/','private-updates/','*.sqlite*','*.db','private.json','.env','.env.cloud','updates.env','.paperplane-paths.json','*.log','.DS_Store','web/icon.ico','')))
    (destination/'AGENTS.md').write_text('# PaperPlane contribution notes\n\nRead README.md and docs/USER_GUIDE.md before changing behavior. Keep runtime data and credentials out of Git. Use synthetic tests and isolated data directories. AI-generated text must be reviewed before adoption. A platform draft is not a public publication. Unknown delivery outcomes require reconciliation before any resend. Preserve version checks and user edits. Never treat API menu/reply video uploads as article-native video.\n',encoding='utf-8')
    workflow=destination/'.github/workflows/tests.yml';workflow.parent.mkdir(parents=True)
    workflow.write_text('''name: Tests
on: [push, pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  test:
    runs-on: windows-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - run: python -m pip install -r requirements-desktop.txt -r requirements-cloud.txt -r requirements-test.txt
      - run: python -m pytest -q -o faulthandler_timeout=90
''',encoding='utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination');parser.add_argument('--manifest',required=True)
    args=parser.parse_args();manifest=export(args.destination)
    Path(args.manifest).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'files_copied':len(manifest),'destination':str(Path(args.destination).resolve())}))
