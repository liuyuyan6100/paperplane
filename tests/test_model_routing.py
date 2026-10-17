"""Verify actual outgoing model IDs and credential boundaries with loopback HTTP."""
import base64
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image
from studio import profiles, model_routing, image_provider, assets
from studio.db import uid, dumps
from studio.server import Server
from studio.service import Studio, Problem
from tests.test_topics import response as topic_response
from tests.test_reviews import advice


class FunctionalModelTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {key: '' for key in ['AI_MODE', 'AI_BASE_URL', 'AI_MODEL', 'AI_API_KEY',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
            *[model_routing.model_key(t) for t in model_routing.TEXT_TASKS]]})
        env.start(); self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.app = Studio(tmp.name); self.addCleanup(self.app.close)
        self.calls = []; self.catalog_calls = []; self.fail = False; self.on_request = None
        self.outputs = {}
        out = io.BytesIO(); Image.new('RGB', (64, 64), 'blue').save(out, format='PNG')
        self.picture = out.getvalue()
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                test.catalog_calls.append(self.headers.get('Authorization'))
                body = json.dumps({'data': [{'id':'body-model'}, {'id':'image-model'}, {'id':'video-model'}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.calls.append({'path':self.path, 'key':self.headers.get('Authorization'), 'payload':payload})
                if test.on_request: test.on_request()
                if test.fail:
                    self.send_response(403); self.end_headers(); self.wfile.write(b'private-upstream-error'); return
                body = ({'data':[{'b64_json':base64.b64encode(test.picture).decode()}]} if '/images/' in self.path else
                        {'choices':[{'message':{'content':test.outputs.get(payload['model'], '连接成功')}}]})
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())
        self.model = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.start(self.model)
        self.base = f'http://127.0.0.1:{self.model.server_port}/v1'
        self.app.save_settings({'AI_MODE':'api', 'AI_BASE_URL':self.base, 'AI_MODEL':'default-model', 'AI_API_KEY':'synthetic-shared-key'})
        self.server = Server(('127.0.0.1',0), self.app); self.start(self.server)

    def start(self, server):
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close(): server.shutdown();server.server_close();thread.join(3)
        self.addCleanup(close)

    def request(self, path, body=None, authenticated=True):
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api'+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','X-Studio-Token':self.server.token if authenticated else ''})
        return json.load(urllib.request.urlopen(req,timeout=5))

    def configure_tasks(self):
        return self.request('/model-settings', {'text_models':{t:t+'-model' for t in model_routing.TEXT_TASKS},
            'image':{'use_shared':True,'model':'image-model','cover_model':'cover-model','illustration_model':'illustration-model','references':'none'}})

    def test_independent_image_catalog_keeps_its_own_credentials(self):
        self.app.config.update({'IMAGE_BASE_URL':self.base,'IMAGE_API_KEY':'synthetic-image-key','IMAGE_USE_SHARED':True})
        self.request('/illustrations/models', {'connection':'independent'})
        self.assertEqual(self.catalog_calls[-1], 'Bearer synthetic-image-key')
        self.request('/illustrations/models', {})
        self.assertEqual(self.catalog_calls[-1], 'Bearer synthetic-shared-key')

    def test_legacy_settings_inherit_without_migration_or_secret_exposure(self):
        before=self.app.config.path.read_bytes();s=self.request('/model-settings')
        self.assertTrue(all(t['effective_model']=='default-model' for t in s['text_tasks']))
        self.assertFalse(s['image']['use_shared']);self.assertNotIn('synthetic-shared-key',dumps(s))
        self.assertEqual(self.app.config.path.read_bytes(),before)
        self.app.generate_draft({'title':'合成稿','body':'','summary':''},'只回复连接成功')
        self.assertEqual(self.calls[-1]['payload']['model'],'default-model')

    def test_all_text_tasks_send_selected_id_and_key(self):
        self.configure_tasks()
        for task in model_routing.TEXT_TASKS:
            self.app.generate_draft({'title':'合成稿','body':'','summary':'','_model_task':task},'连接测试')
            self.assertEqual(self.calls[-1]['payload']['model'],task+'-model')
            self.assertEqual(self.calls[-1]['key'],'Bearer synthetic-shared-key')
        self.request('/model-settings',{'text_models':{'review':''}})
        self.app.generate_draft({'title':'合成稿','body':'','summary':'','_model_task':'review'},'连接测试')
        self.assertEqual(self.calls[-1]['payload']['model'],'default-model')

    def test_chat_planner_uses_configured_transport_and_records_no_key(self):
        self.configure_tasks()
        self.outputs['chat-model']=dumps({'reply':'合成连接已完成，没有创建写作任务','tool':None})
        chat=self.app.chat.create({})
        self.app.chat.send(chat['id'],{'message':'合成连接测试','request_id':uid()})
        self.app.chat.tick()
        current=self.app.chat.get(chat['id'])
        self.assertEqual(current['turns'][-1]['status'],'completed')
        self.assertEqual(self.calls[-1]['payload']['model'],'chat-model')
        self.assertEqual(self.calls[-1]['key'],'Bearer synthetic-shared-key')
        self.assertNotIn('synthetic-shared-key',dumps(current))

    def test_actual_topic_outline_body_repurpose_review_and_plan_modules(self):
        self.configure_tasks()
        profile=profiles.save(self.app,{'name':'合成账号'})
        post=self.app.save_post({'title':'合成母稿','body':'原句需要简洁。','summary':'合成','stage':'draft','profile_id':profile['id']})
        self.outputs.update({'topic-model':topic_response(),'outline-model':'## 提纲\n\n第一节',
            'body-model':'## 正文\n\n合成文章正文。', 'review-model':dumps(advice()),
            'repurpose-model':dumps({'title':'合成改编','summary':'合成摘要','body':'## 合成改编正文', 'tags':[], 'caption':'','pages':[]}),
            'illustration_plan-model':dumps([{'title':'合成图','text':'','prompt':'蓝色圆形','slot':'body','aspect':'square'}])})
        topic=self.app.topics.create({'profile_id':profile['id'],'idea':'合成选题'})
        self.app.topics.tick();self.assertEqual(self.app.topics.get(topic['id'])['status'],'completed')
        self.assertEqual(self.calls[-1]['payload']['model'],'topic-model')
        self.assertNotIn('synthetic-shared-key',dumps(self.app.topics.get(topic['id'])))
        workflow=self.app.workflows.create({'post_id':post['id'],'version':post['version'],'instruction':'合成生成'})
        self.app.workflows.tick();self.assertEqual(self.calls[-1]['payload']['model'],'outline-model')
        current=self.app.workflows.get(workflow['id'])
        self.app.workflows.confirm(workflow['id'],{'outline_version':current['outline_version'],'outline':current['outline']})
        self.app.workflows.tick();self.assertEqual(self.calls[-1]['payload']['model'],'body-model')
        self.app.repurpose.create({'post_id':post['id'],'post_version':post['version'],'platforms':['csdn'],'request_id':uid()})
        self.app.repurpose.tick();self.assertEqual(self.calls[-1]['payload']['model'],'repurpose-model')
        review=self.app.reviews.create({'post_id':post['id'],'post_version':post['version'],'request_id':uid()})
        self.app.reviews.tick();self.assertEqual(self.calls[-1]['payload']['model'],'review-model')
        self.assertEqual(self.app.reviews.get(review['id'])['status'],'review')
        plan=self.app.illustrations.create({'post_id':post['id'],'post_version':post['version']})
        self.app.illustrations.improve(plan['id'],{'version':plan['version']})
        self.app.illustrations.tick();self.assertEqual(self.calls[-1]['payload']['model'],'illustration_plan-model')
        self.assertEqual(self.app.post(post['id'])['body'],post['body'])

    def test_shared_image_connection_and_real_cover_body_dispatch(self):
        self.configure_tasks()
        post=self.app.save_post({'title':'合成配图','body':'正文','stage':'draft'})
        plan=self.app.illustrations.create({'post_id':post['id'],'post_version':post['version']})
        for index, expected in [(0,'cover-model'),(1,'illustration-model')]:
            plan=self.app.illustrations.get(plan['id'])
            self.app.illustrations.generate(plan['id'],{'version':plan['version'],'page_index':index,'request_id':uid()})
            self.app.illustrations.tick()
            self.assertEqual(self.calls[-1]['path'],'/v1/images/generations')
            self.assertEqual(self.calls[-1]['payload']['model'],expected)
            self.assertEqual(self.calls[-1]['key'],'Bearer synthetic-shared-key')
        self.assertEqual(self.app.post(post['id'])['cover'],'')

    def test_validate_all_before_save_and_prevent_key_reuse_at_new_destination(self):
        before=self.app.config.path.read_bytes()
        for values in [{'text_models':{'body':'new'},'image':{'references':'invalid'}},
            {'text_models':{'unknown':'x'}},{'connection':{'base_url':self.base+'/different'}},
            {'connection':{'api_key':'bad\nkey'}}, {'image':{'use_shared':'false'}}]:
            with self.assertRaises(urllib.error.HTTPError):self.request('/model-settings',values)
            self.assertEqual(self.app.config.path.read_bytes(),before)
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/model-settings',{},False)
        self.assertEqual(error.exception.code,403)

    def test_catalog_read_and_selection_do_not_save(self):
        before=self.app.config.path.read_bytes();result=self.request('/ai/models',{})
        self.assertIn('body-model',result['models']);self.assertEqual(before,self.app.config.path.read_bytes())

    def test_single_test_status_invalidates_on_model_key_or_failure(self):
        self.configure_tasks();result=self.request('/model-settings/test',{'task':'review'})
        self.assertEqual(result['model'],'review-model')
        self.assertTrue(next(t for t in self.request('/model-settings')['text_tasks'] if t['id']=='review')['tested'])
        self.fail=True
        with self.assertRaises(urllib.error.HTTPError):self.request('/model-settings/test',{'task':'review'})
        self.assertFalse(next(t for t in self.request('/model-settings')['text_tasks'] if t['id']=='review')['tested'])
        self.fail=False;self.request('/model-settings/test',{'task':'review'})
        self.request('/model-settings',{'connection':{'api_key':'new-synthetic-key'}})
        self.assertFalse(next(t for t in self.request('/model-settings')['text_tasks'] if t['id']=='review')['tested'])
        for task in ['video', None, []]:
            with self.assertRaises(urllib.error.HTTPError):self.request('/model-settings/test',{'task':task})

    def test_started_request_keeps_model_and_next_request_uses_new_setting(self):
        self.configure_tasks()
        self.on_request=lambda:self.app.config.update({'AI_REVIEW_MODEL':'new-review-model'})
        self.app.generate_draft({'title':'合成','summary':'','body':'','_model_task':'review'},'连接测试')
        self.assertEqual(self.calls[-1]['payload']['model'],'review-model')
        self.on_request=None
        self.app.generate_draft({'title':'合成','summary':'','body':'','_model_task':'review'},'连接测试')
        self.assertEqual(self.calls[-1]['payload']['model'],'new-review-model')

    def test_captured_route_does_not_send_stored_key_to_changed_connection(self):
        self.configure_tasks();route=model_routing.route_for(self.app.config,'body')
        self.app.config.update({'AI_MODE':'program'})
        with self.assertRaises(Exception) as error:
            self.app.generate_draft({'title':'合成','summary':'','body':'','_model_task':'body','_model_route':route},'连接测试')
        self.assertIn('连接已变化',str(error.exception));self.assertEqual(self.calls,[])
        self.app.config.update({'AI_MODE':'api'})
        self.request('/model-settings',{'connection':{'base_url':self.base+'/new','api_key':'new-key'}})
        with self.assertRaises(Exception) as error:
            self.app.generate_draft({'title':'合成','summary':'','body':'','_model_task':'body','_model_route':route},'连接测试')
        self.assertIn('连接已变化',str(error.exception));self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
