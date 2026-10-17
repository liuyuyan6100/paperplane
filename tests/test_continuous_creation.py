"""Continuing one article without losing its title, evidence or version boundary."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio.service import Studio, Problem
from studio.db import uid, dumps
from studio import profiles
from studio.workflows import outline_titles, without_duplicate_title


class ContinuousCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name))
        self.addCleanup(self.app.close)
        self.app.config.update({'AI_MODE':'api', 'AI_BASE_URL':'http://127.0.0.1:9999/v1', 'AI_MODEL':'synthetic'})
        self.profile = profiles.save(self.app, {'name':'合成画像'})
        self.post = self.app.save_post({'title':'最初选题', 'body':'原始素材', 'profile_id':self.profile['id']})
        self.chat = self.app.chat.create({'profile_id':self.profile['id']})
        self.model = Mock(return_value='## 备选标题\n1. 第一个标题\n2. 用户选择的标题\n\n## 实践\n- 要点')
        self.app.generate_draft = self.model

    def task(self, **values):
        task = self.app.workflows.create({'post_id':self.post['id'], 'version':self.app.post(self.post['id'])['version'], 'instruction':'为朋友写入门文章', **values})
        self.app.workflows.tick()
        return self.app.workflows.get(task['id'])

    def confirm(self, task, title='用户选择的标题'):
        return self.app.workflows.confirm(task['id'], {'outline':task['outline'], 'outline_version':task['outline_version'], 'selected_title':title})

    def test_title_adoption_request_snapshot_and_rewrite(self):
        original = {'current_message':'本篇给普通朋友，保留已验证范围，不写内部过程。', 'previous_user_messages':[]}
        task = self.task(creation_request=original)
        self.assertEqual(task['title_options'], ['第一个标题','用户选择的标题'])
        self.confirm(task)
        self.confirm(task)  # Exact replay, including selected title, remains idempotent.
        with self.assertRaises(Problem):
            self.confirm(task, '未经重新确认的新标题')
        self.model.return_value = '# 用户选择的标题\n\n从这里开始正文。\n\n## 用户选择的标题\n章节同名要保留。'
        self.app.workflows.tick()
        result = self.app.workflows.get(task['id'])
        self.assertEqual(result['result']['title'], '用户选择的标题')
        self.assertTrue(result['result']['body'].startswith('从这里开始正文。'))
        self.assertIn('## 用户选择的标题', result['result']['body'])
        self.assertIn(original['current_message'], self.model.call_args.args[1])
        self.assertEqual(self.model.call_args.args[0]['_model_route']['owner_id'], task['id'])
        self.assertEqual([m['task'] for m in result['model_stages']], ['outline','body'])
        self.assertEqual(self.app.post(self.post['id'])['title'], '最初选题')
        saved = self.app.workflows.apply(task['id'], {'version':1})['post']
        self.assertEqual(saved['title'], '用户选择的标题')
        self.assertEqual(saved['version'], 2)
        rewrite = self.app.workflows.create({'post_id':self.post['id'], 'version':2, 'instruction':'为朋友写入门文章', 'parent_id':task['id'], 'reuse_confirmed':True})
        self.assertEqual(rewrite['status'], 'queued')
        self.assertEqual(rewrite['creation_request'], original)
        self.assertEqual(rewrite['selected_title'], saved['title'])
        self.assertEqual(self.app.jobs(), [])

    def test_newer_title_and_body_cannot_be_overwritten(self):
        task = self.task()
        self.confirm(task)
        self.model.return_value = '# 用户选择的标题\n\n模型正文'
        self.app.workflows.tick()
        self.app.save_post({'version':1, 'title':'用户后来改的标题', 'body':'用户后来改的正文'}, self.post['id'])
        with self.assertRaises(Problem):
            self.app.workflows.apply(task['id'], {'version':1})
        current = self.app.post(self.post['id'])
        self.assertEqual((current['title'],current['body']), ('用户后来改的标题','用户后来改的正文'))
        self.app.chat.link(self.chat['id'], 'workflow', task['id'])
        self.assertTrue(self.app.chat.get(self.chat['id'])['tasks'][0]['source_changed'])

    def test_chat_retains_exact_request_when_planner_paraphrases(self):
        message = '给普通朋友介绍本地模型；只写已有实践，不把菜单入口当成实测。'
        self.app.chat.send(self.chat['id'], {'message':message,'request_id':uid()})
        self.model.return_value = dumps({'reply':'安排提纲','tool':{'name':'start_outline','arguments':{
            'post_id':self.post['id'],'post_version':1,'instruction':'简短改述','reference_ids':[]}}})
        self.app.chat.tick()
        chat = self.app.chat.get(self.chat['id'])
        task = self.app.workflows.get(chat['tasks'][0]['id'])
        self.assertEqual(task['creation_request']['current_message'], message)
        self.assertEqual(chat['turns'][0]['retrieval']['status'], 'not_searched')

    def test_search_scope_and_screenshot_reference_are_not_verified_evidence(self):
        item = {'id':uid(),'version':1,'name':'原始实践.md','source':'已登记笔记','relative_path':'实践.md'}
        context = {'library_items':{}}
        with patch('studio.shared_library.listing', return_value={'items':[],'total':0}):
            result = self.app.chat.dispatch({'name':'search_library','arguments':{'query':'未命中'}}, uid(), context)
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['index_status'], 'unknown_outside_catalogue')
        self.assertIn('不能据此断言', result['hint'])
        context['library_items'][item['id']] = 1
        with patch('studio.shared_library.material', return_value=(item,'菜单截图：![控制台](console.png)\n实际验证仅覆盖返回文本。'.encode())):
            read = self.app.chat.dispatch({'name':'read_library','arguments':{'item_id':item['id'],'version':1}}, uid(), context)
        self.assertEqual(read['image_references'], ['console.png'])
        self.assertIn('尚未查看', read['evidence_boundary'])
        self.assertEqual(self.app.db.one('SELECT COUNT(*) n FROM posts')['n'], 1)

    def test_turn_distinguishes_empty_catalogue_lookup_from_unavailable_service(self):
        for expected, failure in [('no_match',None), ('unavailable',Problem('合成资料服务不可用',503))]:
            self.app.chat.send(self.chat['id'], {'message':'查找已有实践','request_id':uid()})
            self.model.side_effect = [dumps({'reply':'查找','tool':{'name':'search_library','arguments':{'query':'实践'}}}),
                                      dumps({'reply':'已保留检索结果','tool':None})]
            with patch('studio.shared_library.listing',return_value={'items':[],'total':0},side_effect=failure):
                self.app.chat.tick()
            retrieval = self.app.chat.get(self.chat['id'])['turns'][-1]['retrieval']
            self.assertEqual(retrieval['status'], expected)
            self.assertIn('尚未核实', retrieval['scope'])

    def test_stale_review_can_restart_on_latest_article_from_original_chat(self):
        from tests.test_reviews import advice
        self.app.save_post({'version':1,'body':'原句需要简洁。','stage':'draft'}, self.post['id'])
        review = self.app.reviews.create({'post_id':self.post['id'],'post_version':2,'request_id':uid()})
        self.app.chat.link(self.chat['id'], 'review', review['id'])
        self.model.return_value = dumps(advice())
        self.app.reviews.tick()
        current = self.app.save_post({'version':2,'body':'已修改的新正文。'}, self.post['id'])
        task = self.app.chat.get(self.chat['id'])['tasks'][0]
        self.assertTrue(task['source_changed'])
        self.assertIn('重新审稿', task['next_step'])
        self.app.chat.action(self.chat['id'], {'action':'start_review','kind':'review','id':review['id'],'post_version':current['version'],'request_id':uid()})
        reviews = self.app.reviews.listing(self.post['id'])['reviews']
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0]['source_version'], current['version'])
        self.assertEqual(self.app.post(self.post['id'])['body'], '已修改的新正文。')


class TitleParsingTests(unittest.TestCase):
    def test_only_explicit_title_options_and_leading_duplicate(self):
        self.assertEqual(outline_titles('## 第一节\n1. 操作步骤\n## 第二节'), [])
        self.assertEqual(without_duplicate_title('# 章节标题\n正文', '文章标题'), '# 章节标题\n正文')
        self.assertEqual(without_duplicate_title('文章标题\n\n正文', '文章标题'), '正文')


if __name__ == '__main__':
    unittest.main()
