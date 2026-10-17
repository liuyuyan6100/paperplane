"""Revision persistence, restoration and independent reuse across real entry points."""
import json
import threading
import urllib.request
import urllib.error
from unittest.mock import patch

import pytest

from studio import profiles, memories
from studio.db import Database, uid
from studio.profile_sync import put, digest, records
from studio.server import Server
from studio.service import Studio, Problem
from studio.migration import backup_bundle
from tools.restore_studio import restore as restore_bundle


@pytest.fixture
def app(tmp_path):
    instance = Studio(tmp_path / 'data')
    yield instance
    instance.close()


def profile(app, **values):
    return profiles.save(app, {'name': '历史测试', 'goal': 'OLD_GOAL', **values})


def versions(app, p):
    return profiles.history(app, p['id'])['revisions']


def accepted(app, p):
    m = memories.propose(app, p['id'], {'content': 'ACCOUNT_MEMORY', 'source_text': 'ACCOUNT_MEMORY', 'quote': 'ACCOUNT_MEMORY'})
    return memories.update(app, p['id'], m['id'], {'version': m['version'], 'action': 'confirm'})


def test_edit_restore_and_diff_are_atomic(app):
    p = profile(app)
    profiles.save(app, {'version': 1, 'goal': 'NEW_GOAL'}, p['id'])
    detail = profiles.revision(app, p['id'], 1)
    assert detail['changes'] == [{'field': 'goal', 'label': '运营目标', 'current': 'NEW_GOAL', 'selected': 'OLD_GOAL'}]
    restored = profiles.restore(app, p['id'], {'version': 2, 'target_version': 1})
    assert restored['goal'] == 'OLD_GOAL' and restored['version'] == 3
    assert [r['action'] for r in versions(app, p)] == ['restore', 'edit', 'create']
    assert profiles.revision(app, p['id'], 2)['revision']['snapshot']['goal'] == 'NEW_GOAL'
    with pytest.raises(Problem):
        profiles.restore(app, p['id'], {'version': 3, 'target_version': 1})
    assert len(versions(app, p)) == 3


@pytest.mark.parametrize('values', [{'version': 1, 'target_version': 1}, {'version': True, 'target_version': 1},
                                  {'version': 2, 'target_version': True}, {'version': 2, 'target_version': 999}, []])
def test_invalid_or_stale_restore_preserves_current(app, values):
    p = profile(app)
    current = profiles.save(app, {'version': 1, 'goal': 'NEW_GOAL'}, p['id'])
    with pytest.raises(Problem): profiles.restore(app, p['id'], values)
    assert profiles.get(app, p['id']) == current
    assert len(versions(app, p)) == 2


def test_restore_name_conflict_and_missing_image_do_not_partial_write(app):
    ref = '/media/' + uid() + '.png'
    file = app.db.data_dir / ref.lstrip('/')
    file.parent.mkdir(exist_ok=True)
    file.write_bytes(b'test image')
    p = profile(app, visual_references=[ref])
    current = profiles.save(app, {'version': 1, 'name': '新名称', 'visual_references': []}, p['id'])
    other = profile(app)
    with pytest.raises(Problem): profiles.restore(app, p['id'], {'version': 2, 'target_version': 1})
    profiles.save(app, {'version': 1, 'name': '释放名称'}, other['id'])
    file.unlink()
    with pytest.raises(Problem): profiles.restore(app, p['id'], {'version': 2, 'target_version': 1})
    assert profiles.get(app, p['id']) == current
    assert len(versions(app, p)) == 2


def test_upgrade_records_only_available_baseline_and_is_repeatable(app):
    p = profile(app)
    p = profiles.save(app, {'version': 1, 'goal': 'current'}, p['id'])
    post = app.save_post({'title': '保留文章', 'profile_id': p['id'], 'body': '原文'})
    with app.db.connect() as db: db.execute('DROP TABLE profile_revisions')
    Database(app.db.data_dir)
    Database(app.db.data_dir)
    assert [(r['version'], r['action']) for r in versions(app, p)] == [(2, 'baseline')]
    assert profiles.get(app, p['id']) == p
    assert app.post(post['id'])['body'] == '原文'
    with pytest.raises(Problem) as missing: profiles.revision(app, p['id'], 1)
    assert missing.value.status == 404


def test_duplicate_is_independent_and_retry_idempotent(app):
    ref = '/media/' + uid() + '.png'
    file = app.db.data_dir / ref.lstrip('/')
    file.parent.mkdir(exist_ok=True)
    file.write_bytes(b'test image')
    p = profile(app, links='https://example.com/account', memory='LEGACY_EXPERIENCE', visual_references=[ref])
    accepted(app, p)
    app.save_post({'title': '原账号文章', 'profile_id': p['id']})
    values = {'request_id': uid(), 'version': 1, 'name': '独立账号'}
    copy = profiles.duplicate(app, p['id'], values)
    assert copy['id'] != p['id'] and copy['version'] == 1
    assert copy['links'] == copy['memory'] == ''
    assert copy['visual_references'] == [ref] and file.read_bytes() == b'test image'
    assert memories.recall(app, copy['id']) == []
    assert len(app.state()['posts']) == 1
    profiles.save(app, {'version': 1, 'goal': 'SOURCE_CHANGE'}, p['id'])
    assert profiles.duplicate(app, p['id'], values) == copy
    assert profiles.get(app, copy['id'])['goal'] == 'OLD_GOAL'
    for invalid in ({**values, 'name': 'different'}, {**values, 'request_id': uid()}):
        with pytest.raises(Problem): profiles.duplicate(app, p['id'], invalid)
    assert len(profiles.listing(app)) == 2
    profiles.save(app, {'version': 1, 'goal': 'COPY_CHANGE'}, copy['id'])
    assert profiles.get(app, p['id'])['goal'] == 'SOURCE_CHANGE'


def test_legacy_missing_fields_restore_to_empty_instead_of_retaining_later_values(app):
    p = profile(app)
    with app.db.connect() as db:
        fields = json.loads(db.execute('SELECT content FROM profiles WHERE id=?', (p['id'],)).fetchone()[0])
        fields.pop('visual_style'); fields.pop('visual_references')
        db.execute('UPDATE profiles SET content=? WHERE id=?', (json.dumps(fields), p['id']))
        db.execute('DELETE FROM profile_revisions WHERE profile_id=?', (p['id'],))
    Database(app.db.data_dir)
    assert profiles.revision(app, p['id'], 1)['changes'] == []
    profiles.save(app, {'version': 1, 'visual_style': '后来新增的风格'}, p['id'])
    restored = profiles.restore(app, p['id'], {'version': 2, 'target_version': 1})
    assert restored['visual_style'] == '' and restored['visual_references'] == []
    assert profiles.revision(app, p['id'], 1)['changes'] == []
    with pytest.raises(Problem): profiles.restore(app, p['id'], {'version': 3, 'target_version': 1})


def test_history_scoped_to_profile_and_sync_records_local_revisions(app):
    p = profile(app)
    other = profile(app, name='另一画像')
    changed = put(app, p['id'], {**p, 'goal': 'SYNC_GOAL'}, digest(p))
    assert put(app, p['id'], {**p, 'goal': 'SYNC_GOAL'}, digest(p)) == changed
    assert [r['action'] for r in versions(app, p)] == ['sync', 'create']
    with pytest.raises(Problem): profiles.revision(app, other['id'], 2)
    assert all('revisions' not in item for item in records(app))
    remote_id = uid()
    put(app, remote_id, {**p, 'name': '远端画像', 'version': 99}, None)
    assert [(r['version'], r['action']) for r in versions(app, {'id': remote_id})] == [(1, 'sync')]


def test_new_creation_uses_restored_profile_and_existing_task_keeps_snapshot(app):
    p = profile(app)
    accepted(app, p)
    profiles.save(app, {'version': 1, 'goal': 'TASK_GOAL'}, p['id'])
    post = app.save_post({'title': '原稿', 'profile_id': p['id']})
    old_task = app.workflows.create({'post_id': post['id'], 'version': 1})
    profiles.restore(app, p['id'], {'version': 2, 'target_version': 1})
    with patch('studio.adapters.ai_draft', return_value='合成提纲') as model:
        app.workflows.tick()
    assert 'TASK_GOAL' in model.call_args.args[1] and 'ACCOUNT_MEMORY' in model.call_args.args[1]
    assert app.workflows.get(old_task['id'])['profile']['version'] == 2
    new_post = app.save_post({'title': '新稿', 'profile_id': p['id']})
    new_task = app.workflows.create({'post_id': new_post['id'], 'version': 1})
    assert new_task['profile']['goal'] == 'OLD_GOAL' and new_task['profile']['version'] == 3
    assert 'ACCOUNT_MEMORY' in json.dumps(new_task['profile'])
    with patch('studio.adapters.ai_draft', return_value='新任务提纲') as model:
        app.workflows.tick()
    assert 'OLD_GOAL' in model.call_args.args[1] and 'ACCOUNT_MEMORY' in model.call_args.args[1]
    assert 'TASK_GOAL' not in model.call_args.args[1]
    copy = profiles.duplicate(app, p['id'], {'version': 3, 'name': '复用账号', 'request_id': uid()})
    copy_post = app.save_post({'title': '复用创作', 'profile_id': copy['id']})
    copied_task = app.workflows.create({'post_id': copy_post['id'], 'version': 1})
    assert 'ACCOUNT_MEMORY' not in json.dumps(copied_task['profile'])
    assert copied_task['profile']['goal'] == 'OLD_GOAL'
    with patch('studio.adapters.ai_draft', return_value='复用画像提纲') as model:
        app.workflows.tick()
    assert 'OLD_GOAL' in model.call_args.args[1] and 'ACCOUNT_MEMORY' not in model.call_args.args[1]


def test_backup_restore_keeps_all_history(app, tmp_path):
    p = profile(app)
    profiles.save(app, {'version': 1, 'goal': 'second'}, p['id'])
    bundle = tmp_path / 'backup.zip'
    bundle.write_bytes(backup_bundle(app))
    restore_bundle(bundle, tmp_path / 'restored')
    restored = Studio(tmp_path / 'restored')
    try:
        assert versions(restored, p) == versions(app, p)
        assert profiles.revision(restored, p['id'], 1) == profiles.revision(app, p['id'], 1)
    finally: restored.close()


def test_http_history_restore_duplicate_and_authorization(app):
    server = Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(path, values=None, token=None):
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/profiles' + path,
              data=json.dumps(values).encode() if values is not None else None,
              headers={'Content-Type': 'application/json', 'X-Studio-Token': token or server.token})
        with opener.open(req) as response: return json.load(response)
    try:
        p = call('', {'name': 'HTTP'})
        route = '/' + p['id']
        call(route, {'version': 1, 'goal': 'changed'})
        assert len(call(route + '/history')['revisions']) == 2
        assert call(route + '/history/1')['changes'][0]['field'] == 'goal'
        with pytest.raises(urllib.error.HTTPError) as denied:
            call(route + '/restore', {'version': 2, 'target_version': 1}, 'bad')
        assert denied.value.code == 403
        assert call(route + '/restore', {'version': 2, 'target_version': 1})['version'] == 3
        assert call(route + '/duplicate', {'version': 3, 'name': 'HTTP副本', 'request_id': uid()})['version'] == 1
    finally:
        server.shutdown(); server.server_close(); thread.join()
