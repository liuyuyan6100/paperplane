import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from pathlib import Path
from studio.service import Studio,Problem
from studio.db import uid,dumps
from studio import profiles
from studio.reviews import repair_body,clean_result
from studio.adapters import DeliveryError
from studio.server import Server
from studio.migration import backup_bundle
from tools.restore_studio import restore

def advice(quote='原句需要简洁。',replacement='原句可以简洁。',category='clarity',verify=False):
    return {'summary':'合成审稿建议，未联网核实。','issues':[{'category':category,'priority':'warning','quote':quote,'replacement':replacement,'reason':'表达可更直接','needs_verification':verify}]}

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.app=Studio(self.root/'data');self.addCleanup(self.app.close)
        self.profile=profiles.save(self.app,{'name':'合成画像','style':'第一人称自然表达'})
        self.post=self.app.save_post({'title':'合成审稿文章','body':'原句需要简洁。\n\n保留另外一段。','stage':'draft','profile_id':self.profile['id']})
    def create(self,post=None,request=None):
        p=post or self.post
        return self.app.reviews.create({'post_id':p['id'],'post_version':p['version'],'request_id':request or uid()})
    def complete(self,review=None,result=None):
        r=review or self.create();self.app.generate_draft=lambda *a,**k:dumps(result or advice())
        self.assertTrue(self.app.reviews.tick());return self.app.reviews.get(r['id'])
    def apply(self,review,**extra):
        return self.app.reviews.apply(review['id'],{'post_version':review['post_version'],'issues':[{'id':'0','verified':False}],**extra})
    def test_snapshot_and_idempotent_create(self):
        request=uid();r=self.create(request=request);self.assertEqual(self.create(request=request)['id'],r['id'])
        with self.assertRaises(Problem):self.create()
        updated=profiles.save(self.app,{**self.profile,'style':'新的口吻'},self.profile['id'])
        r=self.app.reviews.get(r['id']);self.assertTrue(r['profile_changed']);self.assertEqual(r['context']['profile']['style'],'第一人称自然表达')
        with self.assertRaises(Problem):self.app.reviews.create({'request_id':uid(),'post_id':{},'post_version':1})
    def test_review_is_advice_not_automatic_adoption(self):
        r=self.complete();self.assertEqual(r['status'],'review');self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM jobs')['n'],0)
    def test_actual_service_structured_adapter_contract(self):
        r=self.create()
        with patch('studio.adapters.ai_draft',return_value=dumps(advice())) as transport:
            self.app.reviews.tick()
        self.assertEqual(self.app.reviews.get(r['id'])['status'],'review')
        self.assertTrue(transport.call_args.kwargs['structured'])
        plan=self.app.illustrations.create({'post_id':self.post['id'],'post_version':self.post['version']})
        self.app.illustrations.improve(plan['id'],{'version':plan['version']})
        with patch('studio.adapters.ai_draft',return_value=dumps([{'title':'合成计划','text':'','prompt':'合成场景','slot':'body','aspect':'landscape'}])) as transport:
            self.app.illustrations.tick()
        self.assertEqual(self.app.illustrations.get(plan['id'])['status'],'review')
        self.assertTrue(transport.call_args.kwargs['structured'])
    def test_partial_apply_preserves_rest_and_is_idempotent(self):
        result=advice();result['issues'].append({**result['issues'][0],'quote':'保留另外一段。','replacement':'另一段也改写。'})
        r=self.complete(result=result);self.apply(r);post=self.app.post(self.post['id'])
        self.assertEqual(post['body'],'原句可以简洁。\n\n保留另外一段。')
        self.assertTrue(self.apply(r)['already_applied']);self.assertEqual(self.app.post(post['id'])['version'],post['version'])
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM revisions WHERE post_id=?',(post['id'],))['n'],2)
    def test_stale_post_cannot_be_overwritten(self):
        r=self.complete();p=self.app.save_post({**self.post,'body':'人工新文字'},self.post['id'])
        with self.assertRaises(Problem) as caught:self.apply(r,post_version=p['version'])
        self.assertEqual(caught.exception.status,409);self.assertEqual(self.app.post(p['id'])['body'],'人工新文字')
    def test_profile_change_requires_acknowledgement(self):
        r=self.complete();profiles.save(self.app,{**self.profile,'style':'新口吻'},self.profile['id'])
        with self.assertRaises(Problem):self.apply(r)
        self.apply(r,ack_profile=True)
    def test_fact_suggestions_need_manual_verification(self):
        r=self.complete(result=advice(category='evidence'))
        self.assertTrue(r['result']['issues'][0]['needs_verification'])
        with self.assertRaises(Problem):self.apply(r)
        self.apply(r,issues=[{'id':'0','verified':True}])
    def test_ambiguous_code_and_links_are_advice_only(self):
        for body,quote in [('原句原句','原句'),('```python\nprint(1)\n```','print(1)'),('阅读[原句](https://example.com)','原句'),('![图](/media/'+uid()+'.png)','图'),('    print(1)','print(1)')]:
            result=clean_result(dumps(advice(quote=quote)),body)
            self.assertFalse(result['issues'][0]['applicable'])
        result=clean_result(dumps(advice(replacement='访问https://fake.example')),self.post['body'])
        self.assertFalse(result['issues'][0]['applicable'])
    def test_overlapping_patches_roll_back(self):
        result=advice();result['issues'].append({**result['issues'][0],'quote':'需要简洁','replacement':'已经简洁'})
        r=self.complete(result=result)
        with self.assertRaises(Problem):self.apply(r,issues=[{'id':'0'},{'id':'1'}])
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
    def test_malformed_result_fails_and_retry_uses_snapshot(self):
        r=self.create();self.app.generate_draft=lambda *a,**k:'not json';self.app.reviews.tick()
        self.assertEqual(self.app.reviews.get(r['id'])['status'],'failed');self.assertFalse(self.app.reviews.tick())
        p=self.app.save_post({**self.post,'body':'人工最新稿'},self.post['id'])
        self.app.reviews.retry(r['id'],{});r=self.complete(r);self.assertTrue(r['source_changed']);self.assertEqual(r['context']['post']['body'],self.post['body'])
        self.assertEqual(self.app.post(p['id'])['body'],'人工最新稿')
    def test_interrupted_late_result_does_not_resurrect(self):
        r=self.create()
        def generate(*a,**k):self.app.reviews.interrupt();return dumps(advice())
        self.app.generate_draft=generate;self.app.reviews.tick();r=self.app.reviews.get(r['id'])
        self.assertEqual(r['status'],'interrupted');self.assertEqual(r['result'],{})
    def test_format_only_blank_lines_and_preserves_protected_content(self):
        source='第一段\n\n\n\n第二段  \n硬换行\n\n```python\na=1\n\n\n\nprint(a)\n```\n\n<pre>\nx\n\n\n\ny\n</pre>\n\n![图](/media/a.png)\n'
        fixed=repair_body(source)
        self.assertEqual(fixed,source.replace('第一段\n\n\n\n第二段','第一段\n\n\n第二段'))
        for body in ('- 列表\n\n\n\n下一段','上段\n\n\n\n    print(1)','```\nx\n\n\n\n未闭合代码','a\r\n\r\n\r\n\r\nb'):
            self.assertEqual(repair_body(body),body if '\r\n' not in body else 'a\r\n\r\n\r\nb')
    def test_format_backup_one_latest_and_undo(self):
        p=self.app.save_post({**self.post,'body':'a\n\n\n\nb'},self.post['id'])
        repaired=self.app.reviews.format(p['id'],{'post_version':p['version']});self.assertTrue(repaired['repaired'])
        p=repaired['post'];same=self.app.reviews.format(p['id'],{'post_version':p['version']});self.assertFalse(same['repaired'])
        self.assertEqual(same['post']['version'],p['version'])
        p=self.app.save_post({**p,'body':p['body']+'\n\n\n\nc'},p['id']);p=self.app.reviews.format(p['id'],{'post_version':p['version']})['post']
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM format_backups')['n'],1)
        restored=self.app.reviews.undo(p['id'],{'post_version':p['version']})['post'];self.assertTrue(restored['body'].endswith('\n\n\n\nc'))
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM format_backups')['n'],0)
    def test_undo_preserves_later_manual_edit(self):
        p=self.app.save_post({**self.post,'body':'a\n\n\n\nb'},self.post['id']);p=self.app.reviews.format(p['id'],{'post_version':p['version']})['post']
        p=self.app.save_post({**p,'body':p['body']+'新文字'},p['id'])
        with self.assertRaises(Problem):self.app.reviews.undo(p['id'],{'post_version':p['version']})
        self.assertTrue(self.app.post(p['id'])['body'].endswith('新文字'))
    def test_publish_automatically_repairs_saved_snapshot(self):
        p=self.app.save_post({**self.post,'body':'a\n\n\n\nb','stage':'ready'},self.post['id'])
        result=self.app.enqueue(p['id'],{'version':p['version'],'channels':['local'],'ack_warnings':True})
        job=self.app.job_detail(result['ids'][0]);self.assertEqual(job['snapshot']['body'],'a\n\n\nb')
        self.assertEqual(job['version'],p['version']+1);self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM format_backups')['n'],1)
    def test_restore_interrupts_reviews_and_retains_backup(self):
        r=self.create();archive=self.root/'backup.zip';archive.write_bytes(backup_bundle(self.app));restore(archive,self.root/'restore')
        restored=Studio(self.root/'restore');self.addCleanup(restored.close)
        self.assertEqual(restored.reviews.get(r['id'])['status'],'interrupted');self.assertFalse(restored.reviews.tick())
    def test_worker_routes_write_token_and_format_check(self):
        self.app.generate_draft=lambda *a,**k:dumps(advice());r=self.create();self.app.start_worker()
        self.assertTrue(self.app.workflows.worker.is_alive())
        server=Server(('127.0.0.1',0),self.app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base='http://127.0.0.1:'+str(server.server_port)
        with urllib.request.urlopen(base+'/api/reviews') as response:self.assertEqual(json.load(response)['reviews'][0]['id'],r['id'])
        with urllib.request.urlopen(base+'/reviews.js') as response:self.assertIn(b'drawReviews',response.read())
        request=urllib.request.Request(base+'/api/reviews',data=b'{}',headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code,403)
        with urllib.request.urlopen(base+'/api/session') as response:token=json.load(response)['token']
        p=self.app.save_post({**self.post,'body':'a\n\n\n\nb','stage':'ready'},self.post['id'])
        request=urllib.request.Request(base+'/api/posts/'+p['id']+'/check',data=dumps({'channels':['local'],'version':p['version']}).encode(),headers={'Content-Type':'application/json','X-Studio-Token':token})
        with urllib.request.urlopen(request) as response:result=json.load(response)
        self.assertTrue(result['repaired']);self.assertEqual(result['post']['version'],p['version']+1)
