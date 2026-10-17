import unittest
from studio.platform_fields import delivery_summary


class PlatformFieldsTests(unittest.TestCase):
    def test_platform_limit_includes_emoji_without_splitting_surrogates(self):
        for text in ['摘'*211,'😀'*80,'摘'*99+'😀后续文字']:
            result=delivery_summary('juejin',text)
            self.assertLessEqual(len(result.encode('utf-16-le'))//2,100)
            self.assertNotIn('\ufffd',result)

    def test_complete_sentence_is_preferred_and_other_platforms_unchanged(self):
        sentence='这是保留的一句摘要，用来说明知识库、对话历史、长期记忆和当前上下文各自应当承担的职责。'
        text=sentence+'后续'*100
        self.assertEqual(delivery_summary('juejin',text),sentence)
        self.assertEqual(delivery_summary('csdn',text),text)
        self.assertEqual(delivery_summary('juejin','摘'*100),'摘'*100)
