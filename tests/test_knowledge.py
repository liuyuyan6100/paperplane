import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from knowledge.service import Archive, Problem, digest
from knowledge.provider import ProviderError
from knowledge.server import Server


TOKEN = 'archive-test-' + 'x' * 40


class FakeFeishu:
    def __init__(self):
        self.files = {}
        self.calls = 0
        self.upload_error = None
        self.corrupt = False
        self.private = True

    def privacy(self, project):
        if not self.private:
            raise ProviderError('target_not_private')

    def upload(self, path, name, project):
        self.calls += 1
        token = 'file' + str(self.calls)
        self.files[token] = (Path(path).read_bytes(),name)
        if self.upload_error:
            raise self.upload_error
        return token

    def download(self, token, path):
        Path(path).write_bytes(b'bad' if self.corrupt else self.files[token][0])

    def nodes(self, project):
        return [{'obj_type':'file','obj_token':k,'node_token':'wiki-' + k,'title':v[1]}
                for k,v in self.files.items()]


class ArchiveFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeFeishu()
        self.agent = {'id':'windows-agent','token_sha256':digest(TOKEN.encode()),
                      'projects':['memory'],'permissions':['read','write']}
        self.admin = dict(self.agent,id='admin',permissions=['read','write','admin'])
        self.config = {'projects':{'memory':{'name':'Memory','space_id':'1','node_token':'node','wiki_origin':'https://my.feishu.cn'},
                                   'other':{'name':'Other','space_id':'2','node_token':'node2','wiki_origin':'https://my.feishu.cn'}},
                       'agents':[self.agent], 'spool_limit_bytes':100_000}
        self.app = Archive(self.temp.name,self.config,self.provider,autostart=False)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def payload(self, **updates):
        raw = b'# Agent memory\nA factual claim requires evidence.\n'
        data = {'project':'memory','topic':'Recall','title':'Learning note','filename':'note.md',
                'kind':'learning_note','source':'local:test/note.md','provenance':'original',
                'environment':'test fixture','evidence':[],'request_id':'request-001',
                'sha256':digest(raw),'content_base64':base64.b64encode(raw).decode()}
        data.update(updates)
        return data

    def assertProblem(self,code,fn):
        with self.assertRaises(Problem) as context:
            fn()
        self.assertEqual(context.exception.code,code)


class ArchiveTests(ArchiveFixture, unittest.TestCase):

    def test_roundtrip_reclaims_spool_and_never_asserts_factual_truth(self):
        receipt = self.app.submit(self.agent,self.payload())
        self.assertEqual(receipt['status'],'queued')
        self.assertTrue(self.app.run_once())
        done = self.app.get(self.agent,receipt['id'])
        self.assertEqual(done['status'],'archived')
        self.assertEqual(done['review_status'],'unverified')
        self.assertFalse(done['published_to_wiki'])
        self.assertEqual(list(self.app.spool.iterdir()),[])
        raw,_,sha = self.app.download(self.agent,receipt['id'])
        self.assertEqual(digest(raw),sha)
        self.assertEqual(list(self.app.downloads.iterdir()),[])

    def test_idempotency_request_conflict_and_concurrent_agents(self):
        receipts = []
        errors = []
        def submit(i):
            try:
                receipts.append(self.app.submit(dict(self.agent,id='agent-' + str(i)),self.payload()))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=submit,args=(i,)) for i in range(6)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertFalse(errors)
        self.assertEqual(len({r['id'] for r in receipts}),1)
        self.app.run_once()
        self.assertEqual(self.provider.calls,1)
        self.app.submit(self.agent,self.payload())
        self.assertProblem('request_id_conflict',lambda:self.app.submit(self.agent,self.payload(title='Changed')))

    def test_acl_read_write_download_retry_and_listing(self):
        self.assertProblem('authentication_required',lambda:self.app.authenticate('wrong'))
        self.assertEqual(self.app.authenticate(TOKEN)['id'],self.agent['id'])
        self.assertProblem('project_not_found',lambda:self.app.submit(self.agent,self.payload(project='other')))
        self.assertProblem('permission_denied',lambda:self.app.submit(dict(self.agent,permissions=['read']),self.payload()))
        row = self.app.submit(self.agent,self.payload())
        outsider = dict(self.agent,projects=['other'])
        for op in (lambda:self.app.get(outsider,row['id']),lambda:self.app.download(outsider,row['id']),
                   lambda:self.app.events(outsider,row['id']),lambda:self.app.listing(outsider,'memory')):
            self.assertProblem('project_not_found',op)
        self.assertProblem('permission_denied',lambda:self.app.retry(self.agent,row['id']))

    def test_validation_hash_path_secrets_and_wiki_review(self):
        self.assertProblem('checksum_mismatch',lambda:self.app.submit(self.agent,self.payload(sha256='0'*64)))
        self.assertProblem('invalid_filename',lambda:self.app.submit(self.agent,self.payload(filename='../escape.md')))
        self.assertProblem('derived_material_required',lambda:self.app.submit(self.agent,self.payload(kind='ai_note')))
        self.assertProblem('wiki_candidate_requires_evidence',lambda:self.app.submit(self.agent,self.payload(kind='wiki_candidate',provenance='derived')))
        raw = b'-----BEGIN OPENSSH PRIVATE KEY-----\nfake-test-only'
        self.assertProblem('possible_secret_requires_local_review',lambda:self.app.submit(self.agent,self.payload(
            content_base64=base64.b64encode(raw).decode(),sha256=digest(raw))))
        row = self.app.submit(self.agent,self.payload(kind='wiki_candidate',provenance='derived',evidence=['test:log-001']))
        self.app.run_once()
        receipt = self.app.get(self.agent,row['id'])
        self.assertEqual(receipt['review_status'],'pending')
        self.assertEqual(receipt['destination'],'wiki_review_queue')
        self.assertFalse(receipt['published_to_wiki'])

    def test_unknown_upload_result_reconciles_without_duplicate_upload(self):
        self.provider.upload_error = ProviderError('timeout',uncertain=True)
        row = self.app.submit(self.agent,self.payload())
        self.app.run_once()
        self.assertEqual(self.app.get(self.agent,row['id'])['status'],'needs_attention')
        self.assertFalse(self.app.run_once())
        self.assertProblem('retry_requires_failed_state',lambda:self.app.retry(self.admin,row['id']))
        self.app.reconcile(self.admin,row['id'])
        self.app.run_once()
        self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.app.get(self.agent,row['id'])['status'],'archived')

    def test_download_corruption_never_marks_archived_and_retry_reuses_token(self):
        row = self.app.submit(self.agent,self.payload())
        self.provider.corrupt = True
        for _ in range(3):
            self.app._set(row['id'],next_attempt=0)
            self.app.run_once()
        done = self.app.get(self.agent,row['id'])
        self.assertEqual(done['status'],'failed')
        self.assertEqual(done['error'],'remote_checksum_mismatch')
        self.assertEqual(self.provider.calls,1)
        self.assertTrue(list(self.app.spool.iterdir()))
        self.provider.corrupt = False
        self.app.retry(self.admin,row['id'])
        self.app.run_once()
        self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.app.get(self.agent,row['id'])['status'],'archived')

    def test_restart_preserves_queued_and_quarantines_uploading(self):
        first = self.app.submit(self.agent,self.payload())
        second = self.app.submit(self.agent,self.payload(request_id='two',title='Second'))
        self.app._set(second['id'],status='uploading')
        self.app.close()
        self.app = Archive(self.temp.name,self.config,self.provider,autostart=False)
        self.assertEqual(self.app.get(self.agent,second['id'])['status'],'needs_attention')
        self.app.run_once()
        self.assertEqual(self.app.get(self.agent,first['id'])['status'],'archived')
        self.assertEqual(self.provider.calls,1)

    def test_immutable_version_conflicts_and_provenance_preserved(self):
        row = self.app.submit(self.agent,self.payload())
        raw = b'version 2'
        data = self.payload(request_id='version-2',supersedes_id=row['id'],expected_version=1,
                            content_base64=base64.b64encode(raw).decode(),sha256=digest(raw))
        v2 = self.app.submit(self.agent,data)
        self.assertEqual(v2['version'],2)
        self.assertEqual(self.app.get(self.agent,row['id'])['version'],1)
        self.assertProblem('version_conflict',lambda:self.app.submit(self.agent,dict(data,request_id='conflicting-editor')))
        other = self.app.submit(self.agent,self.payload(request_id='other-source',source='different-source'))
        self.assertNotEqual(other['id'],row['id'])
        self.assertEqual(self.app.get(self.agent,other['id'])['source'],'different-source')

    def test_spool_capacity_and_private_target_guard(self):
        self.app.spool_limit = 1
        self.assertProblem('spool_full_pending_files_preserved',lambda:self.app.submit(self.agent,self.payload()))
        self.app.spool_limit = 100000
        self.provider.private = False
        row = self.app.submit(self.agent,self.payload())
        self.app.run_once()
        self.assertEqual(self.app.get(self.agent,row['id'])['error'],'target_not_private')
        self.assertEqual(self.provider.calls,0)

    def test_same_bytes_from_different_sources_keep_receipts_and_reuse_storage(self):
        first = self.app.submit(self.agent,self.payload())
        self.app.run_once()
        second = self.app.submit(self.agent,self.payload(request_id='different-provenance',source='second-source'))
        self.app.run_once()
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.app.get(self.agent,second['id'])['source'],'second-source')
        self.assertEqual(self.app.get(self.agent,second['id'])['status'],'archived')


class HTTPTests(ArchiveFixture, unittest.TestCase):
    def test_http_auth_body_bounds_browser_rejection_and_roundtrip(self):
        self.server = Server(('127.0.0.1',0),self.app)
        thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        def request(method,path,value=None,token=TOKEN,extra=None):
            headers = {'Authorization':'Bearer ' + token,'Content-Type':'application/json'}
            headers.update(extra or {})
            c = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
            c.request(method,path,json.dumps(value) if value is not None else None,headers)
            r = c.getresponse()
            result = (r.status,r.read(),dict(r.headers))
            c.close()
            return result
        try:
            self.assertEqual(request('GET','/v1/rules',token='invalid')[0],401)
            self.assertEqual(request('GET','/v1/rules',extra={'Origin':'https://attacker.test'})[0],403)
            self.assertEqual(request('GET','/v1/items?project=other')[0],404)
            self.assertEqual(request('POST','/v1/items',[],extra={'Content-Type':'text/plain'})[0],415)
            self.assertEqual(request('POST','/v1/items',[])[0],400)
            status,raw,_ = request('POST','/v1/items',self.payload())
            self.assertEqual(status,202)
            row = json.loads(raw)
            self.app.run_once()
            status,raw,headers = request('GET','/v1/items/' + row['id'] + '/content')
            self.assertEqual(status,200)
            self.assertEqual(digest(raw),headers['X-Content-SHA256'])
            self.assertEqual(headers['Content-Type'],'application/octet-stream')
            self.assertEqual(request('POST','/v1/items/' + row['id'] + '/retry',{})[0],403)
        finally:
            self.server.shutdown()
            self.server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
