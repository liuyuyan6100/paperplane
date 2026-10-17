import json
from unittest.mock import patch
from tests.test_repurpose import RepurposeTests, content
from studio.db import dumps
from studio.repurpose import clean_content
from studio.service import Problem


class TitleRulesTests(RepurposeTests):
    def test_generation_repairs_only_title_once(self):
        task=self.create(['xiaohongshu'])[0]
        generated=content('xiaohongshu');generated['title']='07 | 从知识库到长期记忆：智能体记忆体系怎么分工'
        with patch.object(self.app,'generate_draft',side_effect=[dumps(generated),dumps({'title':'智能体记忆如何分工'})]) as model:
            self.app.repurpose.tick()
        result=self.app.repurpose.get(task['id'])
        self.assertEqual(result['status'],'review')
        self.assertEqual(result['content']['title'],'智能体记忆如何分工')
        self.assertEqual(result['content']['caption'],generated['caption'])
        self.assertEqual(model.call_count,2)
        self.assertIn('1至20字',model.call_args_list[0].args[1])
        self.assertEqual(self.app.post(self.post['id'])['title'],'母稿')

    def test_invalid_repair_does_not_become_reviewable(self):
        task=self.create(['xiaohongshu'])[0]
        generated=content('xiaohongshu');generated['title']='长'*21
        with patch.object(self.app,'generate_draft',return_value=dumps(generated)) as model:
            self.app.repurpose.tick()
        self.assertEqual(self.app.repurpose.get(task['id'])['status'],'failed')
        self.assertEqual(model.call_count,2)

    def test_title_boundary_and_other_platforms(self):
        generated=content('xiaohongshu');generated['title']='字'*19+'！'
        self.assertEqual(clean_content(generated,'xiaohongshu')['title'],generated['title'])
        generated['title']+='字'
        with self.assertRaises(Problem):clean_content(generated,'xiaohongshu')
        other=content();other['title']='字'*50
        self.assertEqual(clean_content(other,'csdn')['title'],other['title'])
