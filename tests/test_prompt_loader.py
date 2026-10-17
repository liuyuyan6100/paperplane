"""
提示词加载器单元测试

测试 prompt_loader.py 的核心功能
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from studio.prompt_loader import (
    PromptLoader,
    load_prompt,
    reload_prompts,
    get_prompt_loader,
    FALLBACK_WORKFLOWS
)


class TestPromptLoader:
    """测试 PromptLoader 类"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def agents_dir(self, temp_dir):
        """创建 agents 目录"""
        agents = temp_dir / 'agents'
        agents.mkdir()
        return agents

    @pytest.fixture
    def sample_prompts(self, agents_dir):
        """创建示例提示词文件"""
        # PERSONA.md
        persona = agents_dir / 'PERSONA.md'
        persona.write_text("""---
type: persona
version: 1.0.0
---

你是一个实用的写作助手。
""", encoding='utf-8')

        # WORKFLOWS.md
        workflows = agents_dir / 'WORKFLOWS.md'
        workflows.write_text("""---
type: workflows
version: 1.0.0
---

## 对话工作流

处理用户的写作请求。
""", encoding='utf-8')

        return agents_dir

    def test_init(self, temp_dir):
        """测试初始化"""
        loader = PromptLoader(temp_dir / 'agents')
        assert loader.agents_dir == temp_dir / 'agents'
        assert loader._cache == {}

    def test_load_existing_file(self, temp_dir, sample_prompts):
        """测试加载存在的文件"""
        loader = PromptLoader(temp_dir / 'agents')

        content = loader.load('PERSONA')

        assert '实用的写作助手' in content
        assert loader._cache['PERSONA'] == content

    def test_load_with_fallback(self, temp_dir):
        """测试使用降级内容"""
        loader = PromptLoader(temp_dir / 'agents')

        fallback = "默认提示词"
        content = loader.load('NONEXISTENT', fallback)

        assert content == fallback

    def test_load_without_fallback_raises(self, temp_dir):
        """测试不存在且无降级内容时抛出异常"""
        loader = PromptLoader(temp_dir / 'agents')

        with pytest.raises(FileNotFoundError):
            loader.load('NONEXISTENT')

    def test_strip_frontmatter(self, temp_dir):
        """测试去除 frontmatter"""
        loader = PromptLoader(temp_dir / 'agents')

        content = """---
type: test
version: 1.0.0
---

实际内容在这里
"""
        loader.agents_dir.mkdir(exist_ok=True)
        (loader.agents_dir / 'TEST.md').write_text(content, encoding='utf-8')
        stripped = loader.load('TEST')

        assert '实际内容在这里' in stripped
        assert '---' not in stripped
        assert 'type: test' not in stripped

    def test_strip_frontmatter_no_frontmatter(self, temp_dir):
        """测试没有 frontmatter 的内容"""
        loader = PromptLoader(temp_dir / 'agents')

        content = "直接的内容"
        loader.agents_dir.mkdir(exist_ok=True)
        (loader.agents_dir / 'TEST.md').write_text(content, encoding='utf-8')
        stripped = loader.load('TEST')

        assert stripped == content

    def test_cache(self, temp_dir, sample_prompts):
        """测试缓存机制"""
        loader = PromptLoader(temp_dir / 'agents')

        # 第一次加载
        content1 = loader.load('PERSONA')
        assert 'PERSONA' in loader._cache

        # 第二次加载（从缓存）
        content2 = loader.load('PERSONA')
        assert content1 == content2

    def test_reload(self, temp_dir, sample_prompts):
        """测试重新加载"""
        loader = PromptLoader(temp_dir / 'agents')

        # 加载
        content1 = loader.load('PERSONA')

        # 修改文件
        persona = temp_dir / 'agents' / 'PERSONA.md'
        persona.write_text("修改后的内容", encoding='utf-8')

        # 重新加载
        loader.reload()
        content2 = loader.load('PERSONA')

        assert content1 != content2
        assert '修改后的内容' in content2

    def test_exists(self, temp_dir, sample_prompts):
        """测试检查文件存在"""
        loader = PromptLoader(temp_dir / 'agents')

        assert loader.exists('PERSONA')
        assert loader.exists('WORKFLOWS')
        assert not loader.exists('NONEXISTENT')


class TestModuleFunctions:
    """测试模块级函数"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_base_dir(self, temp_dir):
        """Mock BASE_DIR"""
        with patch('studio.prompt_loader._prompt_loader', PromptLoader(temp_dir / 'agents')):
            yield temp_dir

    def test_get_prompt_loader_singleton(self, mock_base_dir):
        """测试单例模式"""
        loader1 = get_prompt_loader()
        loader2 = get_prompt_loader()

        assert loader1 is loader2

    def test_load_prompt(self, mock_base_dir):
        """测试加载提示词"""
        # 创建文件
        agents_dir = mock_base_dir / 'agents'
        agents_dir.mkdir()

        test_file = agents_dir / 'TEST.md'
        test_file.write_text('测试内容', encoding='utf-8')

        # 加载
        content = load_prompt('TEST')

        assert '测试内容' in content

    def test_load_prompt_with_fallback(self, mock_base_dir):
        """测试带降级的加载"""
        fallback = '降级内容'
        content = load_prompt('NONEXISTENT', fallback)

        assert content == fallback

    def test_reload_prompts(self, mock_base_dir):
        """测试重新加载所有提示词"""
        # 创建文件
        agents_dir = mock_base_dir / 'agents'
        agents_dir.mkdir()

        test_file = agents_dir / 'TEST.md'
        test_file.write_text('原始内容', encoding='utf-8')

        # 第一次加载
        content1 = load_prompt('TEST')

        # 修改文件
        test_file.write_text('修改后内容', encoding='utf-8')

        # 重新加载
        reload_prompts()
        content2 = load_prompt('TEST')

        assert content1 != content2
        assert '修改后内容' in content2


class TestEdgeCases:
    """测试边界情况"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_empty_file(self, temp_dir):
        """测试空文件"""
        agents_dir = temp_dir / 'agents'
        agents_dir.mkdir()

        empty_file = agents_dir / 'EMPTY.md'
        empty_file.write_text('', encoding='utf-8')

        loader = PromptLoader(temp_dir / 'agents')
        content = loader.load('EMPTY')

        assert content == ''

    def test_only_frontmatter(self, temp_dir):
        """测试只有 frontmatter 没有内容"""
        agents_dir = temp_dir / 'agents'
        agents_dir.mkdir()

        file = agents_dir / 'ONLY_FM.md'
        file.write_text("""---
type: test
---
""", encoding='utf-8')

        loader = PromptLoader(temp_dir / 'agents')
        content = loader.load('ONLY_FM')

        assert content.strip() == ''

    def test_malformed_frontmatter(self, temp_dir):
        """测试格式错误的 frontmatter"""
        agents_dir = temp_dir / 'agents'
        agents_dir.mkdir()

        file = agents_dir / 'MALFORMED.md'
        file.write_text("""---
这不是有效的 YAML
---

正常内容
""", encoding='utf-8')

        loader = PromptLoader(temp_dir / 'agents')
        # 应该能处理（去除 frontmatter）
        content = loader.load('MALFORMED')

        assert '正常内容' in content

    def test_unicode_content(self, temp_dir):
        """测试 Unicode 内容"""
        agents_dir = temp_dir / 'agents'
        agents_dir.mkdir()

        file = agents_dir / 'UNICODE.md'
        file.write_text('中文内容 🎉 Émojis', encoding='utf-8')

        loader = PromptLoader(temp_dir / 'agents')
        content = loader.load('UNICODE')

        assert '中文内容' in content
        assert '🎉' in content
        assert 'Émojis' in content

    def test_large_file(self, temp_dir):
        """测试大文件"""
        agents_dir = temp_dir / 'agents'
        agents_dir.mkdir()

        # 创建 1MB 的文件
        large_content = 'x' * (1024 * 1024)
        file = agents_dir / 'LARGE.md'
        file.write_text(large_content, encoding='utf-8')

        loader = PromptLoader(temp_dir / 'agents')
        content = loader.load('LARGE')

        assert len(content) == len(large_content)

    def test_agents_dir_not_exists(self, temp_dir):
        """测试 agents 目录不存在"""
        loader = PromptLoader(temp_dir / 'agents')

        fallback = '降级'
        content = loader.load('TEST', fallback)

        assert content == fallback


class TestFallbackWorkflows:
    """测试硬编码降级内容"""

    def test_fallback_workflows_exists(self):
        """测试 FALLBACK_WORKFLOWS 存在"""
        assert FALLBACK_WORKFLOWS is not None
        assert len(FALLBACK_WORKFLOWS) > 0

    def test_fallback_workflows_content(self):
        """测试降级内容包含关键信息"""
        # 应该包含基本的工作流说明
        assert '对话' in FALLBACK_WORKFLOWS or 'conversation' in FALLBACK_WORKFLOWS.lower()


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            # 创建 agents 目录和文件
            agents_dir = base_dir / 'agents'
            agents_dir.mkdir()

            persona = agents_dir / 'PERSONA.md'
            persona.write_text("""---
type: persona
---

你是助手
""", encoding='utf-8')

            workflows = agents_dir / 'WORKFLOWS.md'
            workflows.write_text("""---
type: workflows
---

处理请求
""", encoding='utf-8')

            # 创建加载器
            with patch('studio.prompt_loader._prompt_loader', PromptLoader(agents_dir)):
                # 加载 PERSONA
                persona_content = load_prompt('PERSONA')
                assert '你是助手' in persona_content

                # 加载 WORKFLOWS
                workflows_content = load_prompt('WORKFLOWS')
                assert '处理请求' in workflows_content

                # 加载不存在的（使用降级）
                fallback_content = load_prompt('NONEXISTENT', 'fallback')
                assert fallback_content == 'fallback'

                # 重新加载
                reload_prompts()

                # 再次加载
                persona_content2 = load_prompt('PERSONA')
                assert persona_content == persona_content2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
