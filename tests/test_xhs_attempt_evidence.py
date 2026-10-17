import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from studio.service import Studio, Problem
from studio.adapters import DeliveryError

class XhsAttemptEvidenceTests(unittest.TestCase):
    def test_uncertain_attempt_keeps_exact_ids_without_promotion_or_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Studio(tmp)
            try:
                image='/media/'+'a'*32+'.png'
                (Path(tmp)/'media').mkdir(exist_ok=True)
                (Path(tmp)/image.lstrip('/')).write_bytes(b'fixture')
                post=app.save_post({'title':'Fixture','body':'Body','stage':'ready','xhs':{'title':'测试草稿','caption':'Private fixture','photos':[image]}})
                browser=Mock();browser.account={'id':'fixture-account','name':'Fixture'};browser.diagnostics={}
                def save(*args):
                    browser.diagnostics={'account_id':'fixture-account','save_attempted':True,
                        'before':{'ids':['old-draft-123']},'after':{'ids':['new-draft-456','old-draft-123'],'new_ids':['new-draft-456']}}
                    raise DeliveryError('readback pending',uncertain=True)
                browser.save_draft.side_effect=save
                app.xhs_drafts.browser=browser
                job_id=app.enqueue(post['id'],{'version':1,'channels':['xiaohongshu'],'ack_warnings':True})['ids'][0]
                app.tick();app.tick()
                job=app.job_detail(job_id)
                self.assertEqual(job['status'],'uncertain')
                self.assertEqual(job['attempts'],1)
                self.assertEqual(job['snapshot']['xhs_attempt']['diagnostics']['after']['new_ids'],['new-draft-456'])
                browser.save_draft.assert_called_once()
            finally:app.close()

class XhsAttemptBoundariesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Studio(self.tmp.name);self.addCleanup(self.app.close)
        image='/media/'+'b'*32+'.png'
        (Path(self.tmp.name)/'media').mkdir(exist_ok=True)
        (Path(self.tmp.name)/image.lstrip('/')).write_bytes(b'fixture')
        post=self.app.save_post({'title':'Fixture','body':'Body','stage':'ready',
            'xhs':{'title':'测试草稿','caption':'Private fixture','photos':[image]}})
        self.browser=Mock();self.browser.account={'id':'known','name':'Known'};self.browser.diagnostics={}
        self.browser.save_draft.return_value={'draft_id':'draft-123456','account':{'id':'known','name':'Known'},'verified':True}
        self.xhs=self.app.xhs_drafts;self.xhs.browser=self.browser
        self.xhs.account=dict(self.browser.account);self.xhs.account_state='verified'
        self.job_id=self.app.enqueue(post['id'],{'version':1,'channels':['xiaohongshu'],'ack_warnings':True})['ids'][0]
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?",(self.job_id,))
        self.snapshot=self.app.job_detail(self.job_id)['snapshot']

    def evidence(self):
        self.browser.diagnostics={'account_id':'known','save_attempted':True,'result':'uncertain',
            'after':{'ids':['draft-123456'],'new_ids':['draft-123456']}}

    def test_failed_identity_does_not_reuse_verified_cache(self):
        self.browser.save_draft.side_effect=DeliveryError('identity page still loading')
        with self.assertRaises(DeliveryError):self.xhs.send(self.snapshot,self.job_id)
        self.assertEqual(self.xhs.account['id'],'known')
        self.assertFalse(self.xhs.status()['logged_in'])
        self.assertEqual(self.xhs.account_state,'unverified')
        self.assertFalse(self.xhs.busy);self.assertFalse(self.xhs.lock.locked())

    def test_diagnostic_failure_cannot_replace_verified_remote_receipt(self):
        def save(*args):
            self.evidence()
            return {'draft_id':'draft-123456','account':{'id':'known','name':'Known'},'verified':True}
        self.browser.save_draft.side_effect=save
        with patch.object(self.app.db,'connect',side_effect=RuntimeError('evidence storage unavailable')):
            result=self.xhs.send(self.snapshot,self.job_id)
        self.assertEqual(result['status'],'draft')
        self.assertFalse(self.xhs.busy);self.assertFalse(self.xhs.lock.locked())

    def test_diagnostic_failure_preserves_unknown_exception_and_no_retry(self):
        original=DeliveryError('remote result unknown',uncertain=True)
        def save(*args):self.evidence();raise original
        self.browser.save_draft.side_effect=save
        with patch.object(self.app.db,'connect',side_effect=RuntimeError('evidence storage unavailable')):
            with self.assertRaises(DeliveryError) as caught:self.xhs.send(self.snapshot,self.job_id)
        self.assertIs(caught.exception,original);self.assertTrue(caught.exception.uncertain)
        self.assertFalse(self.xhs.busy);self.assertFalse(self.xhs.lock.locked())
        self.browser.save_draft.assert_called_once()

    def test_attempt_evidence_only_updates_running_job_and_snapshot(self):
        def save(*args):
            self.evidence()
            with self.app.db.connect() as db:db.execute("UPDATE jobs SET status='uncertain' WHERE id=?",(self.job_id,))
            raise DeliveryError('remote result unknown',uncertain=True)
        self.browser.save_draft.side_effect=save
        with self.assertRaises(DeliveryError):self.xhs.send(self.snapshot,self.job_id)
        self.assertNotIn('xhs_attempt',self.snapshot)
        self.assertNotIn('xhs_attempt',self.app.job_detail(self.job_id)['snapshot'])
        self.assertEqual(self.app.job_detail(self.job_id)['status'],'uncertain')
        with self.assertRaises(Problem):self.app.job_action(self.job_id,'retry',{})

    def test_title_adaptation_stops_when_job_no_longer_running(self):
        self.snapshot['xhs']['title']='一个超过二十个字的小红书标题需要在发送之前自动进行适配'
        def adapt(*args):
            with self.app.db.connect() as db:db.execute("UPDATE jobs SET status='canceled' WHERE id=?",(self.job_id,))
            return '短标题'
        with patch('studio.xhs_drafts.adapt_xhs_title',side_effect=adapt):
            with self.assertRaises(DeliveryError) as caught:self.xhs.send(self.snapshot,self.job_id)
        self.assertFalse(caught.exception.uncertain)
        self.browser.save_draft.assert_not_called()
        self.assertNotIn('xhs_title_adaptation',self.app.job_detail(self.job_id)['snapshot'])

    def test_opened_editor_receipt_does_not_prove_current_identity(self):
        with self.app.db.connect() as db:db.execute("UPDATE jobs SET status='uncertain' WHERE id=?",(self.job_id,))
        self.browser.open_draft.return_value={'id':'known','name':'Known'}
        self.browser.verify_identity.side_effect=DeliveryError('editor has no current account information')
        self.xhs.launch(self.job_id);self.xhs.thread.join(5)
        self.assertFalse(self.xhs.thread.is_alive());self.assertFalse(self.xhs.status()['logged_in'])
        self.assertEqual(self.xhs.account['id'],'known');self.assertFalse(self.xhs.lock.locked())

    def test_malformed_identity_response_is_unverified_not_signed_out(self):
        from studio.channel_checks import check
        self.browser.verify_identity.return_value={}
        self.assertEqual(check(self.app,'xiaohongshu')['status'],'needs_login')
        self.assertEqual(self.xhs.account['id'],'known');self.assertEqual(self.xhs.account_state,'unverified')

if __name__=='__main__':unittest.main()
