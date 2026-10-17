"""
F06: 提示词路径安全测试

验证提示词加载器防止目录遍历攻击
"""
import unittest
from pathlib import Path
import tempfile
import shutil
from studio.prompt_loader import PromptLoader


class TestF06PathSecurity(unittest.TestCase):
    """测试提示词路径安全"""

    def setUp(self):
        """创建临时测试环境"""
        # 创建临时目录结构
        self.test_dir = Path(tempfile.mkdtemp())
        self.agents_dir = self.test_dir / 'agents'
        self.agents_dir.mkdir()

        # 创建合法的提示词文件
        (self.agents_dir / 'WORKFLOWS.md').write_text('合法的工作流提示词', encoding='utf-8')

        # 创建技能子目录
        skills_dir = self.agents_dir / 'skills' / 'test-skill'
        skills_dir.mkdir(parents=True)
        (skills_dir.parent / 'test-skill.md').write_text('合法的技能提示词', encoding='utf-8')

        # 在 agents_dir 外部创建敏感文件（模拟攻击目标）
        self.sensitive_file = self.test_dir / 'sensitive.md'
        self.sensitive_file.write_text('敏感信息：密码=secret123', encoding='utf-8')

        # 创建 PromptLoader
        self.loader = PromptLoader(self.agents_dir)

    def tearDown(self):
        """清理临时目录"""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_relative_path_traversal_blocked(self):
        """测试相对路径遍历被阻止"""
        # 尝试使用 ../ 访问上级目录
        with self.assertRaises(ValueError) as context:
            self.loader.load('../sensitive')

        self.assertIn('路径遍历', str(context.exception))

    def test_absolute_path_blocked(self):
        """测试绝对路径被阻止"""
        # 尝试使用绝对路径
        absolute_path = str(self.sensitive_file).replace('.md', '')
        with self.assertRaises(ValueError) as context:
            self.loader.load(absolute_path)

        self.assertIn('绝对路径', str(context.exception))

    def test_windows_path_traversal_blocked(self):
        """测试 Windows 路径遍历被阻止"""
        # 尝试使用 ..\ 访问上级目录
        with self.assertRaises(ValueError) as context:
            self.loader.load('..\\sensitive')

        self.assertIn('路径遍历', str(context.exception))

    def test_complex_traversal_blocked(self):
        """测试复杂路径遍历被阻止"""
        # 尝试多级遍历
        with self.assertRaises(ValueError) as context:
            self.loader.load('skills/../../sensitive')

        self.assertIn('路径遍历', str(context.exception))

    def test_symlink_traversal_blocked(self):
        """测试符号链接越界被阻止"""
        # 创建指向外部的符号链接
        link_path = self.agents_dir / 'evil_link.md'
        try:
            link_path.symlink_to(self.sensitive_file)

            # 尝试通过符号链接访问
            with self.assertRaises(ValueError) as context:
                self.loader.load('evil_link')

            self.assertIn('路径越界', str(context.exception))
        except OSError:
            # 某些系统可能不支持创建符号链接，跳过此测试
            self.skipTest("系统不支持符号链接")

    def test_legitimate_path_allowed(self):
        """测试合法路径被允许"""
        # 加载根目录下的文件
        content = self.loader.load('WORKFLOWS')
        self.assertEqual(content, '合法的工作流提示词')

        # 加载子目录下的文件
        content = self.loader.load('skills/test-skill')
        self.assertEqual(content, '合法的技能提示词')

    def test_exists_with_traversal_returns_false(self):
        """测试 exists 方法对越界路径返回 False"""
        # 合法路径应该返回 True
        self.assertTrue(self.loader.exists('WORKFLOWS'))

        # 越界路径应该返回 False
        self.assertFalse(self.loader.exists('../sensitive'))
        self.assertFalse(self.loader.exists('..\\sensitive'))

    def test_exists_with_absolute_path_returns_false(self):
        """测试 exists 方法对绝对路径返回 False"""
        absolute_path = str(self.sensitive_file).replace('.md', '')
        self.assertFalse(self.loader.exists(absolute_path))

    def test_cache_not_populated_on_blocked_path(self):
        """测试被阻止的路径不会污染缓存"""
        # 尝试加载越界路径
        try:
            self.loader.load('../sensitive')
        except ValueError:
            pass

        # 验证缓存中没有这个路径
        self.assertNotIn('../sensitive', self.loader._cache)


if __name__ == '__main__':
    unittest.main()
