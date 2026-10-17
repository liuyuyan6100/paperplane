import unittest
from unittest.mock import patch
from tests.test_chat import ChatTests as Base
from studio.db import uid
from studio.service import Problem

class HistoryTests(Base):
    def test_chat_requests_json_mode_without_changing_other_structured_tasks(self):
        from studio.adapters import ai_draft
        config={'AI_BASE_URL':'http://localhost:9999/v1','AI_MODEL':'test'}
        for task in ['chat','illustration_plan']:
            with patch('studio.adapters.request_json',return_value={'choices':[{'message':{'content':'{}'}}]}) as request:
                ai_draft({'title':'合成','summary':'','body':'','_model_task':task},'合成',config,structured=True)
                payload=request.call_args.args[1]
                self.assertEqual(payload.get('response_format'),{'type':'json_object'} if task=='chat' else None)

    def test_archive_delete_restore_keep_content_and_links(self):
        cid=self.chat['id']
        w=self.app.workflows.create({'post_id':self.post['id'],'version':1,'instruction':'合成提纲'})
        self.app.chat.link(cid,'workflow',w['id'])
        self.app.chat.organize(cid,{'action':'archive'})
        self.assertEqual(self.app.chat.listing(),[])
        self.assertEqual(self.app.chat.listing('archived')[0]['id'],cid)
        with self.assertRaises(Problem):self.send()
        self.app.chat.organize(cid,{'action':'delete'})
        self.assertEqual(self.app.chat.listing('deleted')[0]['id'],cid)
        self.assertEqual(self.app.post(self.post['id'])['body'],'合成素材')
        self.assertEqual(self.app.chat.get(cid)['tasks'][0]['id'],w['id'])
        self.app.chat.organize(cid,{'action':'restore'})
        self.assertEqual(self.app.chat.listing()[0]['id'],cid)
        self.send()
        with self.assertRaises(Problem):self.app.chat.organize(cid,{'action':'delete'})

    def test_purge_only_trash_and_preserves_generated_content(self):
        cid=self.chat['id']
        with self.assertRaises(Problem): self.app.chat.organize(cid, {'action':'purge'})
        task=self.app.workflows.create({'post_id':self.post['id'],'version':1,'instruction':'合成提纲'})
        self.app.chat.link(cid,'workflow',task['id'])
        self.send()
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE conversation_turns SET status='completed' WHERE conversation_id=?",(cid,))
        self.app.chat.organize(cid,{'action':'delete'})
        self.app.chat.organize(cid,{'action':'purge'})
        with self.assertRaises(Problem):self.app.chat.get(cid)
        self.assertEqual(self.app.chat.listing('deleted'),[])
        self.assertIsNotNone(self.app.workflows.get(task['id']))
        self.assertEqual(self.app.post(self.post['id'])['body'],'合成素材')
        self.assertEqual(self.app.db.rows('PRAGMA foreign_key_check'),[])

    def test_human_platform_names_map_to_supported_variants(self):
        post=self.app.save_post({**self.post,'stage':'draft'},self.post['id'])
        result=self.app.chat.dispatch({'name':'start_repurpose','arguments':{'post_id':post['id'],'post_version':post['version'],'instruction':'合成改编','platforms':['微信公众号','CSDN','掘金','DEV Community','小红书']}},uid(),{})
        self.assertEqual({t['platform'] for t in result['tasks']},{'csdn','juejin','devto','xiaohongshu'})
        self.assertIn('尚未送稿',result['notice'])
        self.assertEqual(self.app.db.one('select count(*) n from jobs')['n'],0)

if __name__=='__main__':unittest.main()
