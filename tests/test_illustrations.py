import base64
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studio import image_provider, profiles
from studio.adapters import DeliveryError
from studio.db import dumps, uid
from studio.illustrations import IllustrationManager
from studio.migration import backup_bundle
from studio.server import Server
from studio.service import Problem, Studio
from tools.restore_studio import restore


def png(color='navy'):
    output=io.BytesIO()
    Image.new('RGB',(256,384),color).save(output,format='PNG')
    return output.getvalue()


class IllustrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.app=Studio(self.root/'data')
        self.addCleanup(self.app.close)
        self.reference=self.app.upload({'data':base64.b64encode(png()).decode(),'title':'合成参考'})['url']
        self.profile=profiles.save(self.app,{'name':'合成画像','visual_style':'暖纸墨线','visual_references':[self.reference]})
        self.post=self.app.save_post({'title':'合成教学正文','body':'提供了具体步骤，没有真实操作结果。','stage':'draft','profile_id':self.profile['id']})
        image_provider.save(self.app.config,{'base_url':'https://image.example/v1','api_key':'test-only-secret','model':'image-test','references':'edits'})

    def plan(self,post=None):
        p=post or self.post
        return self.app.illustrations.create({'post_id':p['id'],'post_version':p['version']})

    def generate(self,plan,index=0,**extra):
        return self.app.illustrations.generate(plan['id'],{'version':plan['version'],'page_index':index,'request_id':uid(),**extra})

    def complete(self):
        with patch('studio.image_provider.generate',return_value=png('teal')):
            self.assertTrue(self.app.illustrations.tick())

    def test_profile_snapshot_and_actual_reference_are_preserved(self):
        plan=self.plan()
        profiles.save(self.app,{**self.profile,'visual_style':'新的风格'},self.profile['id'])
        plan=self.app.illustrations.get(plan['id'])
        self.assertTrue(plan['profile_changed'])
        self.assertEqual(plan['context']['profile']['visual_style'],'暖纸墨线')
        self.assertEqual(plan['context']['references'][0]['url'],self.reference)
        for refs in ([{}], ['/media/../../private.json'], [self.reference]*5):
            with self.assertRaises(Problem):
                profiles.save(self.app,{**self.profile,'visual_references':refs},self.profile['id'])

    def test_missing_image_configuration_keeps_manual_plan(self):
        plan=self.plan()
        self.app.config.update({'IMAGE_MODEL':''})
        with self.assertRaises(Problem):
            self.generate(plan)
        self.assertEqual(self.app.illustrations.get(plan['id'])['status'],'review')
        self.assertEqual(self.app.post(self.post['id'])['version'],1)

    def test_image_configuration_is_independent_and_secret_not_returned(self):
        result=image_provider.public_settings(self.app.config)
        self.assertNotIn('test-only-secret',dumps(result))
        with self.assertRaises(Problem):
            image_provider.save(self.app.config,{'base_url':'https://different.example/v1'})
        self.app.config.update({'AI_API_KEY':'different-text-secret','AI_MODEL':'text-model'})
        result=image_provider.save(self.app.config,{'model':'new-image-model'})
        self.assertEqual(self.app.config.get('AI_MODEL'),'text-model')
        self.assertEqual(result['model'],'new-image-model')

    def test_idempotent_generation_and_page_overlap_protection(self):
        plan=self.plan();request=uid()
        values={'version':1,'page_index':0,'request_id':request}
        self.app.illustrations.generate(plan['id'],values)
        second=self.app.illustrations.generate(plan['id'],values)
        self.assertEqual(len(second['candidates']),1)
        with self.assertRaises(Problem):self.generate(plan,0)
        self.generate(plan,1)
        self.assertEqual(len(self.app.illustrations.get(plan['id'])['candidates']),2)
        self.assertNotIn('test-only-secret',dumps(second))

    def test_permission_failure_does_not_adopt_or_retry_automatically(self):
        plan=self.generate(self.plan())
        with patch('studio.image_provider.generate',side_effect=DeliveryError('当前图片密钥或分组未获生图权限')) as provider:
            self.assertTrue(self.app.illustrations.tick())
            self.assertFalse(self.app.illustrations.tick())
            self.assertEqual(provider.call_count,1)
        failed=self.app.illustrations.get(plan['id'])['candidates'][0]
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(self.app.post(self.post['id'])['cover'],'')
        self.generate(plan);self.complete()
        rows=self.app.illustrations.get(plan['id'])['candidates']
        self.assertEqual([r['status'] for r in rows],['failed','review'])
        self.assertEqual(self.app.jobs(),[])

    def test_provider_configuration_change_prevents_wrong_destination(self):
        plan=self.generate(self.plan())
        self.app.config.update({'IMAGE_BASE_URL':'https://changed.example/v1'})
        with patch('studio.image_provider.generate') as provider:
            self.app.illustrations.tick();provider.assert_not_called()
        self.assertEqual(self.app.illustrations.get(plan['id'])['candidates'][0]['status'],'failed')

    def test_late_result_after_stop_does_not_create_media(self):
        plan=self.generate(self.plan())
        count=len(self.app.db.rows('SELECT id FROM assets'))
        def interrupted(*args):
            self.app.illustrations.interrupt();return png()
        with patch('studio.image_provider.generate',side_effect=interrupted):self.app.illustrations.tick()
        candidate=self.app.illustrations.get(plan['id'])['candidates'][0]
        self.assertEqual(candidate['status'],'interrupted')
        self.assertEqual(candidate['image_url'],'')
        self.assertEqual(len(self.app.db.rows('SELECT id FROM assets')),count)

    def test_changed_reference_prevents_model_call(self):
        plan=self.generate(self.plan())
        (self.app.db.data_dir/'media'/Path(self.reference).name).write_bytes(png('red'))
        with patch('studio.image_provider.generate') as provider:self.app.illustrations.tick();provider.assert_not_called()
        self.assertEqual(self.app.illustrations.get(plan['id'])['candidates'][0]['status'],'failed')

    def test_stale_edit_and_stale_adoption_are_explicit(self):
        plan=self.generate(self.plan());self.complete()
        current=self.app.save_post({'version':1,'body':'人工修改后的正文'},self.post['id'])
        candidate=self.app.illustrations.get(plan['id'])['candidates'][0]
        with self.assertRaises(Problem):self.app.illustrations.adopt(plan['id'],{'candidate_id':candidate['id'],'post_version':current['version']})
        self.app.illustrations.adopt(plan['id'],{'candidate_id':candidate['id'],'post_version':current['version'],'ack_changes':True})
        self.assertEqual(self.app.post(self.post['id'])['body'],'人工修改后的正文')
        edited=self.app.illustrations.edit(plan['id'],{'version':1,'pages':plan['pages']})
        with self.assertRaises(Problem):self.app.illustrations.edit(plan['id'],{'version':1,'pages':plan['pages']})
        self.assertEqual(edited['version'],2)

    def test_adoption_is_atomic_idempotent_and_keeps_old_files(self):
        plan=self.generate(self.plan());self.complete()
        c=self.app.illustrations.get(plan['id'])['candidates'][0]
        values={'candidate_id':c['id'],'post_version':1}
        self.app.illustrations.adopt(plan['id'],values)
        version=self.app.post(self.post['id'])['version']
        self.app.illustrations.adopt(plan['id'],values)
        self.assertEqual(self.app.post(self.post['id'])['version'],version)
        self.generate(plan);self.complete()
        second=self.app.illustrations.get(plan['id'])['candidates'][-1]
        self.app.illustrations.adopt(plan['id'],{'candidate_id':second['id'],'post_version':version})
        self.assertTrue((self.app.db.data_dir/'media'/Path(c['image_url']).name).is_file())
        self.assertEqual(self.app.post(self.post['id'])['cover'],second['image_url'])
        self.assertEqual(self.app.jobs(),[])

    def test_xhs_order_is_page_order_and_manual_images_survive(self):
        self.post=self.app.save_post({'version':1,'xhs':{'title':'合成图集','caption':'合成配文','photos':[self.reference],
            'pages':[{'title':f'第{i}页','text':'准确文案','visual':'示例场景'} for i in range(3)]}},self.post['id'])
        plan=self.plan()
        for index in (2,0,1):
            self.generate(plan,index);self.complete()
            latest=self.app.illustrations.get(plan['id']);candidate=latest['candidates'][-1]
            self.app.illustrations.adopt(plan['id'],{'candidate_id':candidate['id'],'post_version':latest['post_version']})
        result=self.app.illustrations.get(plan['id'])
        ordered=[next(c['image_url'] for c in result['candidates'] if c['page_index']==i) for i in range(3)]
        self.assertEqual(self.app.post(self.post['id'])['xhs']['photos'],ordered+[self.reference])
        with Image.open(self.app.db.data_dir/'media'/Path(ordered[0]).name) as im:self.assertEqual(im.size,(1080,1440))

    def test_restore_interrupts_pending_images_and_keeps_references(self):
        plan=self.generate(self.plan())
        archive=self.root/'backup.zip';archive.write_bytes(backup_bundle(self.app))
        restore(archive,self.root/'restored')
        app=Studio(self.root/'restored');self.addCleanup(app.close)
        restored=app.illustrations.get(plan['id'])
        self.assertEqual(restored['candidates'][0]['status'],'interrupted')
        self.assertTrue((app.db.data_dir/'media'/Path(self.reference).name).is_file())

    def test_ai_plan_uses_snapshot_and_recover_does_not_auto_run(self):
        plan=self.plan();self.app.illustrations.improve(plan['id'],{'version':1})
        self.app.generate_draft=lambda *a,**kw:dumps(plan['pages'])
        self.app.illustrations.tick()
        self.assertEqual(self.app.illustrations.get(plan['id'])['version'],2)
        self.generate(self.app.illustrations.get(plan['id']))
        IllustrationManager(self.app)
        self.assertFalse(self.app.illustrations.tick())
        self.assertEqual(self.app.illustrations.get(plan['id'])['candidates'][0]['status'],'interrupted')

    def test_archive_preserves_article_candidates_adoptions_and_media(self):
        plan=self.generate(self.plan());self.complete()
        candidate=self.app.illustrations.get(plan['id'])['candidates'][0]
        self.app.illustrations.adopt(plan['id'],{'candidate_id':candidate['id'],'post_version':1})
        before=self.app.illustrations.get(plan['id'])
        post=self.app.post(self.post['id'])
        revisions=self.app.db.rows('SELECT * FROM revisions')
        media={p.name:p.read_bytes() for p in (self.app.db.data_dir/'media').iterdir() if p.is_file()}
        archived=self.app.illustrations.archive(plan['id'],{'version':plan['version'],'archived':True})
        self.assertTrue(archived['archived_at'])
        self.assertEqual({**archived,'archived_at':''},before)
        self.assertEqual(self.app.illustrations.listing(),[])
        self.assertEqual([p['id'] for p in self.app.illustrations.listing(archived=True)],[plan['id']])
        self.assertEqual(self.app.illustrations.archive(plan['id'],{'version':plan['version'],'archived':True}),archived)
        restored=self.app.illustrations.archive(plan['id'],{'version':plan['version'],'archived':False})
        self.assertEqual(restored,before)
        self.assertEqual(self.app.illustrations.listing(archived=True),[])
        self.assertEqual(self.app.post(post['id']),post)
        self.assertEqual(self.app.db.rows('SELECT * FROM revisions'),revisions)
        self.assertEqual({p.name:p.read_bytes() for p in (self.app.db.data_dir/'media').iterdir() if p.is_file()},media)
        self.assertEqual(self.app.jobs(),[])

    def test_archived_plan_blocks_mutations_until_restored(self):
        plan=self.generate(self.plan());self.complete()
        before=self.app.illustrations.get(plan['id']);c=before['candidates'][0]
        self.app.illustrations.archive(plan['id'],{'version':1,'archived':True})
        for action,values in [('edit',{'version':1,'pages':before['pages']}),('improve',{'version':1}),
                              ('generate',{'version':1,'page_index':0,'request_id':uid()}),
                              ('generate',{'version':1,'page_index':0,'request_id':c['id']}),
                              ('adopt',{'candidate_id':c['id'],'post_version':1})]:
            with self.subTest(action=action),self.assertRaises(Problem) as caught:
                getattr(self.app.illustrations,action)(plan['id'],values)
            self.assertEqual(caught.exception.status,409)
        self.assertFalse(self.app.illustrations.tick())
        restored=self.app.illustrations.archive(plan['id'],{'version':1,'archived':False})
        self.assertFalse(restored['candidates'][0]['plan_changed'])
        self.app.illustrations.adopt(plan['id'],{'candidate_id':c['id'],'post_version':1})
        self.assertEqual(self.app.post(self.post['id'])['cover'],c['image_url'])

    def test_archive_rejects_busy_stale_and_invalid_requests(self):
        plan=self.plan()
        for values in ({'version':1,'archived':'true'},{'version':True,'archived':True},{'version':0,'archived':True}):
            with self.assertRaises(Problem):self.app.illustrations.archive(plan['id'],values)
        self.app.illustrations.improve(plan['id'],{'version':1})
        with self.assertRaises(Problem):self.app.illustrations.archive(plan['id'],{'version':1,'archived':True})
        self.app.illustrations.interrupt()
        plan=self.app.illustrations.edit(plan['id'],{'version':1,'pages':plan['pages']})
        with self.assertRaises(Problem):self.app.illustrations.archive(plan['id'],{'version':1,'archived':True})
        self.generate(plan)
        with self.assertRaises(Problem):self.app.illustrations.archive(plan['id'],{'version':2,'archived':True})
        self.app.illustrations.interrupt()
        self.generate(plan)
        def while_running(*args):
            with self.assertRaises(Problem):self.app.illustrations.archive(plan['id'],{'version':2,'archived':True})
            return png()
        with patch('studio.image_provider.generate',side_effect=while_running):self.app.illustrations.tick()
        self.assertTrue(self.app.illustrations.archive(plan['id'],{'version':2,'archived':True})['archived_at'])

    def test_existing_schema_migration_and_archive_backup_restore(self):
        plan=self.plan()
        with self.app.db.connect() as db:
            db.execute('ALTER TABLE illustration_plans DROP COLUMN archived_at')
        IllustrationManager(self.app)
        self.assertEqual(self.app.illustrations.get(plan['id']),plan)
        self.app.illustrations.archive(plan['id'],{'version':1,'archived':True})
        IllustrationManager(self.app)
        archive=self.root/'archive-backup.zip';archive.write_bytes(backup_bundle(self.app))
        restore(archive,self.root/'archive-restored')
        app=Studio(self.root/'archive-restored');self.addCleanup(app.close)
        self.assertEqual(app.illustrations.listing(),[])
        self.assertEqual(len(app.illustrations.listing(archived=True)),1)
        self.assertEqual(app.illustrations.get(plan['id'])['pages'],plan['pages'])

    def test_worker_runs_image_tasks_and_http_routes_require_token(self):
        server=Server(('127.0.0.1',0),self.app)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path,values=None,token=True):
            req=urllib.request.Request(base+path,data=dumps(values).encode() if values is not None else None,
                headers={'Content-Type':'application/json',**({'X-Studio-Token':server.token} if token else {})})
            with opener.open(req,timeout=3) as response:return response.read()
        with self.assertRaises(urllib.error.HTTPError) as denied:request('/api/illustrations',{},False)
        self.assertEqual(denied.exception.code,403)
        plan=json.loads(request('/api/illustrations',{'post_id':self.post['id'],'post_version':1}))
        self.assertEqual(len(json.loads(request('/api/illustrations?post_id='+self.post['id']))['plans']),1)
        self.assertNotIn(b'test-only-secret',request('/api/illustrations/settings'))
        self.assertIn(b'handleIllustrationAction',request('/illustrations.js'))
        with self.assertRaises(urllib.error.HTTPError) as denied:
            request('/api/illustrations/'+plan['id']+'/archive',{'version':1,'archived':True},False)
        self.assertEqual(denied.exception.code,403)
        request('/api/illustrations/'+plan['id']+'/archive',{'version':1,'archived':True})
        self.assertEqual(json.loads(request('/api/illustrations'))['plans'],[])
        self.assertEqual(len(json.loads(request('/api/illustrations?archived=1&post_id='+self.post['id']))['plans']),1)
        self.assertEqual(json.loads(request('/api/illustrations?archived=1&post_id='+uid()))['plans'],[])
        with self.assertRaises(urllib.error.HTTPError) as invalid:request('/api/illustrations?archived=oops')
        self.assertEqual(invalid.exception.code,400)
        request('/api/illustrations/'+plan['id']+'/archive',{'version':1,'archived':False})
        self.generate(plan)
        called=threading.Event()
        def provider(*args):called.set();return png()
        with patch('studio.image_provider.generate',side_effect=provider):
            self.app.start_worker();self.assertTrue(called.wait(4))


class ImageProviderTests(unittest.TestCase):
    def test_multipart_contains_real_image_bytes_and_edit_route(self):
        config={'IMAGE_BASE_URL':'https://image.example/v1','IMAGE_MODEL':'image-test','IMAGE_API_KEY':'secret','IMAGE_REFERENCES':'edits'}
        output=json.dumps({'data':[{'b64_json':base64.b64encode(png()).decode()}]}).encode()
        def capture(request,limit,timeout=120):
            self.assertTrue(request.full_url.endswith('/images/edits'))
            self.assertIn(png(),request.data)
            self.assertIn(b'name="image"',request.data)
            self.assertNotIn(b'/media/',request.data)
            return output
        with patch('studio.image_provider._request',side_effect=capture):self.assertEqual(image_provider.generate(config,'具体场景','1024x1024',[png()]),png())
        config['IMAGE_REFERENCES']='none'
        with self.assertRaises(DeliveryError):image_provider.generate(config,'参考图','1024x1024',[png()])

    def test_http_failure_does_not_echo_provider_secret(self):
        error=urllib.error.HTTPError('https://example',403,'Forbidden',{},io.BytesIO(b'secret credential'))
        with patch('urllib.request.OpenerDirector.open',side_effect=error):
            with self.assertRaises(DeliveryError) as caught:image_provider._request(urllib.request.Request('https://example'),10)
        self.assertIn('权限',str(caught.exception))
        self.assertNotIn('credential',str(caught.exception))

    def test_invalid_image_and_private_download_are_rejected(self):
        with self.assertRaises(DeliveryError):image_provider.image_bytes(b'not image')
        config={'IMAGE_BASE_URL':'https://image.example/v1','IMAGE_MODEL':'image-test','IMAGE_API_KEY':'','IMAGE_REFERENCES':'none'}
        with patch('studio.image_provider._request',return_value=b'{"data":[{"url":"https://127.0.0.1/private"}]}') as request:
            with self.assertRaises(DeliveryError):image_provider.generate(config,'prompt','1024x1024',[])
            self.assertEqual(request.call_count,1)


if __name__=='__main__':unittest.main()
