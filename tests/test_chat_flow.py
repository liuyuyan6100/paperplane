import json
from unittest.mock import patch
from tests.test_chat import ChatTests, tool
from tests.test_topics import response
from tests.test_reviews import advice
from studio import profiles
from studio.db import uid, dumps
from studio.service import Studio, Problem
from studio.adapters import DeliveryError


class ChatFlowTests(ChatTests):
    def test_current_request_is_not_mislabeled_as_history(self):
        self.send('之前讨论的是开发者读者')
        self.complete([dumps({'reply':'已收到','tool':None})])
        self.send('本篇给朋友入门，先用已有研究生成提纲')
        prompts=[]
        def generate(post,*args,**kwargs):
            prompts.append(json.loads(post['body'])['context'])
            return dumps({'reply':'本篇按入门要求处理','tool':None})
        self.app.generate_draft=generate
        self.app.chat.tick()
        self.assertEqual(prompts[0]['current_message'],'本篇给朋友入门，先用已有研究生成提纲')
        self.assertEqual([t['user'] for t in prompts[0]['history']],['之前讨论的是开发者读者'])

    def test_multiple_sources_can_reach_outline_without_exhausting_turn(self):
        items=[{'id':uid(),'version':1,'name':f'research-{i}.md','source':'合成','relative_path':f'{i}.md','sha256':'0'*64} for i in range(4)]
        by_id={i['id']:i for i in items}
        self.send('基于四份研究安排提纲')
        outputs=[tool('search_library',query='本地模型')]
        for item in items:
            outputs.extend([tool('read_library',item_id=item['id'],version=1),tool('import_library',item_id=item['id'],version=1)])
        outputs.extend([tool('read_post',post_id=self.post['id']),tool('start_outline',post_id=self.post['id'],post_version=1,instruction='基于四份已读资料生成提纲',reference_ids=[i['id'] for i in items])])
        with patch('studio.shared_library.listing',return_value={'items':items,'total':4}),patch('studio.shared_library.material',side_effect=lambda iid,v:(by_id[iid],b'synthetic evidence')):
            result=self.complete(outputs)
        self.assertEqual(len(result['turns'][-1]['steps']),11)
        self.assertEqual(len(result['tasks']),1)
        task=self.app.workflows.get(result['tasks'][0]['id'])
        self.assertEqual(len(task['reference_ids']),4)
        self.assertEqual(task['status'],'queued')
        self.assertEqual(self.app.post(self.post['id'])['body'],'合成素材')

    def test_default_profile_and_switch_snapshots(self):
        first=self.app.chat.create({})
        self.assertEqual(first['profile_id'],self.profile['id'])
        other=profiles.save(self.app,{'name':'另一个账号'})
        self.app.chat.set_profile(first['id'],{'profile_id':other['id']})
        self.assertEqual(self.app.chat.create({})['profile_id'],other['id'])
        self.send('旧画像消息')
        with self.assertRaises(Problem):self.app.chat.set_profile(self.chat['id'],{'profile_id':other['id']})
        self.complete([dumps({'reply':'已收到','tool':None})])
        changed=self.app.chat.set_profile(self.chat['id'],{'profile_id':other['id']})
        self.assertEqual(changed['turns'][0]['profile_snapshot']['id'],self.profile['id'])

    def test_empty_search_falls_back_and_reads_source(self):
        iid=uid();item={'id':iid,'version':1,'name':'Hermes.md','source':'合成','relative_path':'Hermes.md'}
        self.send('从资料库写07，智能体记忆，先提纲')
        with patch('studio.shared_library.listing',side_effect=lambda q:{'items':[] if q else [item],'total':1}),patch('studio.shared_library.material',return_value=(item,'原始记忆研究证据'.encode())):
            result=self.complete([tool('search_library',query='智能体记忆'),tool('read_library',item_id=iid,version=1),dumps({'reply':'已阅读原文','tool':None})])
        steps=result['turns'][0]['steps']
        self.assertTrue(steps[0]['result']['catalogue_fallback'])
        self.assertEqual(steps[0]['result']['matched'],0)
        self.assertEqual(steps[1]['result']['body'],'原始记忆研究证据')

    def test_invalid_json_is_repaired_before_any_action(self):
        self.send('你好')
        result=self.complete(['invalid',dumps({'reply':'已修正格式','tool':None})])
        self.assertEqual(result['turns'][-1]['status'],'completed')
        self.assertEqual(result['turns'][-1]['steps'],[])

    def test_imported_library_id_resolves_to_reference_post(self):
        iid=uid();context={'profile_id':self.profile['id'],'conversation_id':self.chat['id'],'library_items':{iid:1}}
        item={'id':iid,'name':'notes.md','source':'合成','relative_path':'notes.md','sha256':'0'*64}
        with patch('studio.shared_library.material',return_value=(item,b'synthetic reference')):
            imported=self.app.chat.dispatch({'name':'import_library','arguments':{'item_id':iid,'version':1}},uid(),context)
        result=self.app.chat.dispatch({'name':'start_outline','arguments':{'post_id':self.post['id'],'post_version':1,'reference_ids':[iid],'instruction':'合成'}},uid(),context)
        workflow=self.app.workflows.get(result['tasks'][0]['id'])
        self.assertEqual(workflow['reference_ids'],[imported['post']['id']])

    def test_topic_to_body_review_repurpose_and_images(self):
        plan=self.app.topics.create({'profile_id':self.profile['id'],'idea':'写07记忆研究','reference_ids':[self.post['id']]})
        self.app.chat.link(self.chat['id'],'topic',plan['id'])
        self.app.generate_draft=lambda *a,**k:response();self.app.topics.tick()
        select={'action':'select_topic','kind':'topic','id':plan['id'],'index':0,'request_id':uid()}
        self.app.chat.action(self.chat['id'],select);self.app.chat.action(self.chat['id'],select)
        tasks=self.app.chat.get(self.chat['id'])['tasks'];w=next(t for t in tasks if t['kind']=='workflow')
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM workflows')['n'],1)
        self.assertIn(self.post['id'],[r['id'] for r in self.app.workflows.get(w['id'])['topic']['references']])
        self.app.generate_draft=lambda *a,**k:'## 合成提纲';self.app.workflows.tick()
        w=self.app.chat.task('workflow',w['id'])
        self.app.chat.action(self.chat['id'],{'action':'confirm_outline','id':w['id'],'outline':w['outline'],'outline_version':w['outline_version']})
        self.app.generate_draft=lambda *a,**k:'原句需要简洁。';self.app.workflows.tick()
        self.app.chat.action(self.chat['id'],{'action':'adopt_body','id':w['id'],'post_version':1})
        follow={'action':'start_review','id':w['id'],'post_version':2,'request_id':uid()}
        self.app.chat.action(self.chat['id'],follow);self.app.chat.action(self.chat['id'],follow)
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM editorial_reviews')['n'],1)
        self.app.generate_draft=lambda *a,**k:dumps(advice());self.app.reviews.tick()
        r=next(t for t in self.app.chat.get(self.chat['id'])['tasks'] if t['kind']=='review')
        self.app.chat.action(self.chat['id'],{'action':'apply_review','kind':'review','id':r['id'],'post_version':2,'issues':[{'id':'0'}]})
        self.assertEqual(self.app.post(w['post_id'])['body'],'原句可以简洁。')
        self.app.chat.action(self.chat['id'],{'action':'start_repurpose','id':w['id'],'post_version':3,'platforms':['csdn']})
        self.app.generate_draft=lambda *a,**k:dumps({'title':'平台稿','summary':'','body':'改编正文','tags':[]});self.app.repurpose.tick()
        self.app.chat.action(self.chat['id'],{'action':'plan_images','id':w['id'],'post_version':3})
        self.app.generate_draft=lambda *a,**k:dumps([{'title':'记忆流程','text':'','prompt':'说明流程','slot':'body','aspect':'landscape'}]);self.app.illustrations.tick()
        tasks=self.app.chat.get(self.chat['id'])['tasks']
        self.assertEqual(next(t for t in tasks if t['kind']=='repurpose')['status'],'review')
        self.assertEqual(next(t for t in tasks if t['kind']=='illustration')['status'],'review')
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM jobs')['n'],0)
        self.send('接下来做什么')
        prompts=[]
        def generate(post,*a,**k):
            prompts.append(json.loads(post['body']));return dumps({'reply':'已有审稿、平台稿和配图规划','tool':None})
        self.app.generate_draft=generate;self.app.chat.tick()
        self.assertTrue(prompts[0]['context']['confirmed_actions'])
        self.assertEqual(next(t for t in prompts[0]['context']['current_tasks'] if t['kind']=='workflow')['current_post']['version'],3)

    def test_api_transient_retry_and_auth_no_retry(self):
        post={'title':'测试','body':'合成'}
        with patch('studio.adapters.ai_draft',side_effect=[DeliveryError('HTTP502',retryable=True),'恢复成功']) as call:
            self.assertEqual(Studio.generate_draft(self.app,post,'测试'),'恢复成功')
            self.assertEqual(call.call_count,2)
        with patch('studio.adapters.ai_draft',side_effect=DeliveryError('HTTP401')) as call:
            with self.assertRaises(DeliveryError):Studio.generate_draft(self.app,post,'测试')
            self.assertEqual(call.call_count,1)
