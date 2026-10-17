import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path
from studio.service import Studio, Problem
from studio.adapters import DeliveryError
from studio.db import dumps
from studio.draft_reconciliation import reconcile


class DraftReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Studio(self.temp.name);self.addCleanup(self.app.close)
        self.account={'id':'3919523','name':'Fixture'}
        self.browser=Mock()
        self.browser.accounts.return_value={'oschina':self.account}
        self.browser.account_states.return_value={'oschina':'verified'}
        self.browser.login.return_value=self.account
        self.browser.upload.return_value='https://static.oschina.net/uploads/test.png'
        self.url='https://my.oschina.net/u/3919523/blog/ai-write/draft/3327815'
        self.browser.save.return_value={'id':'3327815','url':self.url,'incomplete':True}
        self.browser.read_draft.return_value={'id':'3327815','url':self.url}
        self.app.publishing_bridge.browser=self.browser
        self.app.save_settings({'channels':[{'id':'oschina','enabled':True}]})
        self.media='/media/'+'a'*32+'.png'
        (Path(self.temp.name)/'media').mkdir(exist_ok=True)
        (Path(self.temp.name)/'media'/Path(self.media).name).write_bytes(b'fixture')
        self.post=self.app.save_post({'title':'Unique draft fixture','body':'Body\n\n![image]('+self.media+')','stage':'ready'})
        self.ident=self.app.enqueue(self.post['id'],{'version':1,'channels':['oschina'],'ack_warnings':True})['ids'][0]

    def test_submission_persisted_before_save_and_read_only_recovery(self):
        def save(*args):
            stored=self.app.job_detail(self.ident)['snapshot']['desktop_submission']
            self.assertNotIn('/media/',stored['markdown'])
            self.assertEqual(stored['account_id'],self.account['id'])
            return {'id':'3327815','url':self.url,'incomplete':True}
        self.browser.save.side_effect=save
        self.app.tick()
        self.assertEqual(self.app.job_detail(self.ident)['status'],'uncertain')
        reconcile(self.app,self.ident,{})
        job=self.app.job_detail(self.ident)
        self.assertEqual(job['status'],'draft');self.assertEqual(job['attempts'],1)
        self.browser.save.assert_called_once();self.browser.upload.assert_called_once()
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
        self.assertEqual(next(p for p in self.app.channels() if p['id']=='oschina')['verification']['draft']['status'],'verified')

    def test_unknown_submission_retains_exact_snapshot(self):
        self.browser.save.side_effect=DeliveryError('lost response',uncertain=True)
        self.app.tick()
        job=self.app.job_detail(self.ident)
        self.assertEqual(job['status'],'uncertain')
        self.assertIn('desktop_submission',job['snapshot'])
        reconcile(self.app,self.ident,{'draft_id':'3327815'})
        self.assertEqual(self.app.job_detail(self.ident)['status'],'draft')
        self.browser.save.assert_called_once()

    def test_mismatch_and_failed_read_never_promote_or_retry(self):
        self.app.tick()
        for receipt in ({'id':'3327815','url':self.url,'incomplete':True},
                        {'id':'3327815','url':self.url.replace('3919523','123')},
                        {'id':'9999','url':self.url}):
            with self.subTest(receipt=receipt):
                self.browser.read_draft.return_value=receipt
                with self.assertRaises(DeliveryError):reconcile(self.app,self.ident,{})
                self.assertEqual(self.app.job_detail(self.ident)['status'],'uncertain')
        self.browser.save.assert_called_once()

    def test_concurrent_confirmation_is_preserved(self):
        self.app.tick()
        def read(*args):
            self.app.job_action(self.ident,'confirm',{})
            return {'id':'3327815','url':self.url}
        self.browser.read_draft.side_effect=read
        with self.assertRaises(Problem):reconcile(self.app,self.ident,{})
        self.assertEqual(self.app.job_detail(self.ident)['status'],'published')

    def test_old_job_missing_upload_evidence_is_not_guessed(self):
        self.app.tick()
        job=self.app.job_detail(self.ident);job['snapshot'].pop('desktop_submission')
        with self.app.db.connect() as db:
            db.execute('UPDATE jobs SET snapshot=? WHERE id=?',(dumps(job['snapshot']),self.ident))
        with self.assertRaises(Problem):reconcile(self.app,self.ident,{})
        self.browser.read_draft.assert_not_called()
        self.assertEqual(self.app.job_detail(self.ident)['status'],'uncertain')

    def test_new_channels_reconcile_existing_number_without_writing(self):
        self.app.tick()
        for platform,url in [('cnblogs','https://i.cnblogs.com/articles/edit;postId=3327815'),
                             ('toutiao','https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=3327815'),
                             ('bilibili-article','https://member.bilibili.com/platform/upload/text/edit?aid=3327815'),
                             ('zhihu','https://zhuanlan.zhihu.com/p/3327815/edit')]:
            with self.subTest(platform=platform):
                with self.app.db.connect() as db:
                    db.execute("UPDATE jobs SET channel_id=?,status='uncertain',url=? WHERE id=?",(platform,url,self.ident))
                self.browser.read_draft.return_value={'id':'3327815','url':url}
                reconcile(self.app,self.ident,{})
                self.assertEqual(self.app.job_detail(self.ident)['status'],'draft')
                self.assertEqual(self.app.job_detail(self.ident)['attempts'],1)
        self.browser.save.assert_called_once()
        self.browser.upload.assert_called_once()

    def test_readback_diagnostics_do_not_promote_or_retry(self):
        self.app.tick()
        self.browser.read_draft.return_value={'id':'3327815','url':self.url,'incomplete':True,
            'reason':'Title differs','observed':{'title_matches':False,'content':{'type':'string','actual_length':13}}}
        with self.assertRaises(Problem) as caught:reconcile(self.app,self.ident,{})
        self.assertEqual(caught.exception.details['draft_readback']['title_matches'],False)
        self.assertEqual(self.app.job_detail(self.ident)['status'],'uncertain')
        self.browser.save.assert_called_once()

    def test_replacing_known_draft_id_is_rejected(self):
        self.app.tick()
        with self.assertRaises(Problem):reconcile(self.app,self.ident,{'draft_id':'555'})
        self.browser.read_draft.assert_not_called()

    def zhihu_existing_job(self):
        self.app.tick()
        job=self.app.job_detail(self.ident)
        ident='2091799896336684899'
        url=f'https://zhuanlan.zhihu.com/p/{ident}/edit'
        reference='https://pic-private.zhihu.com/v2-fixture~render:1440w.png?protocol=v2&auth_key=PRIVATE_FIXTURE&expiration=4102444800'
        expected=job['snapshot']['desktop_submission']
        expected['content']=f'<p>Fixture</p><img src="{reference}">'
        expected['cover']=reference
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET channel_id='zhihu',external_id=?,url=?,snapshot=? WHERE id=?",(ident,url,dumps(job['snapshot']),self.ident))
        self.browser.accounts.return_value={'zhihu':self.account}
        return ident,url,expected

    def test_zhihu_exact_original_snapshot_is_read_without_resending(self):
        ident,url,expected=self.zhihu_existing_job()
        self.browser.read_draft.return_value={'id':ident,'url':url}
        reconcile(self.app,self.ident,{})
        self.browser.read_draft.assert_called_once_with('zhihu',ident,expected)
        job=self.app.job_detail(self.ident)
        self.assertEqual((job['status'],job['external_id'],job['attempts']),('draft',ident,1))
        self.assertEqual(job['snapshot']['desktop_submission'],expected)
        self.assertNotIn('PRIVATE_FIXTURE',dumps(job['snapshot']['desktop_receipt']))
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
        self.browser.save.assert_called_once();self.browser.upload.assert_called_once()

    def test_zhihu_incomplete_preserves_original_state_and_safe_diagnostics(self):
        ident,url,_=self.zhihu_existing_job()
        before=self.app.job_detail(self.ident)
        diagnostic={'stage':'核对原对象私有草稿状态','owner_state':'published'}
        self.browser.read_draft.return_value={'id':ident,'url':url,'incomplete':True,'reason':'原对象未确认草稿状态','observed':diagnostic}
        with self.assertRaises(Problem) as caught:reconcile(self.app,self.ident,{})
        self.assertEqual(caught.exception.details,{'draft_readback':diagnostic})
        after=self.app.job_detail(self.ident)
        for key in ('status','attempts','snapshot','external_id','url'):self.assertEqual(after[key],before[key])
        self.browser.save.assert_called_once();self.browser.upload.assert_called_once()

    def test_zhihu_wrong_receipt_and_concurrent_update_never_promote(self):
        ident,url,_=self.zhihu_existing_job()
        for receipt in ({'id':'2091799896336684800','url':url},{'id':ident,'url':url.replace('/edit','')}):
            self.browser.read_draft.return_value=receipt
            with self.assertRaises(DeliveryError):reconcile(self.app,self.ident,{})
            self.assertEqual(self.app.job_detail(self.ident)['status'],'uncertain')
        def concurrent_read(*args):
            self.app.job_action(self.ident,'confirm',{})
            return {'id':ident,'url':url}
        self.browser.read_draft.side_effect=concurrent_read
        with self.assertRaises(Problem):reconcile(self.app,self.ident,{})
        self.assertEqual(self.app.job_detail(self.ident)['status'],'published')
        self.browser.save.assert_called_once();self.browser.upload.assert_called_once()

if __name__=='__main__':unittest.main()
