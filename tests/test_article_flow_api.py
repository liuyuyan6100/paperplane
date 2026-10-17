"""Loopback HTTP contracts for article draft preparation; never send to WeChat."""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from PIL import Image

from studio import model_routing
from studio.db import dumps
from studio.server import Server
from studio.service import Studio


class ArticleFlowAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name) / 'isolated-http-studio')
        self.addCleanup(self.app.close)
        self.app.config.update({'WECHAT_APP_ID':'synthetic-app', 'WECHAT_APP_SECRET':'synthetic-secret',
            'AI_MODE':'api', 'AI_BASE_URL':'https://private.invalid/tenant', 'AI_API_KEY':'private-text-key',
            'AI_MODEL':'default-text', 'AI_BODY_MODEL':'body-specific', 'IMAGE_MODEL':'default-image',
            'IMAGE_COVER_MODEL':'cover-specific', 'IMAGE_BASE_URL':'https://image-private.invalid/path',
            'IMAGE_API_KEY':'private-image-key'})
        with self.app.db.connect() as db:
            db.execute("UPDATE channels SET enabled=1,config=? WHERE id='wechat'", (dumps({'mode':'api'}),))
        image = io.BytesIO()
        Image.new('RGB', (16,16), 'white').save(image, format='PNG')
        self.cover = self.app.upload({'data':base64.b64encode(image.getvalue()).decode(), 'title':'synthetic cover'})['url']
        self.server = Server(('127.0.0.1',0), self.app)
        self.thread = threading.Thread(target=lambda:self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port) + '/api'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        # No worker is started. Failing these guards would reveal unintended provider I/O.
        self.no_send = patch('studio.wechat.send_draft', side_effect=AssertionError('HTTP preparation must not send to WeChat'))
        self.no_send.start()
        self.addCleanup(self.no_send.stop)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, path, body=None, *, status=200, authenticated=True, headers=None):
        request_headers = {'Content-Type':'application/json'}
        if authenticated: request_headers['X-Studio-Token'] = self.server.token
        request_headers.update(headers or {})
        request = urllib.request.Request(self.base + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8'), headers=request_headers)
        try:
            with self.opener.open(request, timeout=5) as response:
                code, result = response.status, json.load(response)
        except urllib.error.HTTPError as error:
            code, result = error.code, json.load(error)
        self.assertEqual(code, status, result)
        return result

    def post(self, **changes):
        return self.app.save_post({'title':'HTTP 合成测试','body':'第一段真实的合成测试内容。','summary':'测试摘要',
            'author':'测试作者','stage':'draft','cover':self.cover,**changes})

    def flow_path(self, post, suffix='wechat-draft'):
        return '/posts/' + post['id'] + '/' + suffix

    def test_planned_model_endpoint_is_prospective_and_contains_no_connection_data(self):
        result = self.request('/model-provenance/planned')
        tasks = {task['task']:task for task in result['tasks']}
        self.assertEqual(set(tasks), set(model_routing.TEXT_TASKS) | set(model_routing.IMAGE_TASKS))
        self.assertEqual(tasks['body']['requested_model'], 'body-specific')
        self.assertEqual(tasks['cover']['requested_model'], 'cover-specific')
        self.assertTrue(all(task['source']=='planned' for task in tasks.values()))
        encoded = dumps(result)
        for secret in ('private.invalid','tenant','private-text-key','image-private.invalid','private-image-key','base_url','api_key'):
            self.assertNotIn(secret, encoded)
        self.assertFalse(self.app.db.rows('SELECT * FROM model_calls'))

    def test_one_request_and_get_flow_expose_queued_draft_without_extra_confirmation(self):
        post = self.post()
        first = self.request(self.flow_path(post), {'version':post['version']})
        self.assertEqual(first['job']['status'],'queued')
        self.assertEqual(first['intent']['status'],'queued')
        self.assertEqual(first['delivery_model']['source'],'none')
        current = self.request(self.flow_path(post,'flow'))
        self.assertEqual(current['job']['id'], first['job']['id'])
        self.assertEqual(current['action'],'job')
        self.assertEqual(self.app.post(post['id'])['stage'],'ready')
        repeated = self.request(self.flow_path(post), {'version':current['post_version']})
        self.assertEqual(repeated['job']['id'],current['job']['id'])
        self.assertEqual(len(self.app.db.rows('SELECT * FROM jobs')),1)

    def test_saved_correction_resumes_asynchronously_and_only_once(self):
        post = self.post(summary='')
        waiting = self.request(self.flow_path(post), {'version':post['version']})
        self.assertEqual(waiting['intent']['status'],'waiting')
        self.assertFalse(waiting['intent']['report']['ok'])
        resumed, stop = threading.Event(), threading.Event()
        errors = []
        self.app.wake.clear()
        def scheduler():
            while not stop.is_set():
                if self.app.wake.wait(0.05):
                    self.app.wake.clear()
                    try: self.app.article_flow.resume_due()
                    except Exception as error: errors.append(error)
                    resumed.set()
        thread = threading.Thread(target=scheduler, daemon=True)
        thread.start()
        try:
            saved = self.request('/posts/'+post['id'], {'version':waiting['post_version'],'summary':'已补齐摘要'})
            self.assertTrue(resumed.wait(3),'Saved correction did not wake continuation')
            current = self.request(self.flow_path(post,'flow'))
            self.assertEqual(current['job']['status'],'queued')
            self.assertEqual(current['job']['version'],saved['version'])
            self.app.article_flow.resume_due()
            self.assertEqual(len(self.app.db.rows('SELECT * FROM jobs')),1)
            self.assertEqual(errors,[])
        finally:
            stop.set(); self.app.wake.set(); thread.join(3)

    def test_cancel_result_retains_version_and_later_save_cannot_resume(self):
        post = self.post(author='')
        waiting = self.request(self.flow_path(post), {'version':post['version']})
        canceled = self.request(self.flow_path(post,'wechat-draft/cancel'), {'version':waiting['post_version']})
        self.assertEqual(canceled['intent']['status'],'canceled')
        self.assertEqual(canceled['post_version'],waiting['post_version'])
        self.request('/posts/'+post['id'], {'version':canceled['post_version'],'author':'后来补齐'})
        self.app.article_flow.resume_due()
        current = self.request(self.flow_path(post,'flow'))
        self.assertIsNone(current['job'])
        self.assertEqual(current['intent']['status'],'canceled')

    def test_cancel_after_job_exists_is_conflict_and_does_not_claim_success(self):
        post = self.post()
        flow = self.request(self.flow_path(post), {'version':post['version']})
        result = self.request(self.flow_path(post,'wechat-draft/cancel'), {'version':flow['post_version']}, status=409)
        self.assertIn('任务已经创建',result['error'])
        self.assertEqual(self.app.jobs()[0]['status'],'queued')

    def test_errors_have_http_status_for_stale_missing_and_unauthenticated_requests(self):
        post = self.post()
        self.request(self.flow_path(post), {'version':post['version']}, authenticated=False, status=403)
        self.request(self.flow_path(post), {'version':post['version']}, headers={'Origin':'https://foreign.invalid'}, status=403)
        self.request(self.flow_path(post), {'version':post['version']}, headers={'Content-Type':'text/plain'}, status=415)
        current = self.request('/posts/'+post['id'], {'version':post['version'],'summary':'更改摘要'})
        self.request(self.flow_path(post), {'version':post['version']}, status=409)
        self.request(self.flow_path(post,'wechat-draft/cancel'), {'version':post['version']}, status=409)
        missing = '/posts/' + 'f'*32
        self.request(missing+'/flow', status=404)
        self.request(missing+'/wechat-draft', {'version':1}, status=404)
        self.assertFalse(self.app.db.rows('SELECT * FROM jobs'))
        self.assertEqual(current['version'],2)

    def test_generic_publish_route_keeps_multichannel_contract_without_creating_intent(self):
        post = self.post(stage='ready')
        report = self.request(self.flow_path(post,'check'), {'version':post['version'],'channels':['local']})
        self.assertTrue(report['ok'])
        result = self.request(self.flow_path(post,'publish'), {'version':post['version'],'channels':['local'],'ack_warnings':True})
        self.assertEqual(len(result['ids']),1)
        self.assertEqual(self.app.jobs()[0]['channel_id'],'local')
        self.assertIsNone(self.request(self.flow_path(post,'flow'))['intent'])
        self.request(self.flow_path(post,'publish'), {'version':post['version'],'channels':['local'],'ack_warnings':True}, status=422)

    def test_generic_publish_retains_draft_stage_error(self):
        post = self.post(stage='draft')
        result = self.request(self.flow_path(post,'publish'), {'version':post['version'],'channels':['local']}, status=422)
        self.assertFalse(result['details']['ok'])
        self.assertEqual(self.app.post(post['id'])['stage'],'draft')
        self.assertFalse(self.app.db.rows('SELECT * FROM jobs'))

    def test_diagram_api_distinguishes_text_model_from_deterministic_layout(self):
        post = self.post()
        before = self.app.post(post['id'])
        proposal = {'title':'测试流程','steps':[{'title':'步骤一','detail':'合成说明一'},{'title':'步骤二','detail':'合成说明二'}]}
        response = {'model':'reported-diagram-model','choices':[{'message':{'content':dumps(proposal)}}]}
        with patch('studio.adapters.request_json',return_value=response):
            result = self.request('/ai/diagram',{'post_id':post['id'],'instruction':'合成流程'})
        self.assertEqual(result['steps'],proposal['steps'])
        self.assertEqual(result['model_provenance']['task'],'diagram')
        self.assertEqual(result['model_provenance']['requested_model'],'default-text')
        self.assertEqual(result['model_provenance']['reported_model'],'reported-diagram-model')
        self.assertEqual(result['layout_model']['source'],'none')
        self.assertEqual(result['layout_model']['label'],'确定性排版')
        calls = self.app.db.rows('SELECT owner_kind,owner_id,task FROM model_calls')
        self.assertEqual(calls,[{'owner_kind':'posts','owner_id':post['id'],'task':'diagram'}])
        self.assertEqual(self.app.post(post['id']),before)


if __name__=='__main__': unittest.main()
