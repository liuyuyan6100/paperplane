import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

from studio import profiles
from studio.db import dumps
from studio.service import Studio, Problem
from studio.repurpose import clean_content
from studio.server import Server, export_package
from studio.migration import backup_bundle
from tools.restore_studio import restore


def content(platform='csdn'):
    c = {'title': 'Agent memory practice' if platform == 'devto' else '实践中的 Agent 记忆',
         'body': 'A reproducible example with verified evidence.\n\n```python\nprint(1)\n```' if platform == 'devto' else '只保留已经提供的实践证据。\n\n```python\nprint(1)\n```',
         'summary': 'Practical notes' if platform == 'devto' else '分享具体实践', 'tags': ['agent'], 'caption': '', 'pages': []}
    if platform == 'xiaohongshu':
        c.update(caption='从三个步骤理解记忆 #Agent', pages=[{'title': f'第{i + 1}页', 'text': f'说明第{i + 1}个要点。', 'visual': '使用清晰的工程示意'} for i in range(3)])
    return c


class RepurposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name) / 'data')
        self.addCleanup(self.app.close)
        self.profile = profiles.save(self.app, {'name': '测试画像', 'goal': '交流亲测经验'})
        self.post = self.app.save_post({'title': '母稿', 'body': '旧母稿证据。扫码关注公众号。', 'stage': 'draft', 'profile_id': self.profile['id']})

    def create(self, platforms=None, **kwargs):
        return self.app.repurpose.create({'post_id': self.post['id'], 'post_version': self.post['version'], 'platforms': platforms or ['csdn'], 'visual_mode':'cards', **kwargs})['variants']

    def complete(self, task):
        with patch.object(self.app, 'generate_draft', return_value=dumps(content(task['platform']))):
            self.assertTrue(self.app.repurpose.tick())
        while self.app.deliverables.tick():pass
        return self.app.repurpose.get(task['id'])

    def test_batch_is_idempotent_and_rejects_request_reuse(self):
        tasks = self.create(['csdn', 'devto'], request_id='a'*32)
        self.assertEqual([t['id'] for t in tasks], [t['id'] for t in self.create(['csdn', 'devto'], request_id='a'*32)])
        with self.assertRaises(Problem):
            self.create(['juejin'], request_id='a'*32)
        self.assertEqual(len(self.app.repurpose.list()), 2)

    def test_fixed_snapshots_and_changed_source_require_explicit_choice(self):
        task = self.create()[0]
        self.app.save_post({'version': 1, 'body': '新母稿不能污染旧快照'}, self.post['id'])
        profiles.save(self.app, {'version': 1, 'goal': '新目标'}, self.profile['id'])
        with patch.object(self.app, 'generate_draft', return_value=dumps(content())) as model:
            self.app.repurpose.tick()
        sent, instruction = model.call_args.args
        self.assertEqual(sent['_profile_snapshot']['goal'], '交流亲测经验')
        self.assertIn('旧母稿证据', instruction)
        self.assertNotIn('新母稿不能污染旧快照', instruction)
        done = self.app.repurpose.get(task['id'])
        self.assertTrue(done['source_changed'])
        self.assertTrue(done['profile_changed'])
        with self.assertRaises(Problem):
            self.app.repurpose.apply(task['id'], {'version': done['version']})
        while self.app.deliverables.tick():pass
        adopted = self.app.repurpose.apply(task['id'], {'version': done['version'], 'ack_source_changes': True})
        self.assertEqual(adopted['post']['source_version'], 1)
        self.assertEqual(self.app.post(self.post['id'])['body'], '新母稿不能污染旧快照')

    def test_independent_failure_and_retry_do_not_regenerate_success(self):
        tasks = self.create(['csdn', 'devto'])
        with patch.object(self.app, 'generate_draft', return_value='bad'):
            self.app.repurpose.tick()
        self.assertEqual(self.app.repurpose.get(tasks[0]['id'])['status'], 'failed')
        self.complete(tasks[1])
        self.app.repurpose.retry(tasks[0]['id'])
        self.complete(tasks[0])
        self.assertEqual(self.app.repurpose.get(tasks[1]['id'])['content'], content('devto'))
        self.assertEqual(self.app.jobs(), [])

    def test_edit_conflict_and_apply_is_atomic_idempotent(self):
        task = self.complete(self.create()[0])
        updated = self.app.repurpose.edit(task['id'], {'version': task['version'], 'content': {**content(), 'title': '人工修改'}})
        with self.assertRaises(Problem):
            self.app.repurpose.edit(task['id'], {'version': task['version'], 'content': content()})
        count = len(self.app.state()['posts'])
        while self.app.deliverables.tick():pass
        result = self.app.repurpose.apply(task['id'], {'version': updated['version']})
        again = self.app.repurpose.apply(task['id'], {'version': updated['version']})
        self.assertEqual(result['post']['id'], again['post']['id'])
        self.assertEqual(len(self.app.state()['posts']), count + 1)
        self.assertEqual(result['post']['title'], '人工修改')
        self.assertEqual(result['post']['target_channel'], 'csdn')
        self.assertEqual(self.app.post(self.post['id'])['version'], 1)
        self.assertEqual(self.app.jobs(), [])

    def test_regeneration_preserves_adopted_manual_edit_and_lineage(self):
        first = self.complete(self.create()[0])
        post = self.app.repurpose.apply(first['id'], {'version': first['version']})['post']
        self.app.save_post({'version': 1, 'body': '人工改稿', 'target_channel': 'wechat', 'source_version': 100}, post['id'])
        second = self.complete(self.create()[0])
        self.assertNotEqual(first['id'], second['id'])
        original = self.app.post(post['id'])
        self.assertEqual(original['body'], '人工改稿')
        self.assertEqual(original['target_channel'], 'csdn')
        self.assertEqual(original['source_version'], 1)

    def test_target_channels_and_stale_warning_use_existing_preflight(self):
        task = self.complete(self.create()[0])
        post = self.app.repurpose.apply(task['id'], {'version': task['version']})['post']
        post = self.app.save_post({'version': 1, 'stage': 'ready'}, post['id'])
        self.assertFalse(self.app.preflight(post, ['juejin'])['ok'])
        self.assertTrue(self.app.preflight(post, ['csdn'])['ok'])
        self.app.save_post({'version': 1, 'body': '新的母稿'}, self.post['id'])
        checks = self.app.preflight(post, ['csdn'])['checks']
        self.assertEqual(next(c for c in checks if c['label'] == '母稿版本')['level'], 'warn')
        with self.assertRaises(Problem):
            self.app.enqueue(post['id'], {'version': post['version'], 'channels': ['csdn']})
        self.app.enqueue(post['id'], {'version': post['version'], 'channels': ['csdn'], 'ack_warnings': True})
        job = self.app.job_detail(self.app.jobs()[0]['id'])
        self.assertEqual(job['snapshot']['target_channel'], 'csdn')
        with zipfile.ZipFile(io.BytesIO(export_package(job, self.app.db.data_dir))) as z:
            self.assertIn(post['body'].replace('/media/', 'images/'), z.read('article.md').decode())
            self.assertTrue(any(n.startswith('images/') for n in z.namelist()))

    def test_late_result_discarded_after_stop(self):
        task = self.create()[0]
        def interrupted(*args, **kwargs):
            self.app.repurpose.interrupt()
            return dumps(content())
        with patch.object(self.app, 'generate_draft', side_effect=interrupted):
            self.app.repurpose.tick()
        done = self.app.repurpose.get(task['id'])
        self.assertEqual(done['status'], 'interrupted')
        self.assertEqual(done['content'], {})

    def test_restart_and_backup_restore_never_resume_calls(self):
        done = self.complete(self.create()[0])
        pending = self.create()[0]
        archive = Path(self.tmp.name) / 'backup.zip'
        archive.write_bytes(backup_bundle(self.app))
        target = Path(self.tmp.name) / 'restored'
        restore(archive, target)
        other = Studio(target)
        self.addCleanup(other.close)
        self.assertEqual(other.repurpose.get(done['id'])['content'], done['content'])
        self.assertEqual(other.repurpose.get(pending['id'])['status'], 'interrupted')
        self.assertFalse(other.repurpose.tick())

    def test_xhs_pages_persist_with_real_generated_cards(self):
        task = self.complete(self.create(['xiaohongshu'])[0])
        post = self.app.repurpose.apply(task['id'], {'version': task['version']})['post']
        self.assertEqual(len(post['xhs']['pages']), 3)
        self.assertEqual(len(post['xhs']['photos']), 3)
        self.assertTrue(all((self.app.db.data_dir / 'media' / Path(u).name).is_file() for u in post['xhs']['photos']))
        saved = self.app.save_post({'version': 1, 'summary': '更新摘要'}, post['id'])
        self.assertEqual(saved['xhs']['pages'], post['xhs']['pages'])
        saved = self.app.save_post({'version': 2, 'stage': 'ready'}, post['id'])
        self.assertTrue(self.app.preflight(saved, ['xiaohongshu'])['ok'])

    def test_invalid_inputs_and_english_validation(self):
        for extra in ({'post_version': True}, {'post_version': 0}, {'platforms': ['bad']}, {'platforms': ['csdn','csdn']}):
            with self.assertRaises(Problem):
                self.create(**extra)
        with self.assertRaises(Problem):
            clean_content({**content('devto'), 'body': '这是一篇没有英文适配的中文正文。'*10}, 'devto')
        with self.assertRaises(Problem):
            clean_content({**content('xiaohongshu'), 'pages': []}, 'xiaohongshu')

    def test_worker_picks_up_and_history_omits_long_bodies(self):
        task = self.create()[0]
        called = threading.Event()
        def model(*args, **kwargs):
            called.set()
            return dumps(content())
        with patch.object(self.app, 'generate_draft', side_effect=model):
            self.app.start_worker()
            self.assertTrue(called.wait(4))
        row = self.app.repurpose.list()[0]
        self.assertNotIn('context', row)
        self.assertNotIn('body', row['content'])

    def test_http_routes_require_write_token_and_serve_frontend(self):
        import urllib.request
        import urllib.error
        server = Server(('127.0.0.1', 0), self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{server.server_port}'
        def request(path, values=None, token=True):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['X-Studio-Token'] = server.token
            req = urllib.request.Request(base + path, data=dumps(values).encode() if values is not None else None, headers=headers)
            with opener.open(req, timeout=3) as response:
                return response.read()
        with self.assertRaises(urllib.error.HTTPError) as denied:
            request('/api/repurpose', {}, False)
        self.assertEqual(denied.exception.code, 403)
        tasks = json.loads(request('/api/repurpose', {'post_id': self.post['id'], 'post_version': 1, 'platforms': ['csdn'],'visual_mode':'cards'}))['variants']
        task = self.complete(tasks[0])
        edited = json.loads(request('/api/repurpose/' + task['id'] + '/edit', {'version': task['version'], 'content': content()}))
        result = json.loads(request('/api/repurpose/' + task['id'] + '/apply', {'version': edited['version']}))
        self.assertEqual(result['post']['target_channel'], 'csdn')
        self.assertEqual(len(json.loads(request('/api/repurpose?post_id=' + self.post['id']))['variants']), 1)
        self.assertIn(b'handleRepurposeAction', request('/repurpose.js'))


if __name__ == '__main__':
    unittest.main()
