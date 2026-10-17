import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import profiles
from studio.db import dumps, uid
from studio.service import Studio, Problem
from studio.adapters import DeliveryError
from studio.writing_rules import apply_footer, footer_satisfied


class EditorAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name)); self.addCleanup(self.app.close)
        self.app.config.update({'AI_MODE':'api','AI_BASE_URL':'http://127.0.0.1:1/v1','AI_MODEL':'synthetic'})
        self.profile = profiles.save(self.app, {'name':'编辑测试账号', 'wechat_footer':'作者博客：https://author.example/blog/'})
        self.post = self.app.save_post({'title':'合成文章','body':'开始😀\n旧段落\n结束','profile_id':self.profile['id'],'stage':'draft'})
        self.manager = self.app.editor_assistant

    def send(self, kind='post', target=None, **extra):
        target = target or self.post['id']
        source = self.manager.get(kind, target)
        return self.manager.send(kind, target, {'request_id':uid(),'message':'修改这一段','mode':'edit',
            'revision':source['revision'],'draft':source['content'], **extra})

    def complete(self, replacement=None, reply='建议已准备'):
        self.app.generate_draft = lambda *a,**k: dumps({'reply':reply,'replacement':replacement})
        self.assertTrue(self.manager.tick())

    def workflow(self):
        task = self.app.workflows.create({'post_id':self.post['id'],'version':self.post['version'],'instruction':'保留事实，先生成提纲','reference_ids':[]})
        self.app.generate_draft = lambda *a,**k:'## 原提纲\n第一节'
        self.app.workflows.tick()
        return self.app.workflows.get(task['id'])

    def test_preview_accept_never_saves_or_publishes(self):
        state = self.send(); before = self.app.post(self.post['id'])
        replacement = {'title':'新标题','body':'改过的完整正文'}
        self.complete(replacement)
        turn = self.manager.get('post', self.post['id'])['turns'][-1]
        self.assertIn('旧段落', turn['result']['diff'])
        result = self.manager.accept(turn['id'], {'draft':turn['draft']})
        self.assertEqual(result['replacement'], replacement)
        self.assertEqual(before, self.app.post(self.post['id']))
        self.assertEqual(self.app.jobs(), [])
        self.assertEqual(self.manager.get('post',self.post['id'])['turns'][-1]['status'],'accepted')

    def test_draft_and_saved_version_conflicts_preserve_suggestion(self):
        self.send(); self.complete({'title':'合成文章','body':'新建议'})
        turn = self.manager.get('post',self.post['id'])['turns'][-1]
        with self.assertRaises(Problem):
            self.manager.accept(turn['id'], {'draft':{**turn['draft'],'body':'人工后改'}})
        self.app.save_post({'version':1,'body':'其他窗口保存'},self.post['id'])
        with self.assertRaises(Problem):
            self.manager.accept(turn['id'], {'draft':turn['draft']})
        state = self.manager.get('post',self.post['id'])
        self.assertTrue(state['turns'][-1]['source_changed'])
        self.assertEqual(state['turns'][-1]['result']['replacement']['body'],'新建议')
        self.assertEqual(self.app.post(self.post['id'])['body'],'其他窗口保存')

    def test_selected_unicode_fragment_changes_only_that_fragment(self):
        body = self.post['body']; start = body.index('旧段落')
        self.send(selection={'end':start+3,'start':start})
        self.complete('新段落')
        turn = self.manager.get('post',self.post['id'])['turns'][-1]
        self.assertEqual(turn['result']['replacement']['body'],'开始😀\n新段落\n结束')
        self.assertEqual(self.app.post(self.post['id'])['body'],body)

    def test_unsaved_empty_body_is_valid_context(self):
        draft = {'title':'合成文章','body':''}
        state = self.send(draft=draft)
        self.assertEqual(state['turns'][-1]['draft'],draft)
        self.complete({'title':'合成文章','body':'新的正文'})

    def test_duplicate_submission_and_busy_work_item(self):
        source = self.manager.get('post',self.post['id'])
        values = {'request_id':uid(),'message':'只改一句','revision':source['revision'],'draft':source['content']}
        first=self.manager.send('post',self.post['id'],values)
        repeated=self.manager.send('post',self.post['id'],values)
        self.assertEqual(first['turns'][0]['id'],repeated['turns'][0]['id'])
        with self.assertRaises(Problem):self.manager.send('post',self.post['id'],{**values,'request_id':uid()})
        with self.assertRaises(Problem):self.manager.send('post',self.post['id'],{**values,'message':'别的内容'})
        self.assertEqual(len(repeated['turns']),1)

    def test_discussion_cannot_offer_adoptable_replacement(self):
        self.send(mode='discuss');self.complete({'title':'合成文章','body':'不能修改'})
        turn=self.manager.get('post',self.post['id'])['turns'][-1]
        self.assertIsNone(turn['result']['replacement'])
        with self.assertRaises(Problem):self.manager.accept(turn['id'],{'draft':turn['draft']})

    def test_history_is_scoped_and_reopen_persists(self):
        self.send(message='第一条问题');self.complete(None,'第一条讨论回复')
        other=self.app.save_post({'title':'别的文章','body':'独立正文','profile_id':self.profile['id']})
        self.assertEqual(self.manager.get('post',other['id'])['turns'],[])
        self.send(message='继续刚才的讨论')
        captured=[]
        def model(post,*a,**kw):
            captured.append(json.loads(post['body']));return dumps({'reply':'第二轮','replacement':None})
        self.app.generate_draft=model;self.manager.tick()
        self.assertEqual(captured[0]['history'],[{'user':'第一条问题','assistant':'第一条讨论回复'}])
        from studio.editor_assistant import EditorAssistant
        self.assertEqual(len(EditorAssistant(self.app).get('post',self.post['id'])['turns']),2)

    def test_refine_keeps_previous_proposal_and_current_unsaved_edits(self):
        self.send();self.complete({'title':'合成文章','body':'上一版建议正文'})
        prior=self.manager.get('post',self.post['id'])['turns'][-1]
        draft={'title':'作者刚改的标题','body':'作者在编辑区的新内容'}
        self.send(message='沿用上版解释，但保留我刚改的内容',draft=draft,reference_turn_id=prior['id'])
        captured=[]
        def model(post,*args,**kwargs):
            captured.append(json.loads(post['body']))
            return dumps({'reply':'已结合两版','replacement':draft})
        self.app.generate_draft=model;self.manager.tick()
        self.assertEqual(captured[0]['draft'],draft)
        self.assertEqual(captured[0]['previous_suggestion']['replacement']['body'],'上一版建议正文')
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
        self.assertEqual(self.app.jobs(),[])

    def test_refine_rejects_other_work_item_or_incomplete_suggestion(self):
        self.send();self.complete({'title':'合成文章','body':'建议正文'})
        prior=self.manager.get('post',self.post['id'])['turns'][-1]
        other=self.app.save_post({'title':'别篇','body':'独立正文'})
        with self.assertRaises(Problem):self.send(target=other['id'],reference_turn_id=prior['id'])
        self.assertEqual(self.manager.get('post',other['id'])['turns'],[])
        self.send(mode='discuss');self.complete(None)
        discussion=self.manager.get('post',self.post['id'])['turns'][-1]
        with self.assertRaises(Problem):self.send(reference_turn_id=discussion['id'])

    def test_refine_duplicate_request_reuses_exact_reference(self):
        self.send();self.complete({'title':'合成文章','body':'第一版建议'})
        source=self.manager.get('post',self.post['id']);prior=source['turns'][-1]
        values={'request_id':uid(),'revision':source['revision'],'draft':source['content'],'message':'继续改','reference_turn_id':prior['id']}
        first=self.manager.send('post',self.post['id'],values)
        repeated=self.manager.send('post',self.post['id'],values)
        self.assertEqual(first['turns'][-1]['id'],repeated['turns'][-1]['id'])
        with self.assertRaises(Problem):self.manager.send('post',self.post['id'],{**values,'reference_turn_id':None})

    def test_model_cannot_corrupt_or_invent_local_image_addresses(self):
        image='/media/'+'a'*32+'.png'
        post=self.app.save_post({'version':self.post['version'],'body':'![仿真图]('+image+')'},self.post['id'])
        self.send(draft={'title':post['title'],'body':post['body']})
        self.complete({'title':post['title'],'body':'![仿真图](/media/'+'b'*32+'.png)'})
        turn=self.manager.get('post',post['id'])['turns'][-1]
        self.assertEqual(turn['status'],'failed');self.assertIn('未提供的图片地址',turn['error'])
        self.assertEqual(self.app.post(post['id'])['body'],post['body'])

    def test_model_failure_retry_and_late_reply_after_stop(self):
        state=self.send();tid=state['turns'][0]['id']
        def failure(*a,**k):raise DeliveryError('认证错误')
        self.app.generate_draft=failure;self.manager.tick()
        self.assertEqual(self.manager.get('post',self.post['id'])['turns'][0]['status'],'failed')
        self.manager.retry(tid)
        def late(*a,**k):
            self.manager.interrupt();return dumps({'reply':'晚到结果','replacement':{'title':'合成文章','body':'不能采用'}})
        self.app.generate_draft=late;self.manager.tick()
        turn=self.manager.get('post',self.post['id'])['turns'][0]
        self.assertEqual(turn['status'],'interrupted');self.assertEqual(turn['result'],{})

    def test_invalid_response_does_not_change_target(self):
        self.send();self.app.generate_draft=lambda *a,**k:'not-json';self.manager.tick()
        self.assertEqual(self.manager.get('post',self.post['id'])['turns'][0]['status'],'failed')
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])

    def test_outline_revision_is_new_task_and_exact_replay_reuses(self):
        old=self.workflow()
        context={'profile_id':self.profile['id'],'conversation_id':uid()}
        args={'post_id':self.post['id'],'post_version':1,'instruction':'保留事实，先生成提纲','reference_ids':[]}
        reused=self.app.chat.dispatch({'name':'start_outline','arguments':args},uid(),context)
        self.assertTrue(reused['reused'])
        new=self.app.chat.dispatch({'name':'start_outline','arguments':{**args,'instruction':'修改原提纲，文末加入作者博客'}},uid(),context)
        self.assertFalse(new['reused'])
        task=self.app.workflows.get(new['tasks'][0]['id'])
        self.assertNotEqual(task['id'],old['id']);self.assertEqual(task['parent_id'],old['id'])
        self.assertEqual(task['status'],'queued');self.assertEqual(task['confirmed_outline_version'],0)
        prompts=[]
        self.app.generate_draft=lambda post,instruction,**kw:(prompts.append(instruction) or '## 修改提纲\n文末博客')
        self.app.workflows.tick()
        task=self.app.workflows.get(task['id']);self.assertEqual(task['status'],'outline_review')
        self.assertIn('原提纲',prompts[0]);self.assertIn('文末加入作者博客',prompts[0])
        self.assertEqual(self.app.workflows.get(old['id'])['outline'],old['outline'])

    def test_pending_body_save_is_cas_and_original_unchanged(self):
        task=self.workflow();self.app.workflows.confirm(task['id'],{'outline_version':task['outline_version'],'outline':task['outline']})
        self.app.generate_draft=lambda *a,**kw:'模型正文';self.app.workflows.tick();task=self.app.workflows.get(task['id'])
        self.assertTrue(task['result']['footer_applied']);self.assertEqual(task['result']['raw_model_body'],'模型正文')
        edited=self.app.workflows.result_edit(task['id'],{'result_signature':task['result_signature'],'content':{'title':'新标题','body':'保存的待审正文'}})
        self.assertEqual(edited['result']['title'],'新标题')
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])
        with self.assertRaises(Problem):self.app.workflows.result_edit(task['id'],{'result_signature':task['result_signature'],'content':{'title':'旧窗口','body':'旧编辑'}})
        self.assertEqual(self.manager.get('body',task['id'])['content']['title'],'新标题')

    def test_footer_scoped_preserved_deduplicated_and_checked(self):
        body,changed=apply_footer('正文',self.profile)
        self.assertTrue(changed);self.assertIn('https://author.example/blog/',body)
        self.assertEqual(apply_footer(body,self.profile),(body,False))
        self.assertEqual(apply_footer('正文',self.profile,'xiaohongshu'),('正文',False))
        self.assertFalse(footer_satisfied('正文',self.profile))
        self.assertTrue(footer_satisfied(body,self.profile))
        post={**self.post,'body':'正文','stage':'ready'}
        report=self.app.preflight(post,['wechat'])
        check=next(c for c in report['checks'] if c['label']=='公众号文末署名入口')
        self.assertEqual(check['level'],'error')

    def test_footer_legacy_explicit_setting_survives_empty_field_upgrade(self):
        content={k:v for k,v in self.profile.items() if k not in ('id','version','created_at','updated_at','wechat_footer')}
        content['preferences']='保留作者口吻。\n公众号文末署名与入口：作者博客：https://author.example/blog/'
        with self.app.db.connect(immediate=True) as db:db.execute('UPDATE profiles SET content=? WHERE id=?',(dumps(content),self.profile['id']))
        profile=profiles.get(self.app,self.profile['id'])
        self.assertEqual(profile['wechat_footer'],'作者博客：https://author.example/blog/')
        cleared=profiles.save(self.app,{**profile,'wechat_footer':''},profile['id'])
        self.assertEqual(cleared['wechat_footer'],'')

    def test_explicit_side_feedback_uses_learning_receipt_and_current_source(self):
        message='以后公众号文章保持自然口吻，记住这一点。'
        sent=self.send(message=message,mode='discuss');self.complete(None)
        tid=sent['turns'][0]['id']
        receipt=self.manager.get('post',self.post['id'])['turns'][0]['learning']
        self.assertEqual(receipt[0]['status'],'queued')
        candidate={'content':message,'quote':message,'category':'表达','platform':'wechat','keywords':'','intent':'long_term','conflicts':[],'reason':'作者明确要求'}
        with patch('studio.skills.generate_structured',return_value=dumps({'candidates':[candidate]})):
            self.assertTrue(self.app.learning.tick())
        receipt=self.manager.get('post',self.post['id'])['turns'][0]['learning']
        self.assertEqual(receipt[0]['status'],'completed')
        self.assertEqual(receipt[0]['memories'][0]['status'],'accepted')
        self.assertEqual(receipt[0]['memories'][0]['content'],message)
        self.assertEqual(self.app.post(self.post['id'])['body'],self.post['body'])

    def test_this_article_side_feedback_does_not_create_longterm_event(self):
        self.send(message='本篇结尾改成一个问题，不要记为长期要求。')
        self.assertEqual(self.app.db.rows('SELECT * FROM profile_learning_events'),[])

    def test_current_profile_change_blocks_old_suggestion(self):
        self.send();self.complete({'title':'合成文章','body':'建议正文'})
        turn=self.manager.get('post',self.post['id'])['turns'][0]
        profiles.save(self.app,{**self.profile,'style':'新的口吻'},self.profile['id'])
        with self.assertRaises(Problem):self.manager.accept(turn['id'],{'draft':turn['draft']})

    def test_reference_based_image_turn_creates_one_candidate_without_adopting(self):
        from studio import image_provider
        from tests.test_illustrations import png
        image_provider.save(self.app.config,{'base_url':'https://image.example/v1','api_key':'synthetic','model':'synthetic','references':'edits'})
        plan=self.app.illustrations.create({'post_id':self.post['id'],'post_version':1})
        self.app.illustrations.generate(plan['id'],{'version':plan['version'],'page_index':0,'request_id':uid()})
        with patch('studio.image_provider.generate',return_value=png()):self.app.illustrations.tick()
        image=self.app.illustrations.get(plan['id'])['candidates'][0]
        source=self.manager.get('image',image['id']);rid=uid()
        values={'request_id':rid,'message':'保留画面，增加留白','revision':source['revision'],'draft':source['content']}
        self.manager.send('image',image['id'],values);self.manager.send('image',image['id'],values)
        task=self.app.db.one('SELECT snapshot FROM illustration_candidates WHERE id=?',(rid,))
        self.assertEqual(json.loads(task['snapshot'])['parent_image'],image['image_url'])
        self.assertEqual(self.app.db.one('SELECT count(*) n FROM illustration_candidates')['n'],2)
        with patch('studio.image_provider.generate',return_value=png()):self.app.illustrations.tick()
        turn=self.manager.get('image',image['id'])['turns'][0]
        self.assertEqual(turn['candidate']['status'],'review')
        self.assertEqual(self.app.illustrations.get(plan['id'])['adopted'],[])
        self.assertEqual(self.app.post(self.post['id'])['cover'],'')


if __name__=='__main__':unittest.main()
