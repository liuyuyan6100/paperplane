import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio.adapters import DeliveryError
from studio.service import Studio, Problem
from desktop_publishing import DesktopPublishingBrowser


class DesktopPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name))
        self.addCleanup(self.app.close)
        self.bridge = self.app.publishing_bridge
        self.browser = Mock()
        self.account = {'id':'account1','name':'测试账号'}
        self.browser.accounts.return_value = {'csdn':self.account,'juejin':None}
        self.browser.login.return_value = self.account
        self.browser.upload.return_value = 'https://img-blog.csdnimg.cn/fixture.png'
        self.browser.save.return_value = {'id':'12345','url':'https://editor.csdn.net/md?articleId=12345'}
        self.bridge.browser = self.browser
        self.media = '/media/'+'a'*32+'.png'
        (Path(self.temp.name)/'media').mkdir(exist_ok=True)
        (Path(self.temp.name)/self.media.lstrip('/')).write_bytes(b'fixture')
        self.post = self.app.save_post({'title':'内置平台送稿测试','body':'正文\n\n![图]('+self.media+')',
                                      'cover':self.media,'stage':'ready','summary':'摘要'})

    def queue(self):
        return self.app.enqueue(self.post['id'],{'version':1,'channels':['csdn'],'publish_public':True})['ids'][0]

    def test_new_delivery_uploads_once_and_records_draft(self):
        ident=self.queue()
        self.assertEqual(self.app.job_detail(ident)['snapshot']['delivery_config']['mode'],'desktop-publishing')
        with patch.object(self.bridge,'send',side_effect=AssertionError('extension must not run')):
            self.app.tick()
        job=self.app.job_detail(ident)
        self.assertEqual(job['status'],'draft')
        self.assertEqual(job['channel_kind'],'bridge')
        self.browser.upload.assert_called_once()
        self.browser.open.assert_called_once_with('csdn',self.browser.save.return_value['url'])
        article=self.browser.save.call_args.args[1]
        self.assertNotIn('/media/',article['markdown'])
        self.assertEqual(article['cover'],self.browser.upload.return_value)
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
        channel=next(c for c in self.app.channels() if c['id']=='csdn')
        self.assertTrue(channel['auto_send'])
        self.assertFalse(channel['publish'])
        with self.assertRaises(Problem):self.queue()

    def test_image_failure_never_submits_body(self):
        self.browser.upload.side_effect=DeliveryError('上传失败')
        ident=self.queue();self.app.tick()
        self.assertEqual(self.app.job_detail(ident)['status'],'failed')
        self.browser.save.assert_not_called()
        self.assertIn('尚未生成',self.bridge.progress)
        self.assertIn('送稿失败',self.browser.notify.call_args.args[1])

    def test_juejin_summary_fits_publish_form_without_changing_source(self):
        post=self.app.save_post({'version':1,'summary':'摘要'*105},self.post['id'])
        self.browser.save.return_value={'id':'12345','url':'https://juejin.cn/editor/drafts/12345'}
        ident=self.app.enqueue(post['id'],{'version':post['version'],'channels':['juejin'],'ack_warnings':True})['ids'][0]
        self.app.tick()
        self.assertEqual(self.app.job_detail(ident)['status'],'draft')
        sent=self.browser.save.call_args.args[1]['summary']
        self.assertLessEqual(len(sent.encode('utf-16-le'))//2,100)
        self.assertEqual(self.app.post(post['id'])['summary'],'摘要'*105)

    def test_successful_save_is_not_failed_by_window_navigation(self):
        self.browser.open.side_effect=RuntimeError('window closed')
        ident=self.queue();self.app.tick()
        job=self.app.job_detail(ident)
        self.assertEqual(job['status'],'draft')
        self.assertTrue(job['url'])
        self.assertIn('窗口打开失败',job['note'])

    def test_account_change_before_upload_blocks(self):
        ident=self.queue()
        self.browser.login.return_value={'id':'different','name':'另一个账号'}
        self.app.tick()
        self.assertEqual(self.app.job_detail(ident)['status'],'failed')
        self.browser.upload.assert_not_called()

    def test_invalid_receipt_is_uncertain_and_never_retried(self):
        self.browser.save.return_value={'id':'12345','url':'https://evil.example/draft'}
        ident=self.queue();self.app.tick();self.app.tick()
        self.assertEqual(self.app.job_detail(ident)['status'],'uncertain')
        self.browser.save.assert_called_once()

    def test_missing_desktop_cannot_fall_back_to_external_extension(self):
        ident=self.queue();self.bridge.browser=None
        self.app.tick()
        self.assertEqual(self.app.job_detail(ident)['status'],'failed')
        self.browser.save.assert_not_called()

    def test_old_manual_job_is_not_automatically_sent(self):
        self.bridge.browser=None
        ident=self.app.enqueue(self.post['id'],{'version':1,'channels':['csdn'],'ack_warnings':True})['ids'][0]
        self.bridge.browser=self.browser
        self.app.tick()
        self.assertEqual(self.app.job_detail(ident)['status'],'manual')
        self.browser.save.assert_not_called()

    def test_busy_or_cross_platform_open_rejected(self):
        self.bridge.serial.acquire()
        try:
            with self.assertRaises(Problem):self.bridge.launch('csdn')
        finally:self.bridge.serial.release()
        with self.assertRaises(Problem):self.bridge.launch('unsupported-platform')

    def test_cnblogs_identity_uses_hidden_page_after_editor_redirect(self):
        transport=DesktopPublishingBrowser()
        window=transport.windows['cnblogs']
        window.window=Mock();window.window.get_current_url.return_value='https://www.cnblogs.com/'
        hidden=window.identity_window=Mock()
        hidden.home='https://home.cnblogs.com/'
        hidden.window.get_current_url.return_value='https://account.cnblogs.com/signin'
        hidden.call.return_value=self.account
        self.assertEqual(window.call('identity'),self.account)
        hidden._navigate.assert_called_once_with('https://home.cnblogs.com/')
        window.window.load_url.assert_not_called()
        hidden.call.assert_called_once_with('identity',None)

    def test_explicit_preflight_failure_is_safe_but_never_auto_retried(self):
        transport=DesktopPublishingBrowser()
        window=transport.windows['segmentfault']=Mock()
        error=DeliveryError('missing session')
        error.submission_not_started=True
        window.call.side_effect=[self.account,error]
        with self.assertRaises(DeliveryError) as caught:transport.save('segmentfault',{},self.account)
        self.assertFalse(caught.exception.uncertain)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(window.call.call_count,2)

    def test_transport_does_not_treat_failed_submission_as_safe_retry(self):
        transport=DesktopPublishingBrowser()
        window=transport.windows['csdn']=Mock()
        window.call.side_effect=[self.account,DeliveryError('timeout')]
        with self.assertRaises(DeliveryError) as err:transport.save('csdn',{},self.account)
        self.assertTrue(err.exception.uncertain)


if __name__=='__main__':unittest.main()
