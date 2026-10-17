import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import memories, profiles
from studio.db import uid
from studio.service import Studio, Problem


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name))
        self.addCleanup(self.app.close)
        self.profile = profiles.save(self.app, {'name':'记忆测试账号'})
        self.pid = self.profile['id']

    def candidate(self, content='代码文章保留可复现步骤', **kw):
        return memories.propose(self.app, self.pid, dict(content=content, source_text=content,
                                  quote=content, **kw))

    def change(self, item, action, **kw):
        return memories.update(self.app, self.pid, item['id'], {'action':action,'version':item['version'],**kw})

    def test_lifecycle_cas_history_and_profile_isolation(self):
        m=self.candidate()
        self.assertEqual(memories.recall(self.app,self.pid), [])
        other=profiles.save(self.app, {'name':'其他账号'})
        with self.assertRaises(Problem):
            memories.update(self.app, other['id'], m['id'], {'action':'confirm','version':1})
        active=self.change(m,'confirm')
        with self.assertRaises(Problem) as e:self.change(m,'confirm')
        self.assertEqual(e.exception.status,409)
        self.assertEqual(memories.recall(self.app,other['id']),[])
        edited=self.change(active,'edit',content='改用自然作者口吻',platform='',keywords='')
        self.assertEqual(memories.recall(self.app,self.pid),[])
        active=self.change(edited,'confirm')
        disabled=self.change(active,'disable')
        self.assertEqual(memories.recall(self.app,self.pid),[])
        restored=self.change(disabled,'restore')
        self.assertEqual(restored['status'],'pending')
        deleted=self.change(restored,'delete')
        self.assertEqual(memories.recall(self.app,self.pid),[])
        self.assertEqual(len(memories.revisions(self.app,self.pid,m['id'])),7)
        self.assertEqual(self.change(deleted,'restore')['status'],'pending')

    def test_scope_and_duplicate(self):
        general=self.change(self.candidate(),'confirm')
        xhs=self.change(self.candidate('小红书图集不要放博客链接',platform='xiaohongshu'),'confirm')
        topic=self.change(self.candidate('记忆主题区分存储与召回',keywords='记忆,Hermes'),'confirm')
        self.assertEqual([m['id'] for m in memories.recall(self.app,self.pid,'别的主题')],[general['id']])
        selected=memories.recall(self.app,self.pid,'Hermes介绍','xiaohongshu')
        self.assertEqual({m['id'] for m in selected},{general['id'],xhs['id'],topic['id']})
        self.assertEqual(self.candidate()['id'],general['id'])

    def test_task_snapshot_survives_disable_and_new_tasks_stop_using_it(self):
        m=self.change(self.candidate('KEEP_OLD_MEMORY'),'confirm')
        p=self.app.save_post({'title':'快照','profile_id':self.pid})
        task=self.app.workflows.create({'post_id':p['id'],'version':1})
        self.change(m,'disable')
        with patch('studio.adapters.ai_draft',return_value='提纲') as model:self.app.workflows.tick()
        self.assertIn('KEEP_OLD_MEMORY',model.call_args.args[1])
        self.assertEqual(self.app.workflows.get(task['id'])['profile']['recalled_memories'][0]['version'],2)
        newer=self.app.workflows.create({'post_id':p['id'],'version':1})
        self.assertEqual(newer['profile']['recalled_memories'],[])

    def test_chat_frozen_memory_and_candidate_tool_cannot_confirm(self):
        m=self.change(self.candidate('CHAT_MEMORY'),'confirm')
        c=self.app.chat.create({'profile_id':self.pid})
        sent=self.app.chat.send(c['id'],{'request_id':uid(),'message':'以后不要使用夸张标题'})
        t=sent['turns'][0]
        self.change(m,'disable')
        response=json.dumps({'reply':'收到','tool':None},ensure_ascii=False)
        with patch('studio.adapters.ai_draft',return_value=response) as model:self.app.chat.tick()
        self.assertIn('CHAT_MEMORY',model.call_args.args[1])
        step=uid()
        result=self.app.chat.dispatch({'name':'propose_memory','arguments':{'content':'不要使用夸张标题','quote':t['message']}},step,
                    {'profile_id':self.pid,'turn_id':t['id']})
        self.assertEqual(result['memory']['status'],'pending')
        self.assertEqual(result['memory']['source']['id'],t['id'])
        from studio.chat import TOOLS
        self.assertNotIn('confirm_memory',TOOLS)
        self.assertEqual(memories.recall(self.app,self.pid),[])

    def test_fabricated_quote_and_wrong_source_rejected(self):
        with self.assertRaises(Problem):
            memories.propose(self.app,self.pid,{'content':'推断','source_text':'原文','quote':'不存在'})
        other=profiles.save(self.app,{'name':'其他'})
        p=self.app.save_post({'title':'他人资料','profile_id':other['id'],'body':'原句'})
        with self.assertRaises(Problem):
            memories.source(self.app,self.pid,{'source_type':'post','source_id':p['id'],'source_version':1})
        self.assertEqual(memories.listing(self.app,self.pid),[])

    def test_post_source_freezes_version_and_excerpt(self):
        p=self.app.save_post({'title':'复盘','body':'数据不足，不推断涨粉因果。','profile_id':self.pid})
        values={'source_type':'post','source_id':p['id'],'source_version':1,'quote':p['body'],'content':'复盘不推断涨粉因果'}
        m=memories.propose(self.app,self.pid,values)
        self.app.save_post({'version':1,'body':'新正文'},p['id'])
        self.assertEqual(m['source']['text'],p['body'])
        with self.assertRaises(Problem):memories.propose(self.app,self.pid,{**values,'content':'另一个经验'})

    def test_extraction_validates_whole_batch_before_writing(self):
        valid={'content':'作者口吻','quote':'保留作者口吻','keywords':''}
        raw=json.dumps({'candidates':[valid,{**valid,'quote':'编造出处'}]},ensure_ascii=False)
        with patch.object(self.app,'generate_draft',return_value=raw),self.assertRaises(Problem):
            memories.extract(self.app,self.pid,{'source_text':'保留作者口吻'})
        self.assertEqual(memories.listing(self.app,self.pid),[])
        with patch.object(self.app,'generate_draft',return_value=json.dumps({'candidates':[valid]})):
            items=memories.extract(self.app,self.pid,{'source_text':'保留作者口吻'})['memories']
        self.assertEqual(items[0]['status'],'pending')
        self.assertEqual(memories.recall(self.app,self.pid),[])

    def test_persistence_and_profile_sync_keeps_memory_local(self):
        m=self.change(self.candidate(),'confirm')
        from studio.profile_sync import records
        self.assertNotIn('recalled_memories',records(self.app)[0])
        self.app.close()
        other=Studio(Path(self.temp.name))
        try:self.assertEqual(memories.recall(other,self.pid)[0]['id'],m['id'])
        finally:other.close()

    def test_platform_variants_capture_different_scopes(self):
        x=self.change(self.candidate('XHS_ONLY',platform='xiaohongshu'),'confirm')
        self.change(self.candidate('DEV_ONLY',platform='devto'),'confirm')
        p=self.app.save_post({'title':'母稿','body':'可用正文','stage':'draft','profile_id':self.pid})
        variants=self.app.repurpose.create({'post_id':p['id'],'post_version':p['version'],'platforms':['xiaohongshu','devto']})['variants']
        self.assertEqual(variants[0]['context']['profile']['recalled_memories'][0]['id'],x['id'])
        self.assertNotIn('XHS_ONLY',json.dumps(variants[1]['context']['profile']))

    def test_over_budget_is_explicit_and_no_partial_recall(self):
        for i in range(31):self.change(self.candidate('经验'+str(i)),'confirm')
        with self.assertRaises(Problem) as e:memories.recall(self.app,self.pid)
        self.assertEqual(e.exception.status,409)

    def test_backup_restores_source_history_and_scope(self):
        from studio.migration import backup_bundle
        from tools.restore_studio import restore
        m=self.change(self.candidate('恢复后仍可追溯来源',keywords='恢复'),'confirm')
        archive=Path(self.temp.name)/'memory-backup.zip'
        archive.write_bytes(backup_bundle(self.app))
        destination=Path(self.temp.name)/'restored'
        destination.mkdir()
        restore(archive,destination)
        restored=Studio(destination)
        try:
            entries=memories.recall(restored,self.pid,'恢复')
            self.assertEqual(entries[0]['source']['quote'],m['content'])
            self.assertEqual(len(memories.revisions(restored,self.pid,m['id'])),2)
        finally:restored.close()

    def test_http_writes_require_token_and_never_trust_status_from_model(self):
        import threading
        import urllib.request
        import urllib.error
        from studio.server import Server
        server=Server(('127.0.0.1',0),self.app)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base=f'http://127.0.0.1:{server.server_port}/api/profiles/{self.pid}/memories'
        values={'content':'API反馈','source_text':'API反馈','quote':'API反馈','status':'accepted'}
        def call(url,data,token):
            request=urllib.request.Request(url,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Studio-Token':token})
            with opener.open(request) as response:return json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as error:call(base,values,'bad')
        self.assertEqual(error.exception.code,403)
        self.assertEqual(memories.listing(self.app,self.pid),[])
        candidate=call(base,values,server.token)
        self.assertEqual(candidate['status'],'pending')
        call(base+'/'+candidate['id'],{'action':'confirm','version':1},server.token)
        self.assertEqual(len(memories.recall(self.app,self.pid)),1)


if __name__=='__main__':unittest.main()
