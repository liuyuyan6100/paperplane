import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from studio.service import Studio, Problem
from studio.db import dumps
from studio.deliverables import render_card
from tests.test_repurpose import content


class DeliverableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name))
        self.addCleanup(self.app.close)
        saved = self.app.save_post({'title': '母稿', 'body': '已有证据', 'stage': 'draft'})
        self.post = self.app.post(saved['id'])

    def draft(self, platform='csdn'):
        row = self.app.repurpose.create({'post_id': self.post['id'], 'post_version': 1, 'platforms': [platform], 'visual_mode': 'cards'})['variants'][0]
        with patch.object(self.app, 'generate_draft', return_value=dumps(content(platform))):
            self.app.repurpose.tick()
        return self.app.repurpose.get(row['id'])

    def drain(self):
        for _ in range(30):
            if not self.app.deliverables.tick(): return
        self.fail('image queue did not finish')

    def test_full_package_blocks_early_adoption_and_keeps_mother(self):
        for platform in ('csdn', 'juejin', 'devto', 'xiaohongshu'):
            task = self.draft(platform)
            with self.assertRaises(Problem):
                self.app.repurpose.apply(task['id'], {'version': task['version']})
            self.drain()
            current = self.app.repurpose.get(task['id'])
            self.assertEqual(current['visuals']['status'], 'ready')
            self.assertEqual(len(current['visuals']['pages']), 3 if platform == 'xiaohongshu' else 2)
            for page in current['visuals']['pages']:
                with Image.open(self.app.db.data_dir / 'media' / Path(page['url']).name) as im:
                    self.assertEqual(im.size, (1080, 1440) if platform == 'xiaohongshu' else (1536, 1024))
            adopted = self.app.repurpose.apply(task['id'], {'version': task['version']})['post']
            self.assertTrue(adopted['xhs']['photos'] if platform == 'xiaohongshu' else adopted['cover'])
        self.assertEqual(self.app.post(self.post['id']), self.post)
        self.assertEqual(self.app.jobs(), [])

    def test_edit_regenerates_current_version_only_and_noop_keeps_images(self):
        task = self.draft(); self.drain()
        initial = self.app.repurpose.get(task['id'])
        unchanged = self.app.repurpose.edit(task['id'], {'version': task['version'], 'content': content()})
        self.assertEqual(unchanged['visuals'], initial['visuals'])
        edited = self.app.repurpose.edit(task['id'], {'version': task['version'], 'content': {**content(), 'title': '修改标题'}})
        self.assertEqual(edited['visuals']['pages'], [])
        self.drain()
        final = self.app.repurpose.get(task['id'])
        self.assertEqual(final['visuals']['pages'][0]['title'], '修改标题')
        self.assertNotEqual(final['visuals']['pages'][0]['url'], initial['visuals']['pages'][0]['url'])

    def test_partial_failure_retries_only_failed_pages(self):
        task = self.draft()
        with patch('studio.deliverables.render_card', side_effect=Problem('合成失败')):
            self.app.deliverables.tick()
        self.drain()
        partial = self.app.repurpose.get(task['id'])['visuals']
        self.assertEqual(partial['status'], 'failed')
        self.app.deliverables.enable(task['id'], retry=True); self.drain()
        ready = self.app.repurpose.get(task['id'])['visuals']
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(partial['pages'][1]['url'], ready['pages'][1]['url'])
        self.app.deliverables.enable(task['id'], retry=True)
        self.assertFalse(self.app.deliverables.tick())

    def test_interrupted_or_edited_inflight_result_never_attached(self):
        for interrupt in (True, False):
            task = self.draft()
            def mutate(*args):
                if interrupt: self.app.deliverables.interrupt()
                else: self.app.repurpose.edit(task['id'], {'version': task['version'], 'content': {**content(), 'title': '后来修改'}})
                return render_card(*args)
            with patch('studio.deliverables.render_card', side_effect=mutate):
                self.app.deliverables.tick()
            self.assertEqual(self.app.db.one('SELECT count(*) n FROM assets')['n'], 0)
            if interrupt:
                self.assertFalse(self.app.deliverables.tick())

    def test_missing_file_cannot_be_adopted(self):
        task = self.draft(); self.drain()
        page = self.app.repurpose.get(task['id'])['visuals']['pages'][0]
        (self.app.db.data_dir / 'media' / Path(page['url']).name).unlink()
        with self.assertRaises(Problem):
            self.app.repurpose.apply(task['id'], {'version': task['version']})
        self.assertEqual(len(self.app.state()['posts']), 1)

    def test_default_ai_missing_configuration_never_falls_back_to_cards(self):
        task=self.app.repurpose.create({'post_id':self.post['id'],'post_version':1,'platforms':['csdn']})['variants'][0]
        with patch.object(self.app,'generate_draft',return_value=dumps(content())):
            self.app.repurpose.tick()
        with patch('studio.deliverables.render_card',side_effect=AssertionError('silent fallback')):
            self.drain()
        result=self.app.repurpose.get(task['id'])
        self.assertEqual(result['visuals']['mode'],'ai')
        self.assertEqual(result['visuals']['status'],'failed')
        self.assertIn('图片模型尚未配置',result['visuals']['pages'][0]['error'])
        self.assertEqual(result['status'],'review')

    def test_ai_real_bytes_are_stored_with_model_provenance(self):
        self.app.config.update({'IMAGE_BASE_URL':'https://images.example/v1','IMAGE_MODEL':'image-test'})
        task=self.app.repurpose.create({'post_id':self.post['id'],'post_version':1,'platforms':['csdn']})['variants'][0]
        with patch.object(self.app,'generate_draft',return_value=dumps(content())):
            self.app.repurpose.tick()
        buf=io.BytesIO();Image.new('RGB',(32,32),'blue').save(buf,format='PNG')
        with patch('studio.image_provider.generate',return_value=buf.getvalue()) as generate:
            self.drain()
        result=self.app.repurpose.get(task['id'])['visuals']
        self.assertEqual(result['status'],'ready')
        self.assertEqual(generate.call_count,2)
        self.assertEqual(result['pages'][0]['model_route']['model'],'image-test')

    def test_adopt_and_publish_keeps_blocker_instead_of_claiming_success(self):
        task=self.draft();self.drain()
        result=self.app.repurpose.apply(task['id'],{'version':task['version'],'publish_public':True})
        self.assertEqual(result['post']['stage'],'ready')
        self.assertEqual(result['variant']['distribution']['status'],'blocked')
        self.assertIn('自动发送连接尚未接通',result['variant']['distribution']['message'])
        self.assertEqual(self.app.jobs(),[])
        again=self.app.repurpose.apply(task['id'],{'version':task['version'],'publish_public':True})
        self.assertEqual(result['post']['id'],again['post']['id'])

    def test_continue_reuses_existing_work_and_skips_published_platform(self):
        task=self.draft()
        from studio.publication_progress import register
        register(self.app,self.post['id'],{'version':1,'confirmed':True,'channels':['devto']})
        values={'post_id':self.post['id'],'post_version':1,'visual_mode':'cards'}
        first=self.app.repurpose.continue_platforms(values)
        second=self.app.repurpose.continue_platforms(values)
        self.assertEqual(first['created_platforms'],['juejin','xiaohongshu'])
        self.assertEqual(second['created_platforms'],[])
        self.assertEqual([r['id'] for r in first['variants']],[r['id'] for r in second['variants']])
        self.assertIn(task['id'],[r['id'] for r in second['variants']])
        self.assertNotIn('devto',[r['platform'] for r in second['variants']])

    def test_public_approval_is_snapshotted_without_changing_channel_default(self):
        self.app.config.update({'DEVTO_API_KEY':'test-only'})
        post=self.app.save_post({**self.post,'stage':'ready'})
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE channels SET enabled=1,config=? WHERE id='devto'",(dumps({'publish':False}),))
        result=self.app.enqueue(post['id'],{'version':post['version'],'channels':['devto'],'publish_public':True,'ack_warnings':True})
        job=self.app.job_detail(result['ids'][0])
        self.assertTrue(job['snapshot']['delivery_config']['publish'])
        self.assertEqual(job['status'],'queued')
        import json
        self.assertFalse(json.loads(self.app.db.one("SELECT config FROM channels WHERE id='devto'")['config'])['publish'])
