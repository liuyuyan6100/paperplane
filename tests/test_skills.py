"""
技能系统单元测试
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
from studio.skills import SkillSystem, SkillError


@pytest.fixture
def mock_app():
    """创建模拟的 app 对象"""
    app = Mock()
    app.config = {'SKILLS_ENABLED': True}
    app.db = Mock()
    app.db.data_dir = Path('/mock/data')
    return app


@pytest.fixture
def temp_skills_dir(tmp_path):
    """创建临时技能目录结构"""
    skills_dir = tmp_path / 'skills'
    skills_dir.mkdir()

    # 创建一个有效的测试技能
    test_skill_dir = skills_dir / 'test' / 'test-skill'
    test_skill_dir.mkdir(parents=True)

    skill_content = """---
name: test-skill
version: 1.0.0
description: 测试技能
category: test
author: test
enabled: true
deprecated: false
---

# 测试技能

这是一个测试技能。
"""
    (test_skill_dir / 'SKILL.md').write_text(skill_content, encoding='utf-8')

    return skills_dir


class TestSkillSystemInit:
    """测试技能系统初始化"""

    def test_init_success(self, mock_app, temp_skills_dir):
        """测试正常初始化"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        assert skill_system is not None
        assert skill_system._registry is not None
        assert len(skill_system._registry) > 0

    def test_init_missing_skills_dir(self, mock_app, tmp_path):
        """测试技能目录不存在时的降级"""
        non_existent = tmp_path / 'non_existent'


        # 应该降级初始化，不抛出异常
        skill_system = SkillSystem(mock_app, non_existent)

        assert skill_system is not None
        assert len(skill_system._registry) == 0


class TestSkillRegistration:
    """测试技能注册"""

    def test_register_valid_skill(self, mock_app, temp_skills_dir):
        """测试注册有效技能"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        assert 'test-skill' in skill_system._registry
        metadata = skill_system.get_metadata('test-skill')
        assert metadata['name'] == 'test-skill'
        assert metadata['version'] == '1.0.0'

    def test_register_invalid_skill_ignored(self, mock_app, tmp_path):
        """测试无效技能被忽略"""
        skills_dir = tmp_path / 'skills'
        skills_dir.mkdir()

        # 创建一个缺少 frontmatter 的技能
        invalid_dir = skills_dir / 'test' / 'invalid-skill'
        invalid_dir.mkdir(parents=True)
        (invalid_dir / 'SKILL.md').write_text('No frontmatter', encoding='utf-8')


        skill_system = SkillSystem(mock_app, skills_dir)

        # 无效技能应该被跳过
        assert 'invalid-skill' not in skill_system._registry


class TestSkillQuery:
    """测试技能查询"""

    def test_enabled_check(self, mock_app, temp_skills_dir):
        """测试技能启用状态检查"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        assert skill_system.enabled('test-skill') is True
        assert skill_system.enabled('non-existent') is False

    def test_get_metadata(self, mock_app, temp_skills_dir):
        """测试获取技能元数据"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        metadata = skill_system.get_metadata('test-skill')
        assert metadata is not None
        assert metadata['name'] == 'test-skill'

        # 不存在的技能返回 None
        assert skill_system.get_metadata('non-existent') is None

    def test_list_skills(self, mock_app, temp_skills_dir):
        """测试列出技能"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        all_skills = skill_system.list_skills(enabled_only=False)
        assert len(all_skills) > 0

        enabled_skills = skill_system.list_skills(enabled_only=True)
        assert len(enabled_skills) > 0

        # 按分类过滤
        test_skills = skill_system.list_skills(category='test')
        assert all(s['category'] == 'test' for s in test_skills)


class TestSkillExecution:
    """测试技能执行"""

    def test_execute_non_existent_skill(self, mock_app, temp_skills_dir):
        """测试执行不存在的技能"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        with pytest.raises(ValueError, match="技能不存在"):
            skill_system.execute('non-existent-skill', test_input='value')

    def test_execute_disabled_skill(self, mock_app, tmp_path):
        """测试执行未启用的技能"""
        skills_dir = tmp_path / 'skills'
        skills_dir.mkdir()

        # 创建一个禁用的技能
        disabled_dir = skills_dir / 'test' / 'disabled-skill'
        disabled_dir.mkdir(parents=True)

        skill_content = """---
name: disabled-skill
version: 1.0.0
description: 禁用技能
category: test
enabled: false
---

测试
"""
        (disabled_dir / 'SKILL.md').write_text(skill_content, encoding='utf-8')


        skill_system = SkillSystem(mock_app, skills_dir)

        with pytest.raises(ValueError, match="技能未启用"):
            skill_system.execute('disabled-skill', test_input='value')

    def test_execute_response_format(self, mock_app, temp_skills_dir):
        """测试执行返回的响应格式"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        # Mock 执行器
        mock_executor = Mock()
        mock_executor.execute = Mock(return_value={'outline': 'test outline'})

        with patch('studio.skill_executors.get_executor') as mock_get_executor:
            mock_get_executor.return_value = Mock(return_value=mock_executor)

            result = skill_system.execute('test-skill', input_data='test')

            # 验证响应格式
            assert 'success' in result
            assert 'data' in result
            assert 'error' in result
            assert 'metadata' in result

            # 验证成功响应
            assert result['success'] is True
            assert result['data'] == {'outline': 'test outline'}
            assert result['error'] is None

            # 验证元数据
            metadata = result['metadata']
            assert 'skill' in metadata
            assert 'version' in metadata
            assert 'duration_ms' in metadata
            assert 'timestamp' in metadata


class TestSkillError:
    """测试技能错误处理"""

    def test_skill_error_creation(self):
        """测试 SkillError 对象创建"""
        error = SkillError('TEST_ERROR', 'Test message', {'detail': 'value'})

        assert error.code == 'TEST_ERROR'
        assert error.message == 'Test message'
        assert error.details == {'detail': 'value'}
        assert str(error) == 'Test message'

    def test_execute_with_skill_error(self, mock_app, temp_skills_dir):
        """测试技能执行中的 SkillError"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        # Mock 执行器抛出 SkillError
        mock_executor = Mock()
        mock_executor.execute = Mock(side_effect=SkillError(
            'VALIDATION_ERROR',
            '输入验证失败',
            {'field': 'test_input'}
        ))

        with patch('studio.skill_executors.get_executor') as mock_get_executor:
            mock_get_executor.return_value = Mock(return_value=mock_executor)

            result = skill_system.execute('test-skill', input_data='test')

            # 验证错误响应
            assert result['success'] is False
            assert result['data'] is None
            assert result['error'] is not None

            error = result['error']
            assert error['code'] == 'VALIDATION_ERROR'
            assert error['message'] == '输入验证失败'
            assert error['details'] == {'field': 'test_input'}

    def test_execute_with_unexpected_error(self, mock_app, temp_skills_dir):
        """测试技能执行中的未预期异常"""

        skill_system = SkillSystem(mock_app, temp_skills_dir)

        # Mock 执行器抛出普通异常
        mock_executor = Mock()
        mock_executor.execute = Mock(side_effect=RuntimeError('Unexpected error'))

        with patch('studio.skill_executors.get_executor') as mock_get_executor:
            mock_get_executor.return_value = Mock(return_value=mock_executor)

            result = skill_system.execute('test-skill', input_data='test')

            # 验证错误响应
            assert result['success'] is False
            assert result['data'] is None
            assert result['error'] is not None

            error = result['error']
            assert error['code'] == 'INTERNAL_ERROR'
            assert 'Unexpected error' in error['message']
