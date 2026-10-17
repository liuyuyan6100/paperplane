import tempfile
import unittest
from unittest.mock import Mock

from studio.service import Studio, public_article_url
from studio.publishing_bridge import valid_url
from studio.adapters import DeliveryError
from studio.publication_sync import eligible


class Cto51Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = Studio(temp.name)
        self.addCleanup(self.app.close)
        self.browser = Mock()
        self.browser.accounts.return_value = {'51cto': {'id': 'tester', 'name': 'Tester'}}
        self.browser.account_states.return_value = {}
        self.browser.diagnostics.return_value = {}
        self.browser.login.return_value = self.browser.accounts()['51cto']
        self.browser.save.return_value = {'id': '123', 'url': 'https://blog.51cto.com/blogger/draft/123'}
        self.app.publishing_bridge.browser = self.browser
        self.app.save_settings({'channels': [{'id': '51cto', 'enabled': True}]})
        self.post = self.app.save_post({'title': '隔离测试', 'body': '```python\nprint(1)\n```', 'stage': 'ready'})

    def channel(self):
        return next(c for c in self.app.channels() if c['id'] == '51cto')

    def send(self):
        ident = self.app.enqueue(self.post['id'], {'version': 1, 'channels': ['51cto'], 'ack_warnings': True})['ids'][0]
        self.app.tick()
        return self.app.job_detail(ident)

    def test_draft_is_not_publication_and_keeps_original(self):
        self.assertFalse(self.channel()['auto_send'])
        job = self.send()
        self.assertEqual(job['status'], 'draft')
        self.assertEqual(job['external_id'], '123')
        self.assertEqual(job['public_url'], '')
        self.assertTrue(self.channel()['auto_send'])
        self.assertFalse(eligible(job))
        self.assertEqual(self.app.post(self.post['id'])['body'], self.post['body'])
        self.app.publication_sync.tick()
        self.browser.check_publication.assert_not_called()

    def test_readback_failure_keeps_receipt_without_replay(self):
        self.browser.save.return_value['incomplete'] = True
        job = self.send()
        self.assertEqual((job['status'], job['external_id']), ('uncertain', '123'))
        self.assertFalse(self.channel()['auto_send'])
        self.app.tick()
        self.assertEqual(self.browser.save.call_count, 1)

    def test_timeout_does_not_retry(self):
        self.browser.save.side_effect = DeliveryError('待核对', uncertain=True)
        self.assertEqual(self.send()['status'], 'uncertain')
        self.app.tick()
        self.assertEqual(self.browser.save.call_count, 1)

    def test_receipt_urls_are_strict(self):
        self.assertTrue(valid_url('https://blog.51cto.com/blogger/draft/123', '51cto'))
        for url in ('https://blog.51cto.com.evil.test/blogger/draft/123',
                    'https://blog.51cto.com/blogger/draft/123?redirect=evil',
                    'https://blog.51cto.com/blogger/draft/0',
                    'https://blog.51cto.com:444/blogger/draft/123'):
            self.assertFalse(valid_url(url, '51cto'))
        self.assertEqual(public_article_url({'channel_id':'51cto','url':'https://blog.51cto.com/blogger/draft/123'}), '')
        url = 'https://blog.51cto.com/tester/123'
        self.assertEqual(public_article_url({'channel_id':'51cto','url':url}), url)


if __name__ == '__main__':
    unittest.main()
