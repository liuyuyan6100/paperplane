import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studio import illustration_positions as positions, image_provider
from studio.db import dumps, uid
from studio.illustrations import IllustrationManager
from studio.service import Problem, Studio


def image_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (180, 120), '#ecdabb').save(buffer, format='PNG')
    return buffer.getvalue()


BODY = ('开篇解释为什么尝试本地模型。\n\n## 一、模型文件\n\n'
        + '先理解模型文件和运行程序的关系，不把磁盘文件大小当作运行显存。' * 8
        + '\n\n## 二、本地接口\n\n'
        + '本次使用本地接口连接客户端，这只是运行方式之一，并非所有工具都要求 HTTP。' * 8
        + '\n\n## 写在最后\n\n根据实际需要选择工具，先跑通一个小模型。\n\n——Aiclaw\n\nhttps://example.com/blog\n')


class AnchorTests(unittest.TestCase):
    def test_offsets_are_recomputed_after_unrelated_changes(self):
        target = next(t for t in positions.choices(BODY) if t['label'] == '章节末：一、模型文件')
        changed = '补充开场说明。\n\n' + BODY
        result, current = positions.insert(changed, '![示意](/media/' + 'a' * 32 + '.png)', target['id'])
        self.assertGreater(current['end'], target['end'])
        self.assertLess(result.index('![示意]'), result.index('## 二、本地接口'))
        self.assertLess(result.index('![示意]'), result.index('——Aiclaw'))

    def test_changed_or_duplicated_anchor_never_falls_back_to_end(self):
        body = '## 章节\n\n唯一段落。\n'
        target = positions.default_targets(body)[0]
        for changed in ('## 章节\n\n段落已经重写。\n', '## 章节\n\n唯一段落。\n\n唯一段落。\n'):
            with self.subTest(body=changed), self.assertRaisesRegex(Problem, '没有追加到文末'):
                positions.insert(changed, 'image', target['id'])

    def test_choices_exclude_code_images_captions_and_signature(self):
        body = '## 操作\n\n说明段落。\n\n```text\n## 假标题\n\n内部代码\n```\n\n![原图](/media/' + 'a' * 32 + '.png)\n\n图注：用户说明。\n\n——Aiclaw\n\n署名后的文字。'
        options = positions.choices(body)
        self.assertTrue(options)
        self.assertTrue(all(o['excerpt'] == '说明段落。' for o in options))
        self.assertTrue(all(o['end'] < body.index('```') for o in options))

    def test_main_chapters_not_article_h1_or_conclusion_get_defaults(self):
        targets = positions.default_targets('# 文章标题\n\n' + BODY)
        self.assertEqual([t['section'] for t in targets], ['一、模型文件', '二、本地接口'])
        many = '\n\n'.join('## 第' + str(i) + '节\n\n' + '足够长的概念说明。' * 25 for i in range(9))
        self.assertEqual(len(positions.default_targets(many)), 4)
        self.assertEqual(len(positions.default_targets('只有一段简短正文。')), 1)


class PositionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paperplane-positions-')
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name) / 'isolated')
        self.addCleanup(self.app.close)
        self.post = self.app.save_post({'title': '本地模型说明', 'body': BODY, 'author': 'Aiclaw', 'stage': 'draft'})
        self.plan = self.app.illustrations.create({'post_id': self.post['id'], 'post_version': self.post['version']})
        self.original = image_bytes()
        self.asset = self.app.upload({'data': base64.b64encode(self.original).decode(), 'title': '真实截图', 'origin': 'upload'})

    def current(self):
        return self.app.illustrations.get(self.plan['id'])

    def upload(self, index=1):
        plan = self.current()
        result = self.app.illustrations.import_original(plan['id'], {'version': plan['version'], 'page_index': index,
                                                    'image_url': self.asset['url'], 'request_id': uid()})
        return result['candidates'][-1]

    def request(self, candidate, **values):
        return {'candidate_id': candidate['id'], 'post_version': self.current()['post_version'], **values}

    def test_new_plan_has_chapter_targets_and_no_invented_captions(self):
        pages = self.plan['pages']
        self.assertEqual([p['slot'] for p in pages], ['cover', 'body', 'body'])
        self.assertTrue(all(p['placement_valid'] for p in pages if p['slot'] == 'body'))
        self.assertTrue(all(p['caption'] == '' for p in pages))
        self.assertTrue(all('写在最后' not in p['title'] for p in pages))

    def test_imported_original_has_no_model_call_or_byte_changes(self):
        before = self.app.post(self.post['id'])
        with patch.object(self.app, 'generate_draft') as text, patch('studio.image_provider.generate') as image:
            candidate = self.upload()
            self.assertFalse(self.app.illustrations.tick())
        text.assert_not_called(); image.assert_not_called()
        self.assertEqual(candidate['origin'], 'original')
        self.assertEqual(candidate['model_provenance']['source'], 'none')
        self.assertEqual(candidate['context']['page']['caption'], '')
        self.assertEqual(self.app.post(self.post['id']), before)
        self.assertEqual((self.app.db.data_dir / 'media' / Path(self.asset['url']).name).read_bytes(), self.original)
        self.assertEqual(self.app.db.rows('SELECT * FROM model_calls'), [])

    def test_preview_is_readonly_and_adoption_uses_chosen_chapter_before_signature(self):
        candidate = self.upload()
        before = self.app.post(self.post['id'])
        values = self.request(candidate, caption='我运行本地工具时保留的原图。')
        preview = self.app.illustrations.preview(self.plan['id'], values)
        self.assertEqual(self.app.post(self.post['id']), before)
        self.assertIn('章节末：一、模型文件', preview['placement_label'])
        self.assertIn(self.asset['url'], preview['preview_html'])
        self.assertIn('图注：我运行本地工具时保留的原图。', preview['preview_html'])
        result = self.app.illustrations.adopt(self.plan['id'], values)
        after = self.app.post(self.post['id'])
        self.assertFalse(result['already_adopted'])
        self.assertEqual(after['version'], before['version'] + 1)
        self.assertLess(after['body'].index(self.asset['url']), after['body'].index('## 二、本地接口'))
        self.assertLess(after['body'].index(self.asset['url']), after['body'].index('——Aiclaw'))
        self.assertEqual(self.app.jobs(), [])
        self.assertTrue(self.app.illustrations.adopt(self.plan['id'], values)['already_adopted'])
        self.assertEqual(self.app.post(self.post['id'])['version'], after['version'])

    def test_stale_anchor_blocks_even_with_ack_until_user_reselects_current_paragraph(self):
        candidate = self.upload()
        post = self.app.save_post({'version': self.post['version'], 'body': '## 新章节\n\n用户完全改写的正文。\n\n——Aiclaw'}, self.post['id'])
        values = self.request(candidate, ack_changes=True)
        with self.assertRaisesRegex(Problem, '没有追加到文末'):
            self.app.illustrations.adopt(self.plan['id'], values)
        self.assertEqual(self.app.post(post['id'])['body'], post['body'])
        values['placement'] = self.current()['placement_options'][0]['id']
        self.app.illustrations.preview(self.plan['id'], values)
        self.app.illustrations.adopt(self.plan['id'], values)
        body = self.app.post(post['id'])['body']
        self.assertIn('用户完全改写的正文。', body)
        self.assertLess(body.index(self.asset['url']), body.index('——Aiclaw'))

    def test_saved_plan_can_keep_stale_position_but_image_generation_blocks(self):
        self.app.save_post({'version': self.post['version'], 'body': '完全不同的段落。'}, self.post['id'])
        edited = self.app.illustrations.edit(self.plan['id'], {'version': self.plan['version'], 'pages': self.plan['pages']})
        self.assertFalse(edited['pages'][1]['placement_valid'])
        with self.assertRaisesRegex(Problem, '插图位置'):
            self.app.illustrations.generate(self.plan['id'], {'version': edited['version'], 'page_index': 1, 'request_id': uid()})
        self.assertEqual(self.current()['candidates'], [])

    def test_user_can_move_same_original_and_edit_caption_with_preview(self):
        candidate = self.upload()
        self.app.illustrations.adopt(self.plan['id'], self.request(candidate, caption='原说明'))
        first = self.app.post(self.post['id'])
        destination = next(p['id'] for p in self.current()['placement_options'] if p['label'] == '章节末：二、本地接口')
        values = self.request(candidate, placement=destination, caption='修改后的事实说明')
        self.app.illustrations.preview(self.plan['id'], values)
        self.app.illustrations.adopt(self.plan['id'], values)
        post = self.app.post(self.post['id'])
        self.assertEqual(post['version'], first['version'] + 1)
        self.assertEqual(post['body'].count(self.asset['url']), 1)
        self.assertNotIn('原说明', post['body'])
        self.assertGreater(post['body'].index(self.asset['url']), post['body'].index('## 二、本地接口'))
        self.assertLess(post['body'].index(self.asset['url']), post['body'].index('## 写在最后'))

    def test_manually_edited_inserted_caption_is_not_overwritten(self):
        candidate = self.upload()
        self.app.illustrations.adopt(self.plan['id'], self.request(candidate, caption='原说明'))
        post = self.app.post(self.post['id'])
        manual = post['body'].replace('图注：原说明', '图注：手动补充信息')
        self.app.save_post({'version': post['version'], 'body': manual}, post['id'])
        with self.assertRaisesRegex(Problem, '手动修改'):
            self.app.illustrations.adopt(self.plan['id'], self.request(candidate, caption='覆盖说明', ack_changes=True))
        self.assertEqual(self.app.post(post['id'])['body'], manual)

    def test_original_image_cannot_be_used_as_ai_revision_parent(self):
        candidate = self.upload()
        image_provider.save(self.app.config, {'base_url': 'https://image.example/v1', 'model': 'test-model', 'references': 'edits'})
        with self.assertRaisesRegex(Problem, '不通过 AI 重绘'):
            self.app.illustrations.generate(self.plan['id'], {'version': self.plan['version'], 'page_index': 1,
                'parent_id': candidate['id'], 'request_id': uid()})

    def test_changed_uploaded_bytes_and_nonlocal_url_are_rejected(self):
        with self.assertRaises(Problem):
            self.app.illustrations.import_original(self.plan['id'], {'version': 1, 'page_index': 1,
                'image_url': 'https://example.com/screenshot.png', 'request_id': uid()})
        candidate = self.upload()
        (self.app.db.data_dir / 'media' / Path(self.asset['url']).name).write_bytes(b'changed')
        with self.assertRaisesRegex(Problem, '上传原图已变化'):
            self.app.illustrations.preview(self.plan['id'], self.request(candidate))

    def test_old_plan_keeps_explicit_legacy_position_and_can_reselect(self):
        pages = self.plan['pages']
        for p in pages:
            p.pop('placement', None)
        with self.app.db.connect() as db:
            db.execute('UPDATE illustration_plans SET content=? WHERE id=?', (dumps(pages), self.plan['id']))
        legacy = self.current()
        self.assertIn('旧计划', legacy['pages'][1]['placement_label'])
        candidate = self.upload()
        preview = self.app.illustrations.preview(self.plan['id'], self.request(candidate))
        self.assertIn('可能在署名之后', preview['placement_label'])
        self.app.illustrations.adopt(self.plan['id'], self.request(candidate))
        post = self.app.post(self.post['id'])
        self.assertGreater(post['body'].index(self.asset['url']), post['body'].index('——Aiclaw'))
        target = next(t['id'] for t in self.current()['placement_options'] if t['label'] == '章节末：一、模型文件')
        self.app.illustrations.adopt(self.plan['id'], self.request(candidate, placement=target))
        self.assertLess(self.app.post(post['id'])['body'].index(self.asset['url']), self.app.post(post['id'])['body'].index('——Aiclaw'))

    def test_image_generation_preserves_saved_position_and_records_model(self):
        image_provider.save(self.app.config, {'base_url': 'https://image.example/v1', 'model': 'test-model', 'references': 'none'})
        original_pages = self.current()['pages']
        self.app.illustrations.generate(self.plan['id'], {'version': 1, 'page_index': 1, 'request_id': uid()})
        with patch('studio.image_provider.generate', return_value=image_bytes()):
            self.app.illustrations.tick()
        current = self.current()
        self.assertEqual(current['pages'], original_pages)
        self.assertEqual(current['candidates'][0]['model_provenance']['requested_model'], 'test-model')
        self.assertEqual(current['candidates'][0]['context']['page']['placement'], original_pages[1]['placement'])

    def test_import_is_idempotent_and_old_adoption_schema_migrates(self):
        for name in ('placement', 'caption', 'inserted_block'):
            with self.app.db.connect() as db:
                db.execute('ALTER TABLE illustration_adoptions DROP COLUMN ' + name)
        IllustrationManager(self.app)
        values = {'version': 1, 'page_index': 1, 'image_url': self.asset['url'], 'request_id': uid()}
        self.app.illustrations.import_original(self.plan['id'], values)
        self.app.illustrations.import_original(self.plan['id'], values)
        self.assertEqual(len(self.current()['candidates']), 1)
        with self.assertRaisesRegex(Problem, '请求编号'):
            self.app.illustrations.import_original(self.plan['id'], {**values, 'page_index': 0})


if __name__ == '__main__':
    unittest.main()
