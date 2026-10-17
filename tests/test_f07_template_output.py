"""
F07: 模板解析和输出契约测试

验证提纲模板占位符渲染和 JSON 输出解析
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import json
from studio.skill_executors import OutlineGenerator
from studio.skills import SkillError


class TestF07TemplateAndOutput(unittest.TestCase):
    """测试模板解析和输出契约"""

    def setUp(self):
        """设置测试环境"""
        # 创建模拟的 app 对象
        self.mock_app = Mock()
        self.mock_app.db = Mock()
        self.mock_app.skills_dir = Path(__file__).parent.parent / 'skills'

        # 创建完整的技能定义
        self.mock_skill_def = {
            'name': 'generate-outline',
            'version': '1.0.0',
            'description': 'Test skill',
            'metadata': {
                'version': '1.0.0',
                'author': 'test'
            },
            'path': Path(__file__).parent.parent / 'skills' / 'content' / 'generate-outline'
        }

        self.executor = OutlineGenerator(self.mock_app, self.mock_skill_def)

    def test_template_placeholder_rendering(self):
        """测试模板占位符被正确渲染"""
        # 准备测试上下文
        context = {
            'post': {
                'id': 1,
                'title': '测试文章标题'
            },
            'profile': {
                'direction': '技术博客',
                'audience': '开发者',
                'goal': '分享知识',
                'style': '深入浅出',
                'preferences': '代码优先'
            },
            'materials': [
                {'title': '素材1', 'body': '素材内容1'},
                {'title': '素材2', 'body': '素材内容2'}
            ],
            'instruction': '重点关注实战案例'
        }

        # 加载并渲染模板
        prompt = self.executor.load_prompt(context)

        # 验证占位符已被替换
        self.assertNotIn('{profile.direction}', prompt)
        self.assertNotIn('{post.title}', prompt)
        self.assertNotIn('{materials_content}', prompt)
        self.assertNotIn('{requirements}', prompt)

        # 验证实际值已插入
        self.assertIn('技术博客', prompt)
        self.assertIn('测试文章标题', prompt)
        self.assertIn('素材1', prompt)
        self.assertIn('重点关注实战案例', prompt)

    def test_json_output_parsing(self):
        """测试 JSON 输出被正确解析"""
        # 模拟模型返回的 JSON 响应
        json_response = {
            "outline": "# 文章提纲\n\n## 一、引言\n- 开篇点题\n\n## 二、主体\n- 核心内容",
            "sections": [
                {
                    "title": "引言",
                    "points": ["开篇点题", "引出主题"],
                    "estimated_words": 200
                },
                {
                    "title": "主体",
                    "points": ["核心内容", "详细展开"],
                    "estimated_words": 800
                }
            ],
            "title_suggestions": ["标题1", "标题2", "标题3"],
            "key_points": ["要点1", "要点2"],
            "material_gaps": [],
            "political_risk_warning": None
        }

        raw_result = json.dumps(json_response, ensure_ascii=False)

        # 解析结果
        parsed = self.executor.parse_result(raw_result)

        # 验证解析结果
        self.assertIn('outline', parsed)
        self.assertIn('sections', parsed)
        self.assertIn('title_suggestions', parsed)
        self.assertEqual(len(parsed['sections']), 2)
        self.assertEqual(len(parsed['title_suggestions']), 3)
        self.assertNotIn('political_risk_warning', parsed)

    def test_json_in_markdown_code_block(self):
        """测试 markdown 代码块中的 JSON 被正确提取"""
        json_data = {
            "outline": "# 测试提纲",
            "sections": [],
            "title_suggestions": ["标题A"]
        }

        # 模拟 markdown 代码块格式
        raw_result = f"这是一些说明文字\n\n```json\n{json.dumps(json_data, ensure_ascii=False)}\n```\n\n这是结尾"

        # 解析结果
        parsed = self.executor.parse_result(raw_result)

        # 验证提取成功
        self.assertEqual(parsed['outline'], "# 测试提纲")
        self.assertEqual(parsed['title_suggestions'], ["标题A"])

    def test_example_section_not_included_in_outline(self):
        """测试示例部分不会被包含在 outline 中"""
        # 模拟包含示例的响应（非 JSON 格式）
        raw_result = """# 文章提纲

## 一、引言
- 开篇内容

## 二、主体
- 核心内容

## 示例

### 示例输入
这是示例内容，不应该出现在最终 outline 中

### 示例输出
更多示例内容
"""

        # 解析结果
        parsed = self.executor.parse_result(raw_result)

        # 验证示例部分被移除
        self.assertNotIn('示例输入', parsed['outline'])
        self.assertNotIn('示例输出', parsed['outline'])
        self.assertIn('引言', parsed['outline'])
        self.assertIn('主体', parsed['outline'])

    def test_sections_array_to_outline_conversion(self):
        """测试当 outline 缺失时从 sections 数组构建"""
        json_response = {
            "sections": [
                {
                    "title": "第一章",
                    "points": ["要点1", "要点2"],
                    "estimated_words": 300
                },
                {
                    "title": "第二章",
                    "points": ["要点3", "要点4"],
                    "estimated_words": 500
                }
            ],
            "title_suggestions": ["标题"]
        }

        raw_result = json.dumps(json_response, ensure_ascii=False)

        # 解析结果
        parsed = self.executor.parse_result(raw_result)

        # 验证 outline 已从 sections 构建
        self.assertIn('outline', parsed)
        self.assertIn('第一章', parsed['outline'])
        self.assertIn('第二章', parsed['outline'])
        self.assertIn('要点1', parsed['outline'])
        self.assertIn('300字', parsed['outline'])

    def test_invalid_json_falls_back_to_text_extraction(self):
        """测试无效 JSON 时回退到文本提取"""
        # 提供无效的 JSON
        raw_result = "这不是有效的 JSON\n\n# 提纲标题\n\n## 章节1\n- 内容"

        # 解析应该不抛出异常，而是回退到文本模式
        parsed = self.executor.parse_result(raw_result)

        # 验证回退成功
        self.assertIn('outline', parsed)
        self.assertEqual(parsed['outline'], raw_result.strip())
        self.assertNotIn('political_risk_warning', parsed)

    def test_political_risk_warning_detected(self):
        """政治风险警告必须阻断：抛出 POLITICAL_RISK，不返回提纲"""
        raw_result = json.dumps({
            "outline": "# 提纲", "sections": [], "title_suggestions": [],
            "political_risk_warning": "标题涉及敏感话题，建议修改"
        }, ensure_ascii=False)
        with self.assertRaises(SkillError) as ctx:
            self.executor.parse_result(raw_result)
        self.assertEqual(ctx.exception.code, 'POLITICAL_RISK')
        self.assertIn('标题涉及敏感话题', ctx.exception.message)


if __name__ == '__main__':
    unittest.main()
