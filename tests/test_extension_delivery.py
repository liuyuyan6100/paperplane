import tempfile
import unittest
from unittest.mock import Mock
from studio.service import Studio, public_article_url
from studio.publishing_bridge import valid_url

DRAFTS={
 'toutiao':'https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=12345',
 'bilibili-article':'https://member.bilibili.com/platform/upload/text/edit?aid=12345',
 'jianshu':'https://www.jianshu.com/writer#/notebooks/1/notes/12345',
 'weibo':'https://card.weibo.com/article/v5/editor#/draft/12345',
}

class ExtensionDeliveryTests(unittest.TestCase):
    def test_new_platform_queue_uses_desktop_and_retains_uncertain_receipts(self):
        for platform,url in DRAFTS.items():
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as folder:
                app=Studio(folder)
                try:
                    browser=Mock();account={'id':'123','name':'Fixture'}
                    browser.accounts.return_value={platform:account};browser.login.return_value=account
                    browser.account_states.return_value={platform:'verified'}
                    browser.save.return_value={'id':'12345','url':url,'incomplete':True}
                    app.publishing_bridge.browser=browser
                    app.save_settings({'channels':[{'id':platform,'enabled':True}]})
                    post=app.save_post({'title':'Fixture','body':'Test body','stage':'ready'})
                    ident=app.enqueue(post['id'],{'version':1,'channels':[platform],'ack_warnings':True})['ids'][0]
                    self.assertEqual(app.job_detail(ident)['snapshot']['delivery_config']['mode'],'desktop-publishing')
                    app.tick();app.tick()
                    job=app.job_detail(ident)
                    self.assertEqual(job['status'],'uncertain');self.assertEqual(job['url'],url)
                    self.assertEqual(job['attempts'],1);browser.save.assert_called_once()
                    self.assertEqual(job['snapshot']['desktop_submission']['account_id'],'123')
                    self.assertEqual(next(p for p in app.channels() if p['id']==platform)['verification']['draft']['status'],'unverified')
                finally:app.close()

    def test_unknown_draft_platform_is_rejected(self):
        self.assertFalse(valid_url('https://dev.to/dashboard','devto'))
        self.assertFalse(valid_url('https://example.com/','unknown'))

    def test_new_draft_urls_never_become_public_links(self):
        for platform,url in DRAFTS.items():
            with self.subTest(platform=platform):
                self.assertTrue(valid_url(url,platform))
                self.assertEqual(public_article_url({'channel_id':platform,'url':url}),'')
                for changed in (url.replace('https://','http://'),url.replace('https://','https://evil.test/'),url+'&extra=1'):
                    self.assertFalse(valid_url(changed,platform))

    def test_old_manual_job_does_not_start_sending_on_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Studio(folder)
            try:
                app.save_settings({'channels':[{'id':'toutiao','enabled':True}]})
                post=app.save_post({'title':'Old job','body':'Body','stage':'ready'})
                ident=app.enqueue(post['id'],{'version':1,'channels':['toutiao'],'ack_warnings':True})['ids'][0]
                browser=Mock();browser.accounts.return_value={};browser.account_states.return_value={}
                app.publishing_bridge.browser=browser
                app.tick()
                self.assertEqual(app.job_detail(ident)['status'],'manual')
                browser.save.assert_not_called()
            finally:app.close()

if __name__=='__main__':unittest.main()
