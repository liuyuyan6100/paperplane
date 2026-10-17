import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from studio.service import Studio, Problem
from studio.chat import ChatManager, parse
from studio.db import uid, dumps
from studio import profiles
from studio.server import Server
from studio.adapters import DeliveryError
from studio.migration import backup_bundle
from tools.restore_studio import restore


def tool(name, **arguments):
    return dumps({'reply': '准备执行', 'tool': {'name': name, 'arguments': arguments}})


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.app = Studio(self.root / 'data'); self.addCleanup(self.app.close)
        self.app.config.update({'AI_MODE': 'api', 'AI_BASE_URL': 'http://127.0.0.1:9999/v1', 'AI_MODEL': 'synthetic-model'})
        self.profile = profiles.save(self.app, {'name': '合成画像'})
        self.post = self.app.save_post({'title': '07-合成文章', 'body': '合成素材', 'profile_id': self.profile['id']})
        self.chat = self.app.chat.create({'profile_id': self.profile['id']})

    def send(self, message='为07生成提纲', request=None):
        return self.app.chat.send(self.chat['id'], {'message': message, 'request_id': request or uid()})

    def complete(self, outputs):
        outputs = iter(outputs)
        self.app.generate_draft = lambda *a, **k: next(outputs)
        self.assertTrue(self.app.chat.tick())
        return self.app.chat.get(self.chat['id'])

    def test_send_idempotent_and_no_concurrent_turn(self):
        request = uid(); self.send(request=request); self.send(request=request)
        self.assertEqual(len(self.app.chat.get(self.chat['id'])['turns']), 1)
        with self.assertRaises(Problem): self.send('其他安排', request)
        with self.assertRaises(Problem): self.send('另一条')
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM workflows')['n'], 0)

    def test_dispatch_real_outline_and_explicit_confirm_then_adopt(self):
        self.send()
        chat = self.complete([tool('read_post', post_id=self.post['id']), tool('start_outline', post_id=self.post['id'],
            post_version=1, reference_ids=[], instruction='按合成素材生成提纲')])
        self.assertEqual(chat['turns'][-1]['status'], 'completed')
        self.assertEqual(len(chat['tasks']), 1)
        wid = chat['tasks'][0]['id']
        self.app.generate_draft = lambda *a, **k: '## 第一节\n\n合成提纲'
        self.app.workflows.tick()
        task = self.app.chat.get(chat['id'])['tasks'][0]
        self.assertEqual(task['status'], 'outline_review')
        self.assertEqual(self.app.post(self.post['id'])['body'], '合成素材')
        self.app.chat.action(chat['id'], {'action': 'confirm_outline', 'id': wid,
            'outline': task['outline'], 'outline_version': task['outline_version']})
        self.app.generate_draft = lambda *a, **k: '## 合成正文\n\n这是待审稿。'
        self.app.workflows.tick()
        task = self.app.chat.get(chat['id'])['tasks'][0]
        self.assertEqual(task['status'], 'review')
        self.assertEqual(self.app.post(self.post['id'])['body'], '合成素材')
        self.app.chat.action(chat['id'], {'action': 'adopt_body', 'id': wid, 'post_version': 1})
        self.assertIn('这是待审稿', self.app.post(self.post['id'])['body'])
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM jobs')['n'], 0)
        self.app.chat.action(chat['id'], {'action': 'adopt_body', 'id': wid, 'post_version': 1})
        self.assertEqual(self.app.post(self.post['id'])['version'], 2)

    def test_target_version_and_chat_ownership_protect_manuscript(self):
        self.send(); chat = self.complete([tool('start_outline', post_id=self.post['id'], post_version=1, reference_ids=[], instruction='合成')])
        wid = chat['tasks'][0]['id']
        other = self.app.chat.create({})
        with self.assertRaises(Problem): self.app.chat.action(other['id'], {'action': 'confirm_outline', 'id': wid})
        self.app.generate_draft = lambda *a, **k: '## 合成提纲'
        self.app.workflows.tick(); task = self.app.chat.get(chat['id'])['tasks'][0]
        self.app.save_post({**self.post, 'body': '人工新内容'}, self.post['id'])
        with self.assertRaises(Problem): self.app.chat.action(chat['id'], {'action': 'confirm_outline', 'id': wid,
            'outline': task['outline'], 'outline_version': task['outline_version']})
        self.assertEqual(self.app.post(self.post['id'])['body'], '人工新内容')

    def test_invalid_and_out_of_scope_tools_never_execute(self):
        for output in [tool('publish', post_id=self.post['id']), tool('start_review', post_id=self.post['id'], post_version=True),
                       tool('create_idea', title='坏请求', note='', body='不允许的参数'), 'invalid json', dumps({'reply': '', 'tool': None})]:
            with self.assertRaises(DeliveryError): parse(output)
        self.send(); chat = self.complete([tool('delete_all')])
        self.assertEqual(chat['turns'][-1]['status'], 'failed')
        self.assertEqual(self.app.post(self.post['id'])['version'], 1)

    def test_partial_actions_survive_model_failure_and_repeat_message(self):
        self.send('新建选题再找资料')
        chat = self.complete([tool('create_idea', title='合成新选题', note='合成内容'), 'invalid'])
        self.assertEqual(chat['turns'][-1]['status'], 'failed')
        self.assertEqual(chat['turns'][-1]['steps'][0]['status'], 'completed')
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM posts')['n'], 2)
        self.app.chat.interrupt(); self.assertFalse(self.app.chat.tick())

    def test_multiturn_history_and_receipts_go_to_planner_without_key(self):
        self.app.config.update({'AI_API_KEY': 'synthetic-private-value'})
        self.send('找到07'); self.complete([tool('read_post', post_id=self.post['id']), dumps({'reply': '找到了', 'tool': None})])
        self.send('先做提纲')
        prompts=[]
        def generate(post, instruction, **kwargs):
            prompts.append(post); return dumps({'reply': '请补充本篇重点', 'tool': None})
        self.app.generate_draft=generate; self.app.chat.tick()
        self.assertIn('找到07', prompts[0]['body']); self.assertIn('找到了', prompts[0]['body'])
        self.assertEqual(prompts[0]['_model_task'], 'chat')
        self.assertNotIn('synthetic-private-value', dumps(self.app.chat.get(self.chat['id'])))

    def test_library_lookup_import_and_duplicate_reuse(self):
        item_id=uid(); item={'id':item_id,'name':'合成笔记.md','version':1,'source':'合成来源','relative_path':'notes/合成笔记.md','sha256':'0'*64}
        self.send('从库里找笔记并取材')
        with patch('studio.shared_library.listing',return_value={'items':[item],'total':1}), patch('studio.shared_library.material',return_value=(item,'合成笔记'.encode())):
            chat=self.complete([tool('search_library',query='合成'),tool('import_library',item_id=item_id,version=1),dumps({'reply':'已取材','tool':None})])
            self.assertEqual(chat['turns'][-1]['steps'][1]['result']['post']['version'],1)
            self.send('继续取这份素材')
            chat=self.complete([tool('search_library',query='合成'),tool('import_library',item_id=item_id,version=1),dumps({'reply':'复用','tool':None})])
            self.assertTrue(chat['turns'][-1]['steps'][1]['result']['reused'])
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM posts')['n'],2)

    def test_import_requires_lookup_and_does_not_execute_source_instructions(self):
        self.send('查资料');chat=self.complete([tool('import_library',item_id=uid(),version=1),dumps({'reply':'请先查资料','tool':None})])
        self.assertEqual(chat['turns'][-1]['steps'][0]['status'],'failed')
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM posts')['n'],1)

    def test_stop_during_model_prevents_late_tools_and_replay(self):
        self.send()
        def generate(*a,**k):
            self.app.chat.stop();return tool('create_idea',title='不该创建',note='不该执行')
        self.app.generate_draft=generate;self.app.chat.tick()
        chat=self.app.chat.get(self.chat['id']);self.assertEqual(chat['turns'][-1]['status'],'interrupted')
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM posts')['n'],1)
        restarted=ChatManager(self.app);self.assertFalse(restarted.tick())

    def test_backup_restore_keeps_chat_but_never_replays_queued_turn(self):
        self.send();archive=self.root/'backup.zip';archive.write_bytes(backup_bundle(self.app))
        restored=self.root/'restored';restored.mkdir();restore(archive,restored)
        app=Studio(restored);self.addCleanup(app.close)
        chat=app.chat.get(self.chat['id']);self.assertEqual(chat['turns'][-1]['status'],'interrupted')
        self.assertFalse(app.chat.tick())

    def test_http_routes_authentication_and_no_publication_action(self):
        server=Server(('127.0.0.1',0),self.app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
        base=f'http://127.0.0.1:{server.server_port}'
        def req(path,body=None,token=''):
            return json.load(urllib.request.urlopen(urllib.request.Request(base+path,data=None if body is None else dumps(body).encode(),
                headers={'Content-Type':'application/json','X-Studio-Token':token}),timeout=5))
        self.assertEqual(len(req('/api/chat')['conversations']),1)
        with self.assertRaises(urllib.error.HTTPError):req('/api/chat/'+self.chat['id']+'/messages',{'message':'你好','request_id':uid()})
        chat=req('/api/chat/'+self.chat['id']+'/messages',{'message':'你好','request_id':uid()},server.token)
        self.assertEqual(chat['turns'][-1]['status'],'queued')
        with self.assertRaises(urllib.error.HTTPError):req('/api/chat/'+self.chat['id']+'/action',{'action':'publish','id':uid()},server.token)
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM jobs')['n'],0)


if __name__=='__main__':unittest.main()
