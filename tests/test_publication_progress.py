import tempfile
import unittest
from studio.service import Studio, Problem
from studio.publication_progress import register


class PublicationProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Studio(self.tmp.name);self.addCleanup(self.app.close)
        self.post=self.app.save_post({'title':'已公开文章','body':'正文','stage':'ready'})

    def register(self,channels,**extra):
        return register(self.app,self.post['id'],{'version':self.post['version'],'channels':channels,'confirmed':True,**extra})

    def progress(self):
        return next(p for p in self.app.state()['posts'] if p['id']==self.post['id'])['publication_progress']

    def test_registration_is_idempotent_and_never_queues_or_changes_post(self):
        original=self.app.post(self.post['id'])
        self.register(['wechat','csdn','juejin','devto','xiaohongshu'])
        self.register(['csdn','wechat'])
        self.assertEqual(len(self.app.jobs()),5)
        self.assertTrue(all(j['status']=='published' and not j['url'] and j['attempts']==0 for j in self.app.jobs()))
        self.assertTrue(self.progress()['complete'])
        self.assertEqual(original,self.app.post(self.post['id']))

    def test_partial_publication_keeps_other_platforms_pending(self):
        self.app.repurpose.create({'post_id':self.post['id'],'post_version':1,'platforms':['csdn','juejin','devto','xiaohongshu']})
        self.register(['wechat'])
        self.assertEqual(self.progress()['published'],['wechat'])
        self.assertEqual(len(self.progress()['pending']),4)
        self.assertFalse(self.progress()['complete'])
        self.register(['csdn'])
        self.assertEqual(len(self.progress()['pending']),3)

    def test_saving_does_not_erase_publication_or_dispatch_again(self):
        self.register(['wechat'])
        self.app.save_post({'version':1,'body':'本地修改'},self.post['id'])
        self.assertEqual(self.progress()['published'],['wechat'])
        self.assertEqual(len(self.app.jobs()),1)

    def test_invalid_confirmation_version_and_channels_write_nothing(self):
        for args in ({'confirmed':False},{'version':99},{'channels':['not-real']},{'channels':['local']},{'channels':['csdn','csdn']}):
            with self.assertRaises(Problem):
                register(self.app,self.post['id'],{'version':1,'channels':['wechat'],'confirmed':True,**args})
        self.assertEqual(self.app.jobs(),[])

    def test_queued_delivery_blocks_registration_atomically(self):
        self.app.enqueue(self.post['id'],{'version':1,'channels':['csdn'],'ack_warnings':True})
        with self.assertRaises(Problem):self.register(['wechat','csdn'])
        self.assertEqual(len(self.app.jobs()),1)
        self.assertEqual(self.app.jobs()[0]['status'],'queued')

    def test_register_link_and_update_without_delivery(self):
        url='https://blog.csdn.net/test/article/details/123456'
        self.register(['csdn'],url=url)
        job=self.app.jobs()[0]
        self.assertEqual(job['public_url'],url)
        new_url='https://blog.csdn.net/test/article/details/234567'
        self.app.job_action(job['id'],'link',{'url':new_url})
        updated=self.app.jobs()[0]
        self.assertEqual(updated['public_url'],new_url)
        self.assertEqual(updated['status'],'published')
        self.assertEqual(updated['attempts'],0)
        self.assertEqual(len(self.app.jobs()),1)

    def test_wrong_platform_and_draft_links_rejected(self):
        for url in ['javascript:alert(1)','https://editor.csdn.net/md/?articleId=123', 'https://juejin.cn/post/123']:
            with self.assertRaises(Problem):self.register(['csdn'],url=url)
        self.assertEqual(self.app.jobs(),[])
        self.register(['csdn'])
        job=self.app.jobs()[0]
        with self.assertRaises(Problem):self.app.job_action(job['id'],'link',{'url':'https://editor.csdn.net/md/'})
        self.assertEqual(self.app.jobs()[0]['public_url'],'')

    def test_link_action_cannot_confirm_or_restore_a_job(self):
        self.app.enqueue(self.post['id'],{'version':1,'channels':['csdn'],'ack_warnings':True})
        job=self.app.jobs()[0]
        with self.assertRaises(Problem):self.app.job_action(job['id'],'link',{'url':'https://blog.csdn.net/test/article/details/123'})
        self.assertEqual(self.app.jobs()[0]['status'],'queued')
        self.app.job_action(job['id'],'cancel',{})
        self.app.job_action(job['id'],'delete',{})
        with self.assertRaises(Problem):self.app.job_action(job['id'],'link',{'url':'https://blog.csdn.net/test/article/details/123'})
