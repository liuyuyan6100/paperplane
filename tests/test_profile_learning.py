import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

from studio.service import Studio, Problem
from studio import profiles, memories
from studio.db import uid
from studio.profile_learning import enqueue, chunks, explicit_longterm, direct_scope


class ProfileLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name))
        self.addCleanup(self.app.close)
        self.p = profiles.save(self.app, {'name':'学习账号','memory':'正文保持作者第一人称'})
        self.pid = self.p['id']

    def post(self, body='修改前的正文', **kw):
        return self.app.save_post({'title':'文章','body':body,'profile_id':self.pid, **kw})

    def feedback(self, message='以后公众号正文不要使用夸张标题，记住这一点。'):
        c = self.app.chat.create({'profile_id':self.pid})
        sent = self.app.chat.send(c['id'], {'request_id':uid(),'message':message})
        turn = sent['turns'][0]
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE conversation_turns SET status='completed' WHERE id=?", (turn['id'],))
        return turn

    def candidate(self, quote, **kw):
        return {'content':'以后避免夸张标题','quote':quote,'category':'表达','platform':'wechat',
                'keywords':'','intent':'long_term','conflicts':[],'reason':'作者明确反馈',**kw}

    def run_learning(self, candidates, callback=None):
        def invoke(*args, **kw):
            if callback:callback()
            return json.dumps({'candidates':candidates},ensure_ascii=False)
        with patch('studio.skills.generate_structured', side_effect=invoke) as model:
            self.app.learning.tick()
        return model

    def due(self):
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE profile_learning_events SET due_at='2000-01-01T00:00:00+00:00'")

    def test_old_feedback_visible_and_pending_never_enters_prompt(self):
        m=memories.propose(self.app,self.pid,{'content':'UNCONFIRMED','quote':'原句','source_text':'原句'})
        snap=profiles.snapshot(self.app,self.pid)
        self.assertEqual(snap['memory'],'正文保持作者第一人称')
        self.assertEqual(snap['recalled_memories'],[])
        self.assertEqual(snap['memory_exclusions'][0]['id'],m['id'])
        self.assertNotIn('UNCONFIRMED',profiles.prompt(snap))

    def test_manual_saves_coalesce_and_preserve_first_before(self):
        p=self.post();p=self.app.save_post({'version':p['version'],'body':'第一次人工修改'},p['id'])
        p=self.app.save_post({'version':p['version'],'body':'第二次人工修改'},p['id'])
        events=self.app.db.rows('SELECT * FROM profile_learning_events')
        self.assertEqual(len(events),1)
        snap=json.loads(events[0]['snapshot']);self.assertEqual(snap['before'],'修改前的正文')
        self.assertEqual(snap['text'],'第二次人工修改')
        self.assertFalse(self.app.learning.tick())
        self.due();self.run_learning([self.candidate('第二次人工修改',intent='inferred',platform='')])
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'pending')

    def test_generated_save_and_whitespace_not_learned(self):
        p=self.post();p=self.app.save_post({'version':p['version'],'body':'AI生成结果','_edit_origin':'generated'},p['id'])
        self.app.save_post({'version':p['version'],'body':'AI生成结果\n\n'},p['id'])
        self.assertEqual(self.app.learning.get(self.pid)['events'],[])

    def test_explicit_directive_saved_verbatim_with_scope_and_next_task(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'])])
        entry=memories.listing(self.app,self.pid)[0]
        self.assertEqual(entry['status'],'accepted');self.assertEqual(entry['content'],turn['message'])
        self.assertEqual(entry['source']['authority'],'explicit_user_instruction')
        self.assertEqual(len(memories.recall(self.app,self.pid,'','wechat')),1)
        self.assertEqual(memories.recall(self.app,self.pid,'','devto'),[])
        self.assertEqual(self.app.chat.get(turn['conversation_id'])['turns'][0]['learning'][0]['memories'][0]['status'],'accepted') if 'conversation_id' in turn else None

    def test_this_article_feedback_does_not_change_profile(self):
        turn=self.feedback('这篇风格写给入门读者，少讲原理。')
        self.run_learning([self.candidate(turn['message'],intent='this_article',platform='')])
        self.assertEqual(memories.listing(self.app,self.pid),[])
        self.assertEqual(profiles.get(self.app,self.pid)['version'],1)

    def test_platform_cannot_be_invented_for_direct_acceptance(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'],platform='xiaohongshu')])
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'pending')

    def test_known_platform_display_name_is_normalized(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'],platform='公众号')])
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'accepted')
        self.assertEqual(memories.listing(self.app,self.pid)[0]['platform'],'wechat')

    def test_explicit_genre_recall_does_not_require_the_style_words(self):
        message='以后公众号教程的正文请先给操作步骤，再解释原理，记住这一点。'
        self.feedback(message);self.run_learning([self.candidate(message,keywords='公众号教程,操作步骤,原理')])
        recalled=memories.recall(self.app,self.pid,'文件备份教程','wechat')
        self.assertEqual(len(recalled),1);self.assertEqual(recalled[0]['keywords'],'教程')
        self.assertEqual(memories.recall(self.app,self.pid,'行业新闻','wechat'),[])

    def test_article_contains_remember_text_still_pending(self):
        p=self.post('以后公众号正文不要用夸张标题，记住这一点。')
        self.app.learning.organize(self.pid,{'post_ids':[p['id']]})
        self.run_learning([self.candidate(p['body'])])
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'pending')

    def test_fabricated_quote_or_conflict_id_rejects_entire_batch(self):
        turn=self.feedback()
        self.run_learning([self.candidate(turn['message']),self.candidate('不存在的句子')])
        self.assertEqual(memories.listing(self.app,self.pid),[])
        self.assertEqual(self.app.learning.get(self.pid)['events'][0]['status'],'failed')
        event=self.app.learning.get(self.pid)['events'][0]
        self.app.learning.action(self.pid,event['id'],{'action':'retry'})
        self.run_learning([self.candidate(turn['message'],conflicts=[uid()])])
        self.assertEqual(memories.listing(self.app,self.pid),[])

    def test_late_profile_change_prevents_direct_acceptance(self):
        turn=self.feedback()
        self.run_learning([self.candidate(turn['message'])],lambda:profiles.save(self.app,{'version':1,'style':'新风格'},self.pid))
        entry=memories.listing(self.app,self.pid)[0]
        self.assertEqual(entry['status'],'pending');self.assertTrue(entry['source']['source_changed'])

    def test_interrupt_discards_late_result_and_requires_manual_retry(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'])],self.app.learning.interrupt)
        self.assertEqual(memories.listing(self.app,self.pid),[])
        e=self.app.learning.get(self.pid)['events'][0];self.assertEqual(e['status'],'interrupted')
        self.assertFalse(self.app.learning.tick())
        self.app.learning.action(self.pid,e['id'],{'action':'retry'});self.run_learning([self.candidate(turn['message'])])
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'accepted')

    def test_pause_prevents_new_inferences_but_explicit_instruction_allowed(self):
        self.app.learning.options(self.pid,{'version':0,'enabled':False})
        p=self.post();self.app.save_post({'version':1,'body':'人工新正文'},p['id'])
        self.assertEqual(self.app.learning.get(self.pid)['events'],[])
        turn=self.feedback();self.run_learning([self.candidate(turn['message'])])
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'accepted')
        with self.assertRaises(Problem):self.app.learning.options(self.pid,{'version':0,'enabled':True})

    def test_excluded_article_does_not_enter_history_or_queue(self):
        p=self.post();self.app.learning.exclude(self.pid,{'post_id':p['id'],'excluded':True})
        self.app.save_post({'version':1,'body':'新正文'},p['id'])
        self.assertEqual(self.app.learning.organize(self.pid,{'post_ids':[p['id']]})['event_ids'],[])
        self.assertEqual(self.app.learning.get(self.pid)['events'],[])

    def test_history_validates_all_sources_and_is_idempotent(self):
        p=self.post();other=profiles.save(self.app,{'name':'其他画像'})
        foreign=self.app.save_post({'title':'其他','body':'正文','profile_id':other['id']})
        with self.assertRaises(Problem):self.app.learning.organize(self.pid,{'post_ids':[p['id'],foreign['id']],'include_legacy':True})
        self.assertEqual(self.app.learning.get(self.pid)['events'],[])
        one=self.app.learning.organize(self.pid,{'post_ids':[p['id']],'include_legacy':True})
        two=self.app.learning.organize(self.pid,{'post_ids':[p['id']],'include_legacy':True})
        self.assertEqual(one,two)
        self.assertEqual(len(one['event_ids']),2)

    def test_ignored_candidate_not_recreated_from_repeated_source(self):
        p=self.post('教程先写准备条件')
        self.app.learning.organize(self.pid,{'post_ids':[p['id']]})
        candidate=self.candidate(p['body'],content='教程先写准备条件',intent='inferred',platform='')
        self.run_learning([candidate]);m=memories.listing(self.app,self.pid)[0]
        memories.update(self.app,self.pid,m['id'],{'action':'ignore','version':m['version']})
        p=self.app.save_post({'version':1,'body':p['body']+'。'},p['id'])
        self.due();self.run_learning([candidate])
        self.assertEqual(len(memories.listing(self.app,self.pid)),1)
        self.assertEqual(memories.listing(self.app,self.pid)[0]['status'],'deleted')

    def test_undo_uses_version_guard_and_preserves_other_experiences(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'])]);m=memories.listing(self.app,self.pid)[0]
        snap=profiles.snapshot(self.app,self.pid,'','wechat')
        memories.update(self.app,self.pid,m['id'],{'action':'undo','version':m['version']})
        self.assertEqual(memories.recall(self.app,self.pid,'','wechat'),[])
        self.assertEqual(len(snap['recalled_memories']),1)
        with self.assertRaises(Problem):memories.update(self.app,self.pid,m['id'],{'action':'undo','version':m['version']})

    def test_undo_an_edited_rule_restores_prior_accepted_content(self):
        m=memories.propose(self.app,self.pid,{'content':'原要求','quote':'原要求','source_text':'原要求'})
        m=memories.update(self.app,self.pid,m['id'],{'action':'confirm','version':1})
        m=memories.update(self.app,self.pid,m['id'],{'action':'edit','version':2,'content':'新要求','platform':'','keywords':''})
        m=memories.update(self.app,self.pid,m['id'],{'action':'confirm','version':3})
        restored=memories.update(self.app,self.pid,m['id'],{'action':'undo','version':4})
        self.assertEqual(restored['content'],'原要求');self.assertEqual(restored['status'],'accepted')
        self.assertEqual(restored['version'],5)

    def test_conflict_requires_atomic_explicit_replacement(self):
        prior=memories.propose(self.app,self.pid,{'content':'详细讲解原理','quote':'详细讲解原理','source_text':'详细讲解原理'})
        prior=memories.update(self.app,self.pid,prior['id'],{'action':'confirm','version':1})
        turn=self.feedback('以后先给操作步骤，记住这一点。')
        self.run_learning([self.candidate(turn['message'],platform='',conflicts=[prior['id']])])
        m=next(m for m in memories.listing(self.app,self.pid) if m['id']!=prior['id'])
        with self.assertRaises(Problem):memories.update(self.app,self.pid,m['id'],{'action':'confirm','version':1})
        with self.assertRaises(Problem):self.app.learning.resolve(self.pid,m['id'],{'version':1,'replacements':{}})
        self.assertEqual(memories.recall(self.app,self.pid)[0]['id'],prior['id'])
        accepted=self.app.learning.resolve(self.pid,m['id'],{'version':1,'replacements':{prior['id']:prior['version']}})
        self.assertEqual(accepted['status'],'accepted')
        self.assertEqual([x['id'] for x in memories.recall(self.app,self.pid)],[m['id']])

    def test_wrong_profile_never_can_confirm_or_retry(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'],intent='inferred')])
        m=memories.listing(self.app,self.pid)[0];other=profiles.save(self.app,{'name':'外部画像'})
        with self.assertRaises(Problem):self.app.learning.resolve(other['id'],m['id'],{'version':1})
        self.assertEqual(memories.listing(self.app,other['id']),[])

    def test_batch_confirmation_is_all_or_nothing_on_stale_version(self):
        one=memories.propose(self.app,self.pid,{'content':'第一条','quote':'第一条','source_text':'第一条'})
        two=memories.propose(self.app,self.pid,{'content':'第二条','quote':'第二条','source_text':'第二条'})
        with self.assertRaises(Problem):self.app.learning.confirm_many(self.pid,{'items':[{'id':one['id'],'version':1},{'id':two['id'],'version':99}]})
        self.assertEqual(memories.recall(self.app,self.pid),[])
        self.app.learning.confirm_many(self.pid,{'items':[{'id':one['id'],'version':1},{'id':two['id'],'version':1}]})
        self.assertEqual(len(memories.recall(self.app,self.pid)),2)

    def test_learning_state_persists_across_reopen_and_duplicate_does_not_copy(self):
        turn=self.feedback();self.run_learning([self.candidate(turn['message'])])
        self.app.learning.options(self.pid,{'version':0,'enabled':False})
        duplicate=profiles.duplicate(self.app,self.pid,{'name':'复用画像','version':1,'request_id':uid()})
        self.assertEqual(self.app.learning.get(duplicate['id'])['events'],[])
        self.assertEqual(self.app.learning.get(duplicate['id'])['settings']['enabled'],1)
        other=Studio(Path(self.temp.name))
        try:
            self.assertEqual(other.learning.get(self.pid)['memories'][0]['status'],'accepted')
            self.assertEqual(other.learning.get(self.pid)['settings']['enabled'],0)
        finally:other.close()

    def test_long_input_chunks_are_complete_and_resume_one_part(self):
        text='段落内容'*8000
        self.assertEqual(''.join(chunks({'text':text})),text)
        p=self.post(text);self.app.learning.organize(self.pid,{'post_ids':[p['id']]})
        self.run_learning([]);e=self.app.learning.get(self.pid)['events'][0]
        self.assertEqual(e['status'],'queued');self.assertEqual(e['parts_done'],1)
        while self.app.learning.get(self.pid)['events'][0]['status']=='queued':self.run_learning([])
        self.assertEqual(self.app.learning.get(self.pid)['events'][0]['result']['parts'],3)

    def test_direct_intent_guard_rejects_quoted_examples_and_questions(self):
        for value in ['这篇以后再说','例如以后不要夸张标题','以后这样吗？','不用记住这条']:
            self.assertFalse(explicit_longterm(value))
        self.assertTrue(direct_scope('以后公众号少用夸张标题','wechat',''))
        self.assertFalse(direct_scope('以后公众号少用夸张标题','xiaohongshu',''))

    def test_article_reassigned_during_learning_does_not_write_wrong_profile(self):
        p=self.post('先写步骤');self.app.learning.organize(self.pid,{'post_ids':[p['id']]})
        other=profiles.save(self.app,{'name':'新归属'})
        self.run_learning([self.candidate(p['body'],intent='inferred',platform='')],lambda:self.app.save_post({'version':1,'profile_id':other['id']},p['id']))
        self.assertEqual(memories.listing(self.app,self.pid),[])

    def test_failed_learning_leaves_article_intact(self):
        p=self.post();saved=self.app.save_post({'version':1,'body':'人工修改'},p['id']);self.due()
        with patch('studio.skills.generate_structured',side_effect=RuntimeError('synthetic failure')):self.app.learning.tick()
        self.assertEqual(self.app.post(p['id'])['body'],'人工修改')
        self.assertEqual(self.app.post(p['id'])['version'],saved['version'])
        self.assertEqual(self.app.learning.get(self.pid)['events'][0]['status'],'failed')

    def test_explicit_profile_field_tool_has_literal_authorization(self):
        c=self.app.chat.create({'profile_id':self.pid});message='把这个账号的目标读者改为有编程基础的开发者'
        t=self.app.chat.send(c['id'],{'request_id':uid(),'message':message})['turns'][0]
        result=self.app.chat.dispatch({'name':'set_profile_field','arguments':{'field':'audience','value':'有编程基础的开发者','quote':message}},uid(),{'turn_id':t['id']})
        self.assertEqual(result['profile_update']['version'],2)
        with self.assertRaises(Problem):self.app.chat.dispatch({'name':'set_profile_field','arguments':{'field':'goal','value':'变现','quote':message}},uid(),{'turn_id':t['id']})

    def test_learning_http_authentication(self):
        from studio.server import Server
        server=Server(('127.0.0.1',0),self.app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base=f'http://127.0.0.1:{server.server_port}/api/profiles/{self.pid}/learning'
        with op.open(base) as r:self.assertEqual(json.load(r)['pending_count'],0)
        body=json.dumps({'version':0,'enabled':False}).encode()
        req=urllib.request.Request(base+'/settings',data=body,headers={'Content-Type':'application/json','X-Studio-Token':'bad'})
        with self.assertRaises(urllib.error.HTTPError) as error:op.open(req)
        self.assertEqual(error.exception.code,403)
        req.add_header('X-Studio-Token',server.token)
        with op.open(req) as r:self.assertEqual(json.load(r)['enabled'],0)


if __name__=='__main__':unittest.main()
