import json, tempfile, unittest, urllib.error
from studio.config import Config
from studio.usage_guide import read, update, KEY
from studio.service import Problem
from tests import test_model_setup

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.config=Config(self.tmp.name)
    def test_read_does_not_write(self):
        self.assertEqual(read(self.config),{'step':0,'completed':[],'active':False});self.assertFalse(self.config.path.exists())
    def test_resume_and_reset_preserve_config(self):
        self.config.update({'AI_API_KEY':'synthetic-secret','OTHER':42})
        update(self.config,{'action':'complete','step':0});update(self.config,{'action':'pause'})
        saved=read(Config(self.tmp.name));self.assertEqual(saved,{'step':1,'completed':[0],'active':False})
        update(self.config,{'action':'start'});self.assertTrue(read(self.config)['active'])
        update(self.config,{'action':'reset'});self.assertEqual(read(self.config)['completed'],[])
        self.assertEqual(self.config.get('AI_API_KEY'),'synthetic-secret');self.assertEqual(self.config.get('OTHER'),42)
    def test_navigation_is_not_completion_and_repeated_completion_is_idempotent(self):
        self.assertEqual(update(self.config,{'action':'goto','step':5})['completed'],[])
        for _ in range(2):update(self.config,{'action':'complete','step':0})
        self.assertEqual(read(self.config)['completed'],[0])
        for i in range(1,6):update(self.config,{'action':'complete','step':i})
        self.assertFalse(read(self.config)['active']);self.assertEqual(read(self.config)['step'],5)
    def test_invalid_request_is_atomic(self):
        update(self.config,{'action':'start'});before=self.config.path.read_bytes()
        for v in [{'action':'delete'}, {'action':'goto','step':True},{'action':'complete','step':6},{'action':'goto','step':'1'}]:
            with self.assertRaises(Problem):update(self.config,v)
            self.assertEqual(self.config.path.read_bytes(),before)
    def test_malformed_stored_progress_is_sanitized(self):
        self.config.update({KEY:{'step':True,'completed':[True,5,5,-1,'secret'],'active':'yes'}})
        self.assertEqual(read(self.config),{'step':0,'completed':[5],'active':False})

class GuideHTTPTests(unittest.TestCase):
    setUp = test_model_setup.SetupHttpTests.setUp
    request = test_model_setup.SetupHttpTests.request
    close_server = test_model_setup.SetupHttpTests.close_server
    def test_guide_progress_auth_and_no_content_mutation(self):
        before=self.request('/state')
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/usage-guide',{'action':'start'},False)
        self.assertEqual(cm.exception.code,403)
        for i in range(6):self.request('/usage-guide',{'action':'complete','step':i})
        self.assertEqual(self.request('/usage-guide')['completed'],list(range(6)))
        after=self.request('/state');self.assertEqual(before['posts'],after['posts']);self.assertEqual(before['jobs'],after['jobs']);self.assertEqual(before['channels'],after['channels'])
    def test_invalid_http_step(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/usage-guide',{'action':'goto','step':-1})
        self.assertEqual(cm.exception.code,400)

if __name__=='__main__':unittest.main()
