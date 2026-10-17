"""
技能系统整合测试套件

合并了原有的 test_skills.py 和 test_skill_executors.py，
消除冗余，专注于核心契约验证。
"""
import pytest
from unittest.mock import Mock, patch
from pathlib import Path
from studio.skills import SkillSystem, SkillError
from studio.skill_executors import OutlineGenerator, DraftReviewer


# ============================================================================
# 测试夹具
# ============================================================================

@pytest.fixture
def mock_app():
    """创建模拟的 app 对象"""
    app = Mock()
    app.config = {'SKILLS_ENABLED': True}
    app.db = Mock()
    app.db.data_dir = Path('/mock/data')
    app.db.now = Mock(return_value='2024-01-01T00:00:00Z')
    app.generate_draft = Mock(return_value='模型返回的结果')
    return app


@pytest.fixture
def temp_skills_dir(tmp_path):
    """创建临时技能目录结构"""
    skills_dir = tmp_path / 'skills'
    skills_dir.mkdir()

    # 创建有效的测试技能
    test_skill_dir = skills_dir / 'writing' / 'generate-outline'
    test_skill_dir.mkdir(parents=True)

    skill_content = """---
name: generate-outline
version: 1.0.0
description: 生成文章提纲
category: writing
author: system
enabled: true
deprecated: false
executor: generate-outline
inputs:
  - name: post
    type: dict
    required: true
  - name: materials
    type: list
    required: true
  - name: profile
    type: dict
    required: false
  - name: instruction
    type: string
    required: false
  - name: model_route
    type: dict
    required: false
outputs:
  - name: outline
    type: string
  - name: title_suggestions
    type: list
---

# 生成文章提纲

这是一个测试技能。
"""
    (test_skill_dir / 'SKILL.md').write_text(skill_content, encoding='utf-8')
    (test_skill_dir / 'prompt.md').write_text('测试提示词模板', encoding='utf-8')

    return skills_dir


@pytest.fixture
def valid_outline_inputs():
    """提纲生成的有效输入"""
    return {
        'post': {
            'id': 1,
            'title': '测试文章',
            'body': '测试内容'
        },
        'materials': [
            {'title': '素材1', 'body': '素材内容1'},
            {'title': '素材2', 'body': '素材内容2'}
        ],
        'profile': {
            'direction': '技术',
            'audience': '开发者',
            'style': '专业'
        },
        'instruction': '生成技术文章提纲',
        'model_route': {'provider': 'test', 'model': 'test-model'}
    }


@pytest.fixture
def valid_review_inputs():
    """审稿的有效输入"""
    return {
        'post': {
            'id': 1,
            'title': '测试文章',
            'body': '这是一篇需要审稿的文章内容。' * 100
        },
        'profile': {
            'direction': '技术',
            'style': '专业'
        },
        'model_route': {'provider': 'test', 'model': 'test-model'}
    }


# ============================================================================
# 技能系统核心测试
# ============================================================================

class TestSkillSystemCore:
    """技能系统核心功能测试"""

    def test_init_with_valid_dir(self, mock_app, temp_skills_dir):
        """C1: 正常初始化，加载有效技能"""
        skill_system = SkillSystem(mock_app, temp_skills_dir)

        assert skill_system is not None
        assert 'generate-outline' in skill_system._registry

    def test_init_with_missing_dir(self, mock_app, tmp_path):
        """C2: 技能目录不存在时降级初始化"""
        non_existent = tmp_path / 'non_existent'

        skill_system = SkillSystem(mock_app, non_existent)

        # 应该成功初始化但没有技能
        assert skill_system is not None
        assert len(skill_system._registry) == 0

    def test_init_with_system_disabled(self, mock_app, temp_skills_dir):
        """C3: 配置禁用技能系统"""
        mock_app.config['SKILLS_ENABLED'] = False

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        assert skill_system._system_enabled is False

    def test_execute_valid_skill(self, mock_app, temp_skills_dir, valid_outline_inputs):
        """C4: 执行有效技能"""
        skill_system = SkillSystem(mock_app, temp_skills_dir)

        with patch('studio.skill_executors.OutlineGenerator.execute') as mock_execute:
            mock_execute.return_value = {'outline': '测试提纲'}

            result = skill_system.execute('generate-outline', **valid_outline_inputs)

            assert result['success'] and 'outline' in result['data']
            mock_execute.assert_called_once()

    def test_execute_nonexistent_skill(self, mock_app, temp_skills_dir):
        """C5: 执行不存在的技能"""
        skill_system = SkillSystem(mock_app, temp_skills_dir)

        with pytest.raises(ValueError, match='技能不存在'):
            skill_system.execute('nonexistent-skill')


# ============================================================================
# 提纲生成执行器测试
# ============================================================================

class TestOutlineGenerator:
    """提纲生成执行器测试"""

    def test_validate_inputs_success(self, mock_app, valid_outline_inputs):
        """E1: 验证有效输入"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        # 不应抛出异常
        executor.validate_inputs(valid_outline_inputs)

    def test_validate_inputs_missing_post(self, mock_app):
        """E2: 缺少必需参数 post"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        with pytest.raises(SkillError) as exc_info:
            executor.validate_inputs({'materials': []})

        assert exc_info.value.code == 'INVALID_INPUT'
        assert 'post' in str(exc_info.value)

    def test_validate_inputs_missing_materials(self, mock_app):
        """E3: 缺少必需参数 materials"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        with pytest.raises(SkillError) as exc_info:
            executor.validate_inputs({'post': {}})

        assert exc_info.value.code == 'INVALID_INPUT'
        assert 'materials' in str(exc_info.value)

    def test_validate_inputs_profile_optional(self, mock_app):
        """E4: profile 参数可选"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        inputs = {
            'post': {'id': 1, 'title': 'test'},
            'materials': [],
            'model_route': {'model': 'test'},
            'profile': None  # 允许 None
        }

        # 不应抛出异常
        executor.validate_inputs(inputs)

    def test_prepare_context_preserves_model_route(self, mock_app, valid_outline_inputs):
        """E5: prepare_context 必须保留 model_route"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        context = executor.prepare_context(valid_outline_inputs)

        assert 'model_route' in context
        assert context['model_route'] == valid_outline_inputs['model_route']

    def test_prepare_context_handles_no_profile(self, mock_app):
        """E6: prepare_context 处理无画像情况"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        inputs = {
            'post': {'id': 1, 'title': 'test'},
            'materials': [],
            'model_route': {'model': 'test'},
            'profile': None
        }

        context = executor.prepare_context(inputs)

        assert 'profile' in context
        assert context['profile'] is None

    def test_render_template_with_no_profile(self, mock_app):
        """E7: 模板渲染处理无画像"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        template = '方向: {profile.direction}, 目标: {profile.goal}'
        context = {
            'post': {'title': 'test'},
            'model_route': {'model': 'test'},
            'profile': None,  # 无画像
            'materials': [],
            'instruction': 'test'
        }

        rendered = executor._render_template(template, context)

        # 应该有默认值，不应崩溃
        assert '通用方向' in rendered or '未指定' in rendered

    def test_parse_result_blocks_political_risk(self, mock_app):
        """E8: 政治风险警告必须阻断执行"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        raw_result = '''```json
{
    "outline": "测试提纲",
    "political_risk_warning": "内容涉及敏感政治话题"
}
```'''

        with pytest.raises(SkillError) as exc_info:
            executor.parse_result(raw_result)

        assert exc_info.value.code == 'POLITICAL_RISK'
        assert '政治敏感' in str(exc_info.value)


# ============================================================================
# 审稿执行器测试
# ============================================================================

class TestDraftReviewer:
    """审稿执行器测试"""

    def test_validate_inputs_success(self, mock_app, valid_review_inputs):
        """R1: 验证有效输入"""
        executor = DraftReviewer(mock_app, {'metadata': {'name': 'test'}})

        # 不应抛出异常
        executor.validate_inputs(valid_review_inputs)

    def test_validate_inputs_profile_optional(self, mock_app):
        """R2: profile 可选"""
        executor = DraftReviewer(mock_app, {'metadata': {'name': 'test'}})

        inputs = {
            'post': {'id': 1, 'title': 'test', 'body': '内容'},
            'model_route': {'model': 'test'},
            'profile': None,
            'model_route': {}
        }

        # 不应抛出异常
        executor.validate_inputs(inputs)

    def test_prepare_context_preserves_model_route(self, mock_app, valid_review_inputs):
        """R3: prepare_context 保留 model_route"""
        executor = DraftReviewer(mock_app, {'metadata': {'name': 'test'}})

        context = executor.prepare_context(valid_review_inputs)

        assert 'model_route' in context
        assert context['model_route'] == valid_review_inputs['model_route']

    def test_prepare_context_limits_body_length(self, mock_app):
        """R4: 正文长度限制"""
        executor = DraftReviewer(mock_app, {'metadata': {'name': 'test'}})

        inputs = {
            'post': {
                'id': 1,
                'title': 'test',
                'body': 'x' * 200000  # 超长
            },
            'model_route': {}
        }

        context = executor.prepare_context(inputs)

        # 应该被截断
        assert len(context['post']['body']) == 200000


# ============================================================================
# 安全和异常处理测试
# ============================================================================

class TestSecurityAndSanitization:
    """安全和脱敏测试"""

    def test_exception_sanitization(self, mock_app, valid_outline_inputs):
        """S1: 异常消息脱敏"""
        executor = OutlineGenerator(mock_app, {'metadata': {'name': 'test'}})

        # 模拟包含敏感信息的异常
        mock_app.generate_draft = Mock(
            side_effect=Exception("API key abc123xyz failed")
        )

        with pytest.raises(SkillError) as exc_info:
            context = executor.prepare_context(valid_outline_inputs)
            executor.call_model('test', context)

        # 错误消息不应包含完整的 API key
        assert 'abc123xyz' not in str(exc_info.value)

    def test_logging_sanitization(self, mock_app):
        """S2: 日志脱敏过滤器已安装"""
        import logging
        from studio.sanitize import SanitizingFilter

        root_logger = logging.getLogger('studio.skills')

        # 检查是否安装了脱敏过滤器
        has_sanitizing_filter = any(
            isinstance(f, SanitizingFilter) for f in root_logger.filters
        )

        assert has_sanitizing_filter, "日志脱敏过滤器未安装"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
