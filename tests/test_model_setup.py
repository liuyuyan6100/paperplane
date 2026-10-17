"""First-use checks must not touch providers, expose keys or rewrite config."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from studio import model_setup, model_routing
from studio.config import Config
from studio.service import Studio, Problem
from studio.server import Server

class SetupTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{key:'' for key in ['AI_MODE','AI_MODEL','AI_BASE_URL','AI_API_KEY','MODEL_SETUP_DISMISSED']})
        env.start();self.addCleanup(env.stop)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.config=Config(tmp.name)
    def complete(self, **changes):
        self.config.update(dict(AI_MODE='api',AI_BASE_URL='https://models.test/v1',AI_API_KEY='synthetic-private-key',AI_MODEL='text-model',**changes))
    def test_empty_checks_do_not_write_or_call_provider(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network')):
            info=model_setup.inspect(self.config)
        self.assertTrue(info['should_prompt']);self.assertFalse(info['configured'])
        self.assertEqual([i['field'] for i in info['missing']],['ai-base','ai-key','ai-model'])
        self.assertFalse(self.config.path.exists())
    def test_complete_existing_config_is_read_only_and_private(self):
        self.complete();before=self.config.path.read_bytes()
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network')):
            info=model_setup.inspect(self.config)
        self.assertTrue(info['configured']);self.assertFalse(info['should_prompt'])
        self.assertNotIn('synthetic-private-key',json.dumps(info));self.assertEqual(before,self.config.path.read_bytes())
    def test_skip_persists_without_claiming_configured(self):
        info=model_setup.dismiss(self.config)
        self.assertFalse(info['should_prompt']);self.assertFalse(info['configured'])
        self.assertFalse(model_setup.inspect(Config(self.config.path.parent))['should_prompt'])
    def test_placeholder_values_and_invalid_url_are_not_ready(self):
        for key in ['REPLACE_ME','sk-ant-REPLACE_ME','your_api_key','••••','contains space']:
            with self.subTest(key=key):
                self.config.update({'AI_MODE':'api','AI_BASE_URL':'https://models.test/v1','AI_API_KEY':key,'AI_MODEL':'model'})
                self.assertIn('ai-key',[i['field'] for i in model_setup.inspect(self.config)['missing']])
        for url in ['https://example.com/v1','https://models.invalid/v1','https://user:pass@models.test','not-a-url']:
            self.config.update({'AI_BASE_URL':url})
            self.assertIn('ai-base',[i['field'] for i in model_setup.inspect(self.config)['missing']])
    def test_anonymous_loopback_is_supported_but_remote_requires_key(self):
        self.config.update({'AI_BASE_URL':'http://127.0.0.1:1234/v1','AI_MODEL':'local-model'})
        self.assertTrue(model_setup.inspect(self.config)['configured'])
        self.config.update({'AI_BASE_URL':'https://models.test/v1'})
        self.assertFalse(model_setup.inspect(self.config)['configured'])
    def test_legacy_program_is_not_silently_changed(self):
        self.complete();self.config.update({'AI_MODE':'program','AI_PROGRAM':'old.exe'})
        self.assertEqual(model_setup.inspect(self.config)['missing'][0]['field'],'ai-mode')
        self.assertEqual(self.config.get('AI_MODE'),'program')
        saved=model_routing.save(self.config,{'connection':{'mode':'api'}})
        self.assertTrue(saved['setup']['configured']);self.assertEqual(self.config.get('AI_PROGRAM'),'old.exe')
    def test_invalid_mode_update_is_atomic(self):
        self.complete();before=self.config.path.read_bytes()
        with self.assertRaises(Problem): model_routing.save(self.config,{'connection':{'mode':'invalid','model':'new'}})
        self.assertEqual(before,self.config.path.read_bytes())
    def test_missing_key_also_marks_task_unconfigured(self):
        self.config.update({'AI_BASE_URL':'https://models.test/v1','AI_MODEL':'text-model'})
        self.assertFalse(any(t['configured'] for t in model_routing.public_settings(self.config)['text_tasks']))
    def test_unreadable_private_file_is_distinct_from_empty(self):
        self.config.path.write_text('broken',encoding='utf-8')
        info=model_setup.inspect(self.config)
        self.assertTrue(info['read_error']);self.assertFalse(info['should_prompt'])
        self.assertEqual(self.config.path.read_text(),'broken')
    def test_image_readiness_uses_effective_key_and_model(self):
        self.complete()
        self.config.update({'IMAGE_MODEL':'image-model','IMAGE_BASE_URL':'https://images.test/v1'})
        self.assertFalse(model_routing.public_settings(self.config)['image_tasks'][0]['configured'])
        self.config.update({'IMAGE_USE_SHARED':True})
        self.assertTrue(model_routing.public_settings(self.config)['image_tasks'][0]['configured'])
        self.config.update({'AI_API_KEY':'REPLACE_ME'})
        self.assertFalse(model_routing.public_settings(self.config)['image_tasks'][0]['configured'])

    def test_wizard_partial_saves_preserve_other_steps_and_routes(self):
        self.complete()
        self.config.update({'AI_REVIEW_MODEL':'custom-review','IMAGE_BASE_URL':'https://images.test/v1','IMAGE_API_KEY':'saved-image-key','IMAGE_MODEL':'existing-image','IMAGE_COVER_MODEL':'cover-override'})
        model_routing.save(self.config,{'connection':{'mode':'api','model':'new-text'}})
        self.assertEqual(self.config.get('IMAGE_API_KEY'),'saved-image-key')
        self.assertEqual(self.config.get('AI_REVIEW_MODEL'),'custom-review')
        model_routing.save(self.config,{'image':{'use_shared':True,'model':'new-image'}})
        self.assertEqual(self.config.get('AI_MODEL'),'new-text')
        self.assertEqual(self.config.get('AI_API_KEY'),'synthetic-private-key')
        self.assertEqual(self.config.get('IMAGE_API_KEY'),'saved-image-key')
        self.assertEqual(self.config.get('IMAGE_COVER_MODEL'),'cover-override')

    def test_effective_environment_takes_precedence(self):
        self.complete()
        with patch.dict(os.environ,{'AI_API_KEY':'REPLACE_ME'}):
            self.assertFalse(model_setup.inspect(self.config)['configured'])

class SetupHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{k:'' for k in ['AI_MODE','AI_BASE_URL','AI_API_KEY','AI_MODEL','MODEL_SETUP_DISMISSED']});env.start();self.addCleanup(env.stop)
        self.app=Studio(self.tmp.name);self.addCleanup(self.app.close)
        self.server=Server(('127.0.0.1',0),self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def request(self,path,body=None,token=True):
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api'+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','X-Studio-Token':self.server.token if token else ''})
        return json.load(urllib.request.urlopen(req,timeout=5))
    def test_http_read_skip_save_and_recheck(self):
        self.assertTrue(self.request('/model-setup')['should_prompt'])
        self.assertFalse(self.request('/model-setup/dismiss',{})['should_prompt'])
        saved=self.request('/model-settings',{'connection':{'mode':'api','base_url':'https://models.test/v1','model':'model','api_key':'synthetic-private-key'}})
        self.assertTrue(saved['setup']['configured']);self.assertTrue(self.request('/model-setup')['configured'])
        self.assertNotIn('synthetic-private-key',json.dumps(saved))
    def test_skip_requires_existing_session_token(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/model-setup/dismiss',{},False)
        self.assertEqual(cm.exception.code,403)
        self.assertTrue(self.request('/model-setup')['should_prompt'])

if __name__=='__main__':unittest.main()
