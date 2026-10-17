"""Model provenance across real task managers, with in-process synthetic adapters."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from studio import adapters, image_provider, model_provenance as provenance, model_routing, profiles
from studio.db import dumps, uid
from studio.service import Studio
from tests.test_reviews import advice
from tests.test_topics import response as topic_response


class ProvenanceFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name) / 'isolated-studio')
        self.addCleanup(self.app.close)
        self.app.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'https://synthetic.example/v1',
            'AI_API_KEY': 'synthetic-private-key', 'AI_MODEL': 'default',
            **{model_routing.model_key(task): task + '-model' for task in model_routing.TEXT_TASKS},
            'IMAGE_BASE_URL': 'https://synthetic.example/v1', 'IMAGE_API_KEY': 'synthetic-image-key',
            'IMAGE_MODEL': 'image-model', 'IMAGE_COVER_MODEL': 'cover-model',
            'IMAGE_ILLUSTRATION_MODEL': 'illustration-model', 'IMAGE_REFERENCES': 'none', 'IMAGE_USE_SHARED': False})
        self.outputs = {'topic-model': topic_response(), 'outline-model': '## 提纲\n\n第一节',
            'body-model': '## 正文\n\n合成文章正文。', 'review-model': dumps(advice()),
            'repurpose-model': dumps({'title':'合成改编','summary':'合成摘要','body':'## 合成改编正文','tags':[],'caption':'','pages':[]}),
            'chat-model': dumps({'reply': '仅作合成测试，没有执行创作操作。', 'tool': None}),
            'illustration_plan-model': dumps([{'title':'合成插图','text':'','prompt':'蓝色圆形','slot':'body','aspect':'square'}])}
        def response(_url, payload=None, *_args, **_kwargs):
            model = payload['model']
            return {'model': 'reported-' + model, 'choices': [{'message': {'content': self.outputs.get(model, '合成输出')}}]}
        self.request_patch = patch.object(adapters, 'request_json', side_effect=response)
        self.request_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.profile = profiles.save(self.app, {'name': '合成账号'})
        self.post = self.app.save_post({'title':'合成母稿','body':'原句需要简洁。','summary':'合成',
                                        'stage':'draft','profile_id':self.profile['id']})

    def assert_call(self, kind, owner, task):
        result = provenance.public_for(self.app, kind, owner, task=task)
        self.assertEqual(result['source'], 'recorded')
        self.assertEqual(result['requested_model'], task + '-model')
        self.assertEqual(result['reported_model'], 'reported-' + task + '-model')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['calls']), 1)
        self.assertNotIn('synthetic-private-key', dumps(result))
        return result

    def test_all_text_task_managers_bind_calls_to_correct_owner(self):
        chat = self.app.chat.create({})
        self.app.chat.send(chat['id'], {'message':'合成测试', 'request_id':uid()})
        self.app.chat.tick()
        turn = self.app.chat.get(chat['id'])['turns'][-1]
        self.assert_call('conversation_turns', turn['id'], 'chat')

        topic = self.app.topics.create({'profile_id':self.profile['id'],'idea':'合成选题'})
        self.app.topics.tick()
        self.assertEqual(self.app.topics.get(topic['id'])['model_provenance'], self.assert_call('topic_plans', topic['id'], 'topic'))

        workflow = self.app.workflows.create({'post_id':self.post['id'],'version':self.post['version'],'instruction':'合成生成'})
        self.app.workflows.tick()
        self.assert_call('workflows', workflow['id'], 'outline')
        current = self.app.workflows.get(workflow['id'])
        self.app.workflows.confirm(workflow['id'], {'outline_version':current['outline_version'],'outline':current['outline']})
        self.app.workflows.tick()
        self.assert_call('workflows', workflow['id'], 'body')

        variant = self.app.repurpose.create({'post_id':self.post['id'],'post_version':self.post['version'],
            'platforms':['csdn'],'request_id':uid()})['variants'][0]
        self.app.repurpose.tick()
        self.assertEqual(self.app.repurpose.get(variant['id'])['model_provenance'], self.assert_call('repurpose_variants', variant['id'], 'repurpose'))

        review = self.app.reviews.create({'post_id':self.post['id'],'post_version':self.post['version'],'request_id':uid()})
        self.app.reviews.tick()
        self.assertEqual(self.app.reviews.get(review['id'])['model_provenance'], self.assert_call('editorial_reviews', review['id'], 'review'))

        plan = self.app.illustrations.create({'post_id':self.post['id'],'post_version':self.post['version']})
        self.app.illustrations.improve(plan['id'], {'version':plan['version']})
        self.app.illustrations.tick()
        self.assert_call('illustration_plans', plan['id'], 'illustration_plan')

    def test_image_generation_and_plan_have_separate_model_records(self):
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'white').save(buffer, format='PNG')
        raw = buffer.getvalue()
        plan = self.app.illustrations.create({'post_id':self.post['id'],'post_version':self.post['version']})
        for index, task in ((0, 'cover'), (1, 'illustration')):
            request = uid()
            self.app.illustrations.generate(plan['id'], {'version':plan['version'],'page_index':index,'request_id':request})
            response = {'model':'reported-' + task + '-model','data':[{'b64_json':base64.b64encode(raw).decode()}]}
            with patch.object(image_provider, '_request', return_value=json.dumps(response).encode()):
                self.app.illustrations.tick()
            self.assert_call('illustration_candidates', request, task)
        self.assertEqual(provenance.public_for(self.app, 'illustration_plans', plan['id'], task='illustration_plan')['source'], 'unknown')

    def test_central_retry_records_each_attempt_and_keeps_captured_model(self):
        route = model_routing.route_for(self.app.config, 'body', 'workflows', 'synthetic-owner')
        post = {**self.post, '_model_task':'body','_model_route':route}
        calls = []
        def response(_url, payload=None, *_args, **_kwargs):
            calls.append(payload['model'])
            self.app.config.update({'AI_BODY_MODEL':'different-future-model'})
            if len(calls) == 1:
                raise adapters.DeliveryError('temporary synthetic error', retryable=True)
            return {'model':'returned-alias','choices':[{'message':{'content':'合成输出'}}]}
        with patch.object(adapters, 'request_json', side_effect=response):
            self.assertEqual(self.app.generate_draft(post, ''), '合成输出')
        self.assertEqual(calls, ['body-model','body-model'])
        history = provenance.public_for(self.app, 'workflows', 'synthetic-owner', task='body')
        self.assertEqual([c['status'] for c in history['calls']], ['failed','completed'])
        self.assertEqual(history['requested_model'], 'body-model')
        self.assertEqual(history['reported_model'], 'returned-alias')
        self.assertEqual(provenance.planned(self.app.config, 'body')['requested_model'], 'different-future-model')

    def test_old_result_is_not_relabelled_after_settings_change(self):
        review = self.app.reviews.create({'post_id':self.post['id'],'post_version':self.post['version'],'request_id':uid()})
        self.app.reviews.tick()
        self.app.config.update({'AI_REVIEW_MODEL':'new-review-model'})
        self.assertEqual(self.app.reviews.get(review['id'])['model_provenance']['requested_model'], 'review-model')
        # Simulate an existing pre-upgrade record which has only its original route.
        with self.app.db.connect(immediate=True) as db:
            db.execute('DELETE FROM model_calls WHERE owner_kind=? AND owner_id=?', ('editorial_reviews', review['id']))
        legacy = self.app.reviews.get(review['id'])['model_provenance']
        self.assertEqual(legacy['source'], 'legacy')
        self.assertEqual(legacy['requested_model'], 'review-model')
        self.assertEqual(legacy['reported_model'], '')

    def visual_variant(self, mode='ai'):
        variant = self.app.repurpose.create({'post_id':self.post['id'],'post_version':self.post['version'],
            'platforms':['csdn'],'visual_mode':mode,'request_id':uid()})['variants'][0]
        self.app.repurpose.tick()
        return variant['id']

    def image_response(self):
        buffer = io.BytesIO()
        Image.new('RGB', (32,32), 'blue').save(buffer, format='PNG')
        raw = buffer.getvalue()
        def response(request, *_args, **_kwargs):
            model = json.loads(request.data)['model']
            return dumps({'model':'reported-'+model,'data':[{'b64_json':base64.b64encode(raw).decode()}]}).encode()
        return raw, response

    def test_platform_visuals_record_image_models_separately_from_text(self):
        variant = self.visual_variant()
        _raw, response = self.image_response()
        with patch.object(image_provider, '_request', side_effect=response):
            self.assertTrue(self.app.deliverables.tick())
            partial = self.app.repurpose.get(variant)['visuals']['pages']
            self.assertEqual(partial[0]['model_provenance']['reported_model'],'reported-cover-model')
            self.assertEqual(partial[1]['model_provenance']['source'],'planned')
            self.assertTrue(self.app.deliverables.tick())
        pages = self.app.repurpose.get(variant)['visuals']['pages']
        for page, task in zip(pages, ('cover','illustration')):
            self.assertEqual(page['model_provenance'], self.assert_call('platform_visual_pages', page['id'], task))
        self.app.config.update({'IMAGE_COVER_MODEL':'later-cover'})
        self.assertEqual(self.app.repurpose.get(variant)['visuals']['pages'][0]['model_provenance']['requested_model'],'cover-model')

    def test_platform_cards_are_deterministic_without_image_calls(self):
        variant = self.visual_variant('cards')
        raw, _response = self.image_response()
        with patch('studio.deliverables.render_card',return_value=raw), patch.object(image_provider,'generate',side_effect=AssertionError('cards must not call image model')):
            self.app.deliverables.tick()
            self.app.deliverables.tick()
        pages = self.app.repurpose.get(variant)['visuals']['pages']
        self.assertEqual(len(pages),2)
        self.assertTrue(all(p['model_provenance']['source']=='none' for p in pages))
        self.assertTrue(all(p['model_provenance']['label']=='确定性排版' for p in pages))
        self.assertFalse(self.app.db.rows("SELECT * FROM model_calls WHERE owner_kind='platform_visual_pages'"))

    def test_platform_image_manual_retry_retains_both_models_and_old_snapshot(self):
        variant = self.visual_variant()
        _raw, response = self.image_response()
        with patch.object(image_provider,'_request',side_effect=adapters.DeliveryError('synthetic failure')):
            self.app.deliverables.tick()
        with patch.object(image_provider,'_request',side_effect=response):
            self.app.deliverables.tick()
            self.app.config.update({'IMAGE_COVER_MODEL':'retry-cover'})
            self.app.deliverables.enable(variant,retry=True)
            self.app.deliverables.tick()
        pages = self.app.repurpose.get(variant)['visuals']['pages']
        history = pages[0]['model_provenance']
        self.assertEqual([c['status'] for c in history['calls']],['failed','completed'])
        self.assertEqual([c['requested_model'] for c in history['calls']],['cover-model','retry-cover'])
        self.assertEqual(history['reported_model'],'reported-retry-cover')
        with self.app.db.connect(immediate=True) as db:
            db.execute("DELETE FROM model_calls WHERE owner_kind='platform_visual_pages'")
        legacy = self.app.repurpose.get(variant)['visuals']['pages'][1]['model_provenance']
        self.assertEqual(legacy['source'],'legacy')
        self.assertEqual(legacy['requested_model'],'illustration-model')


if __name__ == '__main__':
    unittest.main()
