import tempfile, threading, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from studio.zhihu_images import upload
from studio.adapters import DeliveryError
from studio.service import Studio, public_article_url

class ZhihuTests(unittest.TestCase):
    def test_upload_fixed_host_waits_for_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.png';path.write_bytes(b'fixture image')
            w=Mock();w.stopped=threading.Event()
            w.call.side_effect=[{'upload_file':{'state':0,'image_id':'123','object_key':'v2_abc'},'upload_token':{'access_id':'id','access_key':'key','access_token':'token'}}, {'status':'processing'}, {'status':'completed','original_hash':'hash123'}, {'ready':True,'reason':'loaded','width':2,'height':2}]
            with patch('studio.zhihu_images.build_opener') as opener:
                self.assertEqual(upload(w,path),'https://pic4.zhimg.com/hash123')
                self.assertEqual(opener.return_value.open.call_count,1)
                self.assertEqual(w.call.call_args.args,('image-verify',{'url':'https://pic4.zhimg.com/hash123','diagnostics':True}))
                req=opener.return_value.open.call_args.args[0]
                self.assertEqual(req.full_url,'https://zhihu-pics-upload.zhimg.com/v2_abc')
                self.assertEqual(req.method,'PUT')
                self.assertEqual(req.data,b'fixture image')
                self.assertTrue(req.get_header('Authorization').startswith('OSS id:'))
            self.assertEqual(w.upload_diagnostics['status'],'success')
    def test_cached_image_no_upload_and_bad_grant_no_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.gif';path.write_bytes(b'gif fixture')
            w=Mock();w.stopped=threading.Event()
            with patch('studio.zhihu_images.build_opener') as opener:
                w.call.side_effect=[{'upload_file':{'state':1,'image_id':'123'}},{'status':'completed','original_hash':'cached'}, {'ready':True,'reason':'loaded','width':2,'height':2}]
                self.assertEqual(upload(w,path),'https://pic4.zhimg.com/cached.gif')
                self.assertEqual(w.call.call_args.args,('image-verify',{'url':'https://pic4.zhimg.com/cached.gif','diagnostics':True}))
                opener.assert_not_called()
                for key in ('../secret','https://evil.example/x','/x','x?secret'):
                    w.call.side_effect=[{'upload_file':{'state':0,'image_id':'123','object_key':key},'upload_token':{}}]
                    with self.assertRaises(DeliveryError):upload(w,path)
                opener.assert_not_called()
    def test_registered_not_falsely_verified_and_incomplete_receipt_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Studio(tmp)
            b=Mock();account={'id':'account','name':'Fixture'}
            b.accounts.return_value={'zhihu':account};b.account_states.return_value={};b.diagnostics.return_value={};b.login.return_value=account
            app.publishing_bridge.browser=b
            app.save_settings({'channels':[{'id':'zhihu','enabled':True}]})
            row=next(c for c in app.channels() if c['id']=='zhihu')
            self.assertTrue(row['delivery_implemented']);self.assertFalse(row['auto_send'])
            p=app.save_post({'title':'测试','body':'合成正文','stage':'ready'})
            b.save.return_value={'id':'123','url':'https://zhuanlan.zhihu.com/p/123/edit','incomplete':True,'reason':'知乎核对封面：封面不一致','observed':{'stage':'核对封面','cover_matches':False}}
            j=app.enqueue(p['id'],{'version':1,'channels':['zhihu'],'ack_warnings':True})['ids'][0]
            app.tick();job=app.job_detail(j)
            self.assertEqual((job['status'],job['external_id']),('uncertain','123'))
            self.assertEqual(job['snapshot']['desktop_receipt']['account_id'],'account')
            self.assertIn('知乎核对封面',job['note'])
            self.assertEqual(job['snapshot']['desktop_receipt']['draft_readback'],{'stage':'核对封面','cover_matches':False})
            self.assertEqual(job['public_url'],'')
            self.assertFalse(next(c for c in app.channels() if c['id']=='zhihu')['auto_send'])
            b.check_publication.return_value={'state':'published','draft_id':'123','article_id':'123','account_id':'account','url':'https://zhuanlan.zhihu.com/p/123'}
            app.publication_sync.tick()
            job=app.job_detail(j)
            self.assertEqual(job['status'],'published');self.assertEqual(job['public_url'],'https://zhuanlan.zhihu.com/p/123')
            self.assertEqual(b.save.call_count,1)
            app.close()
    def test_editor_url_is_not_a_public_receipt(self):
        self.assertEqual(public_article_url({'channel_id':'zhihu','url':'https://zhuanlan.zhihu.com/p/123/edit'}),'')

if __name__=='__main__':unittest.main()
