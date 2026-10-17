import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from studio import memories, profiles
from studio.db import uid, now
from studio.service import Studio, Problem
from studio.publication_progress import register


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(self.tmp.name)
        self.addCleanup(self.app.close)
        self.profile = profiles.save(self.app, {'name': '合成技术账号', 'goal': '帮助读者实践'})
        self.post = self.app.save_post({'title': '实践教程', 'body': '可复现的三步操作。', 'stage': 'ready', 'profile_id': self.profile['id']})
        register(self.app, self.post['id'], {'version': 1, 'channels': ['wechat'], 'confirmed': True})
        self.post = self.app.post(self.post['id'])
        self.job = self.app.jobs()[0]
        self.manager = self.app.performance

    def sample(self, **extra):
        prior = self.manager.samples(self.job['id'])
        values = {'request_id': uid(), 'previous_id': prior[0]['id'] if prior else None,
                  'reads': 100, 'favorites': 8, 'likes': 0, 'observed_at': now(),
                  'source_note': '合成平台后台累计数据', 'note': '有读者希望补充安装步骤', **extra}
        return self.manager.add_sample(self.job['id'], values)

    def review(self, sample=None, **extra):
        sample = sample or self.sample()
        return self.manager.create({'request_id': uid(), 'job_id': self.job['id'],
                                    'sample_id': sample['id'], 'profile_id': self.profile['id'], **extra})

    def output(self, **extra):
        return json.dumps({'summary': '目前只知道单篇累计表现，需继续观察。', 'limitations': ['没有同期对照，不能判断因果。'],
                           'observations': [{'content': '已录入100次阅读和8次收藏。', 'evidence_ids': ['reads', 'favorites']}],
                           'candidates': [{'content': '实践教程可尝试补充安装步骤，并继续收集反馈验证。', 'reason': '读者提出了步骤需求。',
                                           'keywords': '教程', 'evidence_ids': ['feedback']}], **extra}, ensure_ascii=False)

    def complete(self, review=None):
        review = review or self.review()
        with patch.object(self.app, 'generate_draft', return_value=self.output()):
            self.assertTrue(self.manager.tick())
        return self.manager.get(review['id'])

    def test_missing_zero_append_history_and_replay_do_not_revert(self):
        original = self.app.post(self.post['id'])
        first = self.sample()
        self.assertIsNone(first['metrics']['comments'])
        self.assertEqual(first['metrics']['likes'], 0)
        second = self.sample(reads=110)
        replay = self.manager.add_sample(self.job['id'], {**first['metrics'], 'request_id': first['id'],
                    'observed_at': first['observed_at'], 'source_note': first['source_note'], 'note': first['note']})
        self.assertEqual(replay['id'], first['id'])
        self.assertEqual(self.app.job_detail(self.job['id'])['reads'], 110)
        self.assertEqual(len(self.manager.samples(self.job['id'])), 2)
        self.assertEqual(self.manager.samples(self.job['id'])[0]['id'], second['id'])
        self.assertEqual(self.app.post(self.post['id']), original)
        self.assertEqual(self.app.job_detail(self.job['id'])['attempts'], 0)

    def test_validation_and_conflict_do_not_write(self):
        for invalid in (-1, 1.5, True, '100', 1000000001):
            with self.assertRaises(Problem): self.sample(reads=invalid)
        with self.assertRaises(Problem): self.sample(observed_at='2099-01-01T00:00:00Z')
        with self.assertRaises(Problem): self.sample(reads=None, favorites=None, likes=None)
        self.assertEqual(self.manager.samples(self.job['id']), [])
        self.sample()
        with self.assertRaises(Problem) as e: self.sample(previous_id=None)
        self.assertEqual(e.exception.status, 409)
        with self.app.db.connect() as db: db.execute("UPDATE jobs SET status='draft' WHERE id=?", (self.job['id'],))
        with self.assertRaises(Problem): self.sample()

    def test_frozen_input_profile_isolation_and_original_preserved(self):
        s = self.sample()
        other = profiles.save(self.app, {'name': '其他画像'})
        with self.assertRaises(Problem): self.review(s, profile_id=other['id'])
        r = self.review(s)
        self.app.save_post({'version': 1, 'body': '后来编辑的正文'}, self.post['id'])
        self.sample(reads=200)
        profiles.save(self.app, {'version': 1, 'goal': '新目标'}, self.profile['id'])
        r = self.complete(r)
        self.assertEqual(r['context']['sample']['metrics']['reads'], 100)
        self.assertEqual(r['context']['sample']['publication']['body'], '可复现的三步操作。')
        self.assertTrue(r['data_changed'] and r['profile_changed'])
        with self.assertRaises(Problem): self.manager.propose(r['id'], {'candidate_id': '0'})
        m = self.manager.propose(r['id'], {'candidate_id': '0', 'ack_changed': True})
        self.assertEqual(m['profile_id'], self.profile['id'])
        self.assertEqual(memories.listing(self.app, other['id']), [])

    def test_candidate_confirmation_recall_and_provenance(self):
        r = self.complete()
        self.assertEqual(memories.listing(self.app, self.profile['id']), [])
        m = self.manager.propose(r['id'], {'candidate_id': '0'})
        self.assertEqual(m['status'], 'pending')
        self.assertEqual(m['source']['sample_id'], r['sample_id'])
        self.assertEqual(memories.recall(self.app, self.profile['id'], '教程', 'wechat'), [])
        self.assertEqual(self.manager.propose(r['id'], {'candidate_id': '0'})['id'], m['id'])
        memories.update(self.app, self.profile['id'], m['id'], {'action': 'confirm', 'version': 1})
        self.assertEqual(len(memories.recall(self.app, self.profile['id'], '教程', 'wechat')), 1)
        self.assertEqual(memories.recall(self.app, self.profile['id'], '教程', 'devto'), [])
        self.assertEqual(self.manager.get(r['id'])['candidate_memories']['0']['status'], 'accepted')
        self.assertEqual(self.app.post(self.post['id']), self.post)

    def test_invalid_model_evidence_rejected_and_retry_idempotence(self):
        r = self.review()
        with patch.object(self.app, 'generate_draft', return_value=self.output(observations=[{'content': '编造', 'evidence_ids': ['made_up']}])):
            self.manager.tick()
        self.assertEqual(self.manager.get(r['id'])['status'], 'failed')
        self.assertEqual(memories.listing(self.app, self.profile['id']), [])
        self.manager.retry(r['id'], {})
        with self.assertRaises(Problem): self.manager.retry(r['id'], {})
        self.assertEqual(self.complete(r)['status'], 'ready')

    def test_stop_does_not_accept_late_model_result_and_reopen_preserves(self):
        r = self.review()
        def stopped(*args, **kwargs):
            self.manager.interrupt()
            return self.output()
        with patch.object(self.app, 'generate_draft', side_effect=stopped): self.manager.tick()
        self.assertEqual(self.manager.get(r['id'])['status'], 'interrupted')
        self.app.close()
        reopened = Studio(self.tmp.name)
        try:
            self.assertEqual(len(reopened.performance.samples(self.job['id'])), 1)
            self.assertEqual(reopened.performance.get(r['id'])['status'], 'interrupted')
        finally: reopened.close()

    def test_legacy_metrics_and_devto_sync_feed_snapshots(self):
        self.app.job_action(self.job['id'], 'metrics', {'reads': 12, 'likes': 2, 'comments': 0, 'note': '手动旧入口'})
        self.assertEqual(self.manager.samples(self.job['id'])[0]['source'], 'manual')
        register(self.app, self.post['id'], {'version': 1, 'channels': ['devto'], 'confirmed': True})
        dev = next(j for j in self.app.jobs() if j['channel_id'] == 'devto')
        with self.app.db.connect() as db: db.execute("UPDATE jobs SET external_id='123' WHERE id=?", (dev['id'],))
        with patch('studio.adapters.devto_metrics', return_value={'published_at': now(), 'page_views_count': 10,
                  'positive_reactions_count': 0, 'comments_count': 2, 'url': 'https://dev.to/example/test'}):
            self.app.sync_metrics(dev['id'])
        sample = self.manager.samples(dev['id'])[0]
        self.assertEqual(sample['source'], 'devto')
        self.assertIsNone(sample['metrics']['favorites'])
        with patch('studio.adapters.devto_metrics', return_value={'published_at': now(), 'url': 'https://dev.to/example/test'}):
            result = self.app.sync_metrics(dev['id'])
        self.assertFalse(result['metrics_saved'])
        self.assertEqual(len(self.manager.samples(dev['id'])), 1)
        self.assertEqual(self.app.job_detail(dev['id'])['reads'], 10)

    def test_duplicate_create_and_stale_sample(self):
        s = self.sample(); request = uid()
        first = self.review(s, request_id=request)
        self.assertEqual(self.review(s, request_id=request)['id'], first['id'])
        with self.assertRaises(Problem): self.review(s)
        self.complete(first); self.sample()
        with self.assertRaises(Problem): self.review(s)

    def test_api_guard_and_asset(self):
        from studio.server import Server
        server = Server(('127.0.0.1', 0), self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + '/performance.js') as response: self.assertEqual(response.status, 200)
        request = urllib.request.Request(base + '/api/performance/reviews', data=b'{}', headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e: opener.open(request)
        self.assertEqual(e.exception.code, 403)
        with opener.open(base + '/api/performance') as response:
            self.assertEqual(len(json.load(response)['publications']), 1)

    def test_model_route_records_actual_request_and_prompt_uses_frozen_inputs(self):
        self.app.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://example.invalid/v1', 'AI_MODEL': 'synthetic-review-model'})
        r = self.review()
        with patch('studio.adapters.ai_draft', return_value=self.output()) as model:
            self.manager.tick()
        call = model.call_args
        material = json.loads(call.args[0]['body'])
        self.assertEqual(material['sample']['metrics']['reads'], 100)
        self.assertNotIn('model_route', material)
        result = self.manager.get(r['id'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['model_provenance']['calls'][0]['requested_model'], 'synthetic-review-model')
        self.assertNotIn('model_route', result['context'])

    def test_legacy_import_and_invalid_timestamp_do_not_block_startup(self):
        with self.app.db.connect() as db:
            db.execute('UPDATE jobs SET metrics_at=?,reads=42 WHERE id=?', (now(), self.job['id']))
        self.app.close()
        reopened = Studio(self.tmp.name)
        try:
            self.assertEqual(reopened.performance.samples(self.job['id'])[0]['source'], 'legacy')
            with reopened.db.connect() as db:
                db.execute('DELETE FROM performance_samples')
                db.execute("UPDATE jobs SET metrics_at='old-invalid-date'")
        finally: reopened.close()
        reopened = Studio(self.tmp.name)
        try: self.assertTrue(reopened.performance.overview()['publications'][0]['legacy_unmigrated'])
        finally: reopened.close()


if __name__ == '__main__':
    unittest.main()
