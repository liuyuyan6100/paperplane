"""Isolated tests of model output recovery; never open the installed user's data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.adapters import DeliveryError
from studio.db import dumps, uid
from studio.illustrations import PlanFormatError, clean_plan, model_plan
from studio.service import Problem, Studio


def page(**changes):
    return {'title': '概念图', 'text': '', 'prompt': '模型文件与本地推理的关系',
            'slot': 'body', 'aspect': 'landscape', **changes}


class ModelPlanOutputTests(unittest.TestCase):
    def test_optional_text_normalization_is_lossless_and_model_only(self):
        values = [page(text=['第一行', '第二行']), page(text=None), page(text=[])]
        result = model_plan(dumps(values))
        self.assertEqual([p['text'] for p in result], ['第一行\n第二行', '', ''])
        for value in values:
            with self.subTest(text=value['text']), self.assertRaises(Problem):
                clean_plan([value])

    def test_long_text_is_rejected_without_truncation(self):
        for text in ('字' * 501, ['字' * 250, '字' * 250]):
            with self.subTest(text=type(text)), self.assertRaisesRegex(PlanFormatError, '500'):
                model_plan(dumps([page(text=text)]))
        self.assertEqual(model_plan(dumps([page(text='字' * 500)]))[0]['text'], '字' * 500)

    def test_objects_numbers_and_mixed_arrays_are_not_stringified(self):
        for text in ({'heading': '标题'}, 1, True, ['文字', None], ['文字', {'x': 'y'}]):
            with self.subTest(text=text), self.assertRaises(PlanFormatError):
                model_plan(dumps([page(text=text)]))
        for values in (page(title=None), page(prompt=['画面']), page(aspect=[])):
            with self.subTest(page=values), self.assertRaises(PlanFormatError):
                model_plan(dumps([values]))

    def test_invalid_json_root_and_page_count_are_format_errors(self):
        for raw in ('not json', '{}', '[]', dumps([page()] * 13), None):
            with self.subTest(raw=raw), self.assertRaises(PlanFormatError):
                model_plan(raw)
        self.assertEqual(model_plan('```json\n' + dumps([page()]) + '\n```'), [page()])


class PlanRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paperplane-plan-output-')
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name) / 'isolated-data')
        self.addCleanup(self.app.close)
        self.post = self.app.save_post({'title': '合成验证文章', 'body': '模型可在本机运行。', 'stage': 'draft'})
        self.post = self.app.post(self.post['id'])
        self.plan = self.app.illustrations.create({'post_id': self.post['id'], 'post_version': self.post['version']})

    def improve(self):
        return self.app.illustrations.improve(self.plan['id'], {'version': self.plan['version']})

    def current(self):
        return self.app.illustrations.get(self.plan['id'])

    def test_valid_model_array_text_succeeds_without_second_request(self):
        self.improve()
        with patch.object(self.app, 'generate_draft', return_value=dumps([page(text=['第一行', '第二行'])])) as generate:
            self.assertTrue(self.app.illustrations.tick())
        self.assertEqual(generate.call_count, 1)
        self.assertIn('title、text、prompt 必须是字符串', generate.call_args.args[1])
        self.assertIn('空字符串 ""', generate.call_args.args[1])
        current = self.current()
        self.assertEqual(current['status'], 'review')
        self.assertEqual(current['version'], 2)
        self.assertEqual(current['pages'][0]['text'], '第一行\n第二行')
        self.assertEqual(self.app.post(self.post['id']), self.post)

    def test_bad_json_gets_one_correction_and_saves_only_valid_result(self):
        self.improve()
        with patch.object(self.app, 'generate_draft', side_effect=['not JSON', dumps([page(text='修正后')])]) as generate:
            self.assertTrue(self.app.illustrations.tick())
        self.assertEqual(generate.call_count, 2)
        self.assertIn('上次输出未通过格式校验', generate.call_args.args[1])
        self.assertEqual(generate.call_args_list[0].args[0], generate.call_args_list[1].args[0])
        self.assertTrue(all(call.kwargs['structured'] for call in generate.call_args_list))
        self.assertEqual({key: self.current()['pages'][0][key] for key in page()}, page(text='修正后'))
        self.assertEqual(self.current()['pages'][0]['placement'], '')
        self.assertFalse(self.current()['pages'][0]['placement_valid'])
        self.assertEqual(self.current()['version'], 2)
        self.assertEqual(self.app.post(self.post['id']), self.post)
        self.assertEqual(self.current()['candidates'], [])

    def test_failed_correction_preserves_original_plan_and_stops(self):
        self.improve()
        invalid = dumps([page(text='长' * 501)])
        with patch.object(self.app, 'generate_draft', return_value=invalid) as generate:
            self.assertTrue(self.app.illustrations.tick())
            self.assertFalse(self.app.illustrations.tick())
        current = self.current()
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(current['status'], 'failed')
        self.assertIn('已尝试纠正 1 次', current['error'])
        self.assertIn('原计划已保留', current['error'])
        self.assertEqual(current['pages'], self.plan['pages'])
        self.assertEqual(current['version'], self.plan['version'])
        self.assertEqual(self.app.post(self.post['id']), self.post)
        restored = self.app.illustrations.edit(current['id'], {'version': current['version'], 'pages': current['pages']})
        self.assertEqual(restored['status'], 'review')
        self.assertEqual(restored['error'], '')

    def test_provider_auth_or_network_error_does_not_trigger_format_correction(self):
        for error in (DeliveryError('凭据无效'), DeliveryError('网络临时失败', retryable=True)):
            with self.subTest(error=str(error)):
                self.improve()
                with patch.object(self.app, 'generate_draft', side_effect=error) as generate:
                    self.assertTrue(self.app.illustrations.tick())
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(self.current()['error'], str(error))
                self.assertEqual(self.current()['pages'], self.plan['pages'])

    def test_provider_error_during_correction_is_not_retried_as_format(self):
        self.improve()
        with patch.object(self.app, 'generate_draft', side_effect=['bad json', DeliveryError('没有模型权限')]) as generate:
            self.app.illustrations.tick()
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(self.current()['error'], '没有模型权限')
        self.assertEqual(self.current()['pages'], self.plan['pages'])

    def test_interruption_before_correction_does_not_send_second_request(self):
        self.improve()
        def interrupt(*args, **kwargs):
            self.app.illustrations.interrupt()
            return 'not JSON'
        with patch.object(self.app, 'generate_draft', side_effect=interrupt) as generate:
            self.app.illustrations.tick()
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.current()['status'], 'interrupted')
        self.assertEqual(self.current()['pages'], self.plan['pages'])

    def test_generate_errors_distinguish_busy_failed_interrupted_version_and_page(self):
        for status, version, index, message in (
            ('queued', 1, 0, '正在完善'), ('running', 1, 0, '正在完善'),
            ('failed', 1, 0, '完善配图计划失败'), ('interrupted', 1, 0, '已中断'),
            ('review', 0, 0, '版本已变化'), ('review', True, 0, '版本已变化'),
            ('review', 1, -1, '页面不存在'), ('review', 1, 100, '页面不存在'),
            ('review', 1, True, '页面不存在')):
            with self.subTest(status=status, version=version, index=index):
                with self.app.db.connect() as db:
                    db.execute('UPDATE illustration_plans SET status=? WHERE id=?', (status, self.plan['id']))
                with self.assertRaisesRegex(Problem, message) as caught:
                    self.app.illustrations.generate(self.plan['id'], {'version': version, 'page_index': index, 'request_id': uid()})
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(self.current()['candidates'], [])


if __name__ == '__main__':
    unittest.main()
