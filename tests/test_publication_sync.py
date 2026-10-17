"""Publication reconciliation tests use isolated SQLite and a read-only fake browser."""
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from unittest.mock import Mock, patch

from studio.adapters import DeliveryError
from studio.db import dumps
from studio.server import Server
from studio.service import Problem, Studio


class PublicationSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(self.temp.name)
        self.addCleanup(self.app.close)
        self.post = self.app.save_post({'title': '核对测试', 'body': '原始正文', 'summary': '摘要', 'stage': 'ready'})
        self.job_id = self.app.enqueue(self.post['id'], {'version': 1, 'channels': ['juejin'], 'ack_warnings': True})['ids'][0]
        snapshot = self.app.job_detail(self.job_id)['snapshot']
        snapshot['delivery_config'] = {'mode': 'desktop-publishing', 'account_id': 'account1'}
        self.snapshot = dumps(snapshot)
        self.draft_url = 'https://juejin.cn/editor/drafts/123456'
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET snapshot=?,status='draft',external_id='123456',url=?,attempts=1 WHERE id=?", (self.snapshot, self.draft_url, self.job_id))
        self.browser = Mock()
        self.browser.accounts.return_value = {}
        self.browser.account_states.return_value = {}
        self.browser.diagnostics.return_value = {}
        self.app.publishing_bridge.browser = self.browser
        self.sync = self.app.publication_sync
        self.result = {'state': 'published', 'draft_id': '123456', 'article_id': '987654',
                       'url': 'https://juejin.cn/post/987654', 'account_id': 'account1'}
        self.browser.check_publication.return_value = dict(self.result)

    def check(self):
        return self.app.db.one('SELECT * FROM publication_checks WHERE job_id=?', (self.job_id,))

    def due(self):
        with self.app.db.connect() as db:
            db.execute("UPDATE publication_checks SET next_check_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (self.job_id,))

    def test_exact_receipt_updates_original_job_without_delivery_or_post_changes(self):
        original_post = self.app.post(self.post['id'])
        self.assertTrue(self.sync.tick())
        self.assertFalse(self.sync.tick())
        job = self.app.job_detail(self.job_id)
        self.assertEqual((job['status'], job['url'], job['external_id'], job['attempts']),
                         ('published', self.result['url'], '123456', 1))
        self.assertEqual(dumps(job['snapshot']), self.snapshot)
        self.assertEqual(self.app.post(self.post['id']), original_post)
        self.assertEqual(self.app.db.rows('SELECT * FROM publications'), [])
        self.assertEqual(job['publication_sync']['receipt']['draft_url'], self.draft_url)
        self.assertEqual(job['publication_sync']['receipt']['account_id'], 'account1')
        self.assertFalse(job['publication_sync']['can_check'])
        self.assertEqual(len(self.app.jobs()), 1)
        self.assertEqual(self.app.jobs()[0]['publication_sync']['state'], 'published')
        self.browser.check_publication.assert_called_once_with('juejin', '123456', 'account1')
        self.browser.save.assert_not_called()
        self.browser.upload.assert_not_called()
        self.browser.open.assert_not_called()

    def test_reviewing_and_draft_never_count_as_published(self):
        for state in ('reviewing', 'draft'):
            self.browser.check_publication.return_value = {**self.result, 'state': state}
            self.sync.tick()
            job = self.app.job_detail(self.job_id)
            self.assertEqual(job['status'], 'draft')
            self.assertEqual(job['url'], self.draft_url)
            self.assertEqual(job['publication_sync']['state'], state)
            self.assertEqual(job['publication_sync']['receipt'], {})
            self.assertEqual(job['publication_sync']['failures'], 0)
            self.due()
        self.browser.check_publication.return_value = self.result
        self.sync.tick()
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'published')

    def test_mismatched_identity_and_bad_public_urls_are_not_receipts(self):
        invalid = [
            {'draft_id': '999'}, {'account_id': 'different'}, {'account_id': ''},
            {'article_id': ''}, {'article_id': '111'},
            {'url': 'https://juejin.cn/editor/drafts/987654'},
            {'url': 'https://other.example/post/987654'},
            {'url': 'http://juejin.cn/post/987654'},
            {'url': 'https://user@juejin.cn/post/987654'},
            {'url': 'https://juejin.cn/post/987654?draft=true'},
            {'url': 'https://juejin.cn:443/post/987654'},
        ]
        for change in invalid:
            with self.subTest(change=change):
                self.browser.check_publication.return_value = {**self.result, **change}
                self.sync.tick()
                self.assertEqual(self.app.job_detail(self.job_id)['status'], 'draft')
                self.assertEqual(self.check()['state'], 'unknown')
                self.assertEqual(json.loads(self.check()['receipt_json']), {})
                self.due()

    def test_unknown_failure_is_visible_with_backoff_and_manual_rate_limit(self):
        self.browser.check_publication.side_effect = DeliveryError('请核对登录状态')
        self.sync.tick()
        first = self.check()
        self.assertEqual(first['state'], 'unknown')
        self.assertIn('登录', first['message'])
        self.assertEqual(first['failures'], 1)
        self.assertGreaterEqual((datetime.fromisoformat(first['next_check_at']) - datetime.fromisoformat(first['checked_at'])).total_seconds(), 60)
        self.assertFalse(self.sync.tick())
        self.sync.request(self.job_id)
        requested = self.check()
        self.assertGreaterEqual((datetime.fromisoformat(requested['next_check_at']) - datetime.fromisoformat(first['checked_at'])).total_seconds(), 30)
        self.assertFalse(self.sync.tick())
        self.browser.check_publication.assert_called_once()
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'draft')

    def test_ready_delivery_takes_priority_over_background_readback(self):
        other = self.app.save_post({'title': '另一篇', 'body': '正文', 'summary': '摘要', 'stage': 'ready'})
        other_id = self.app.enqueue(other['id'], {'version': 1, 'channels': ['csdn'], 'ack_warnings': True})['ids'][0]
        for state in ('queued', 'running'):
            with self.app.db.connect() as db:
                db.execute('UPDATE jobs SET status=? WHERE id=?', (state, other_id))
            self.assertFalse(self.sync.tick())
        self.browser.check_publication.assert_not_called()

    def test_background_check_flag_is_set_only_while_reading(self):
        self.assertFalse(self.sync.checking)
        def observe(*args):
            self.assertTrue(self.sync.checking)
            raise DeliveryError('临时错误')
        self.browser.check_publication.side_effect = observe
        self.sync.tick()
        self.assertFalse(self.sync.checking)
        self.assertTrue(self.app.publishing_bridge.serial.acquire(blocking=False))
        self.app.publishing_bridge.serial.release()

    def _hold_background_check(self):
        entered, release = threading.Event(), threading.Event()
        def check(*args):
            entered.set()
            if not release.wait(5): raise AssertionError('test check was not released')
            return {**self.result, 'state': 'draft'}
        self.browser.check_publication.side_effect = check
        thread = threading.Thread(target=self.sync.tick, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(2))
        return release, thread

    def test_send_waits_for_background_check_and_saves_exactly_once(self):
        bridge = self.app.publishing_bridge
        release, checker = self._hold_background_check()
        self.browser.login.return_value = {'id': 'account1', 'name': '测试账号'}
        self.browser.save.return_value = {'id': '7777', 'url': 'https://juejin.cn/editor/drafts/7777'}
        results, errors, finished = [], [], threading.Event()
        def send():
            try: results.append(bridge.send_desktop(json.loads(self.snapshot), 'juejin'))
            except Exception as exc: errors.append(exc)
            finally: finished.set()
        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        try:
            self.assertFalse(finished.wait(.1))
            self.browser.save.assert_not_called()
        finally:
            release.set()
            checker.join(3)
            sender.join(3)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]['status'], 'draft')
        self.assertEqual(results[0]['desktop_receipt']['account_id'], 'account1')
        self.browser.save.assert_called_once()
        self.assertFalse(self.sync.checking)
        self.assertFalse(bridge.busy)
        self.assertTrue(bridge.serial.acquire(blocking=False))
        bridge.serial.release()

    def test_launch_returns_while_checking_then_opens_and_releases_lock(self):
        bridge = self.app.publishing_bridge
        release, checker = self._hold_background_check()
        returned, opened = threading.Event(), threading.Event()
        results, errors = [], []
        self.browser.open.side_effect = lambda *args: opened.set()
        def launch():
            try: results.append(bridge.launch('juejin', self.job_id))
            except Exception as exc: errors.append(exc)
            finally: returned.set()
        caller = threading.Thread(target=launch, daemon=True)
        caller.start()
        try:
            self.assertTrue(returned.wait(.5))
            self.assertFalse(opened.is_set())
        finally:
            release.set()
            checker.join(3)
            caller.join(3)
        self.assertEqual(errors, [])
        self.assertTrue(results[0]['busy'])
        self.assertTrue(opened.wait(3))
        self.assertTrue(bridge.serial.acquire(timeout=2))
        try: self.assertFalse(bridge.busy)
        finally: bridge.serial.release()
        self.browser.open.assert_called_once_with('juejin', self.draft_url)

    def test_stopping_a_waiting_send_does_not_submit_or_release_another_owner_lock(self):
        bridge = self.app.publishing_bridge
        release, checker = self._hold_background_check()
        finished, errors = threading.Event(), []
        def send():
            try: bridge.send_desktop(json.loads(self.snapshot), 'juejin')
            except Exception as exc: errors.append(exc)
            finally: finished.set()
        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        try:
            self.assertFalse(finished.wait(.05))
            self.app.stop.set()
            self.assertTrue(finished.wait(1))
            self.assertTrue(bridge.serial.locked())
            self.browser.save.assert_not_called()
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], DeliveryError)
        finally:
            release.set()
            checker.join(3)
            sender.join(3)
        self.assertFalse(self.sync.checking)
        self.assertFalse(bridge.serial.locked())

    def test_shared_window_lock_does_not_block_or_open_a_window(self):
        bridge = self.app.publishing_bridge
        bridge.serial.acquire()
        try: self.assertFalse(self.sync.tick())
        finally: bridge.serial.release()
        self.browser.check_publication.assert_not_called()
        self.browser.login.assert_not_called()
        self.browser.open.assert_not_called()

    def test_concurrent_manual_confirmation_is_preserved(self):
        manual_url = 'https://juejin.cn/post/88888'
        def confirm(*args):
            self.app.job_action(self.job_id, 'confirm', {'url': manual_url})
            return self.result
        self.browser.check_publication.side_effect = confirm
        self.sync.tick()
        job = self.app.job_detail(self.job_id)
        self.assertEqual(job['status'], 'published')
        self.assertEqual(job['url'], manual_url)
        self.assertIn('用户确认', job['note'])
        self.assertEqual(job['publication_sync']['receipt'], {})

    def test_concurrent_recycle_and_purge_never_recreate_job_or_receipt(self):
        def purge(*args):
            self.app.job_action(self.job_id, 'delete', {})
            self.app.jobs_batch({'action': 'purge', 'ids': [self.job_id], 'confirmed': True})
            return self.result
        self.browser.check_publication.side_effect = purge
        self.sync.tick()
        self.assertEqual(self.app.jobs(), [])
        self.assertIsNone(self.check())

    def test_delete_restore_during_check_is_detected_even_with_same_timestamp(self):
        original = self.app.db.one('SELECT updated_at FROM jobs WHERE id=?', (self.job_id,))['updated_at']
        def recycle(*args):
            self.app.job_action(self.job_id, 'delete', {})
            self.app.job_action(self.job_id, 'restore', {})
            with self.app.db.connect() as db:
                db.execute('UPDATE jobs SET updated_at=? WHERE id=?', (original, self.job_id))
            return self.result
        self.browser.check_publication.side_effect = recycle
        self.sync.tick()
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'draft')
        self.assertEqual(self.check()['state'], 'unknown')
        self.assertEqual(json.loads(self.check()['receipt_json']), {})

    def test_delivery_worker_persists_actual_receipt_for_future_reconciliation(self):
        receipt = {'draft_id': '123456', 'draft_url': self.draft_url, 'account_id': 'actual'}
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='queued',url='',external_id='' WHERE id=?", (self.job_id,))
        result = {'status': 'draft', 'url': self.draft_url, 'external_id': '123456',
                  'note': '已送草稿', 'desktop_receipt': receipt}
        with patch.object(self.app.publishing_bridge, 'send_desktop', return_value=result):
            self.app.tick()
        job = self.app.job_detail(self.job_id)
        self.assertEqual(job['snapshot']['desktop_receipt'], receipt)
        self.browser.check_publication.return_value = {**self.result, 'account_id': 'actual'}
        self.sync.tick()
        self.browser.check_publication.assert_called_once_with('juejin', '123456', 'actual')
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'published')

    def test_actual_send_account_receipt_precedes_cached_enqueue_account(self):
        snapshot = json.loads(self.snapshot)
        snapshot['desktop_receipt'] = {'draft_id': '123456', 'draft_url': self.draft_url, 'account_id': 'actual'}
        with self.app.db.connect() as db:
            db.execute('UPDATE jobs SET snapshot=? WHERE id=?', (dumps(snapshot), self.job_id))
        self.browser.check_publication.return_value = {**self.result, 'account_id': 'actual'}
        self.sync.tick()
        self.browser.check_publication.assert_called_once_with('juejin', '123456', 'actual')
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'published')

    def test_old_unbound_draft_uses_author_verified_account_and_keeps_snapshot(self):
        snapshot = json.loads(self.snapshot)
        snapshot['delivery_config'].pop('account_id')
        with self.app.db.connect() as db:
            db.execute('UPDATE jobs SET snapshot=? WHERE id=?', (dumps(snapshot), self.job_id))
        self.sync.tick()
        self.browser.check_publication.assert_called_once_with('juejin', '123456', None)
        self.assertEqual(self.check()['account_id'], 'account1')
        self.assertEqual(self.app.job_detail(self.job_id)['snapshot'], snapshot)

    def test_manual_or_missing_draft_receipt_never_triggers_read_back(self):
        for updates in ("external_id=''", "external_id='abc'", "status='manual'", "status='queued'", "status='published'"):
            with self.app.db.connect() as db:
                db.execute("UPDATE jobs SET status='draft',external_id='123456' WHERE id=?", (self.job_id,))
                db.execute('UPDATE jobs SET ' + updates + ' WHERE id=?', (self.job_id,))
            self.assertFalse(self.sync.tick())
            with self.assertRaises(Problem): self.sync.request(self.job_id)
        self.browser.check_publication.assert_not_called()

    def test_csdn_article_url_must_match_verified_author_and_article_id(self):
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET channel_id='csdn',url='https://editor.csdn.net/md?articleId=123456' WHERE id=?", (self.job_id,))
        self.browser.check_publication.return_value = {**self.result, 'url': 'https://blog.csdn.net/other/article/details/987654'}
        self.sync.tick()
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'draft')
        self.due()
        self.browser.check_publication.return_value = {**self.result, 'url': 'https://blog.csdn.net/account1/article/details/987654'}
        self.sync.tick()
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'published')

    def test_post_endpoint_queues_without_waiting_or_calling_browser(self):
        server = Server(('127.0.0.1', 0), self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/jobs/{self.job_id}/publication-recheck',
                data=b'{}', headers={'Content-Type': 'application/json', 'X-Studio-Token': server.token})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=3) as response: result = json.load(response)
            self.assertTrue(result['queued'])
            self.browser.check_publication.assert_not_called()
            self.assertEqual(self.check()['state'], 'pending')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_restart_recovers_only_checking_state_without_replaying_delivery(self):
        self.sync.request(self.job_id)
        with self.app.db.connect() as db:
            db.execute("UPDATE publication_checks SET state='checking',next_check_at='2099-01-01T00:00:00+00:00'")
        with patch.object(self.sync, 'tick'):
            self.sync.start()
            self.sync.close()
        self.assertEqual(self.check()['state'], 'pending')
        self.assertEqual(self.app.job_detail(self.job_id)['status'], 'draft')
        self.browser.save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
