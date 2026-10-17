"""
F10: 验证测试套件契约
基于实际的 SkillSystem API
"""
import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from studio.skills import SkillSystem, SkillError


def test_skill_system_initialization():
    """验证技能系统初始化"""
    print("测试: 技能系统初始化...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    assert skill_system.app is app
    assert skill_system._registry is not None
    assert skill_system._loaded is not None
    assert skill_system._system_enabled is True
    print("✓ 技能系统初始化正确")


def test_skill_system_disabled_by_config():
    """验证配置禁用技能系统"""
    print("\n测试: 配置禁用技能系统...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': False}

    app = MockApp()
    skill_system = SkillSystem(app)

    assert skill_system._system_enabled is False
    print("✓ 配置禁用功能正常")


def test_list_skills():
    """验证列出技能"""
    print("\n测试: 列出技能...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    # 列出所有技能
    skills = skill_system.list_skills(enabled_only=False)

    assert isinstance(skills, list)
    print(f"✓ 发现 {len(skills)} 个技能")

    # 验证至少有一些技能
    if skills:
        print(f"  示例技能: {skills[0].get('name', 'unknown')}")


def test_get_metadata():
    """验证获取技能元数据"""
    print("\n测试: 获取技能元数据...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    skills = skill_system.list_skills(enabled_only=False)

    if not skills:
        print("⚠ 没有技能可供测试")
        return

    # 获取第一个技能的元数据
    first_skill_name = skills[0]['name']
    metadata = skill_system.get_metadata(first_skill_name)

    assert metadata is not None
    assert 'name' in metadata
    assert 'version' in metadata
    assert 'description' in metadata
    assert 'category' in metadata
    print(f"✓ 成功获取技能元数据: {first_skill_name}")


def test_enabled_check():
    """验证技能启用状态检查"""
    print("\n测试: 检查技能启用状态...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    skills = skill_system.list_skills(enabled_only=False)

    if not skills:
        print("⚠ 没有技能可供测试")
        return

    first_skill_name = skills[0]['name']
    is_enabled = skill_system.enabled(first_skill_name)

    assert isinstance(is_enabled, bool)
    print(f"✓ 技能 {first_skill_name} 启用状态: {is_enabled}")


def test_skill_error_structure():
    """验证 SkillError 结构"""
    print("\n测试: SkillError 结构...")

    error = SkillError("TEST_ERROR", "测试错误消息", {"key": "value"})

    assert error.code == "TEST_ERROR"
    assert error.message == "测试错误消息"
    assert error.details == {"key": "value"}
    assert "测试错误消息" in str(error)

    print(f"✓ SkillError 结构正确: {error}")


def test_skill_metadata_structure():
    """验证技能元数据结构"""
    print("\n测试: 技能元数据结构...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    skills = skill_system.list_skills(enabled_only=False)

    if not skills:
        print("⚠ 没有技能可供验证")
        return

    required_fields = ['name', 'version', 'description', 'category']

    for skill in skills:
        for field in required_fields:
            assert field in skill, f"技能缺少 {field}: {skill.get('name', 'unknown')}"

    print(f"✓ {len(skills)} 个技能的元数据结构正确")


def test_nonexistent_skill():
    """验证不存在的技能"""
    print("\n测试: 访问不存在的技能...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    # 检查不存在的技能
    assert not skill_system.enabled("nonexistent_skill_xyz")
    assert skill_system.get_metadata("nonexistent_skill_xyz") is None

    print("✓ 不存在的技能处理正确")


def test_category_filter():
    """验证分类过滤"""
    print("\n测试: 分类过滤...")

    class MockApp:
        def __init__(self):
            self.db = None
            self.profiles = None
            self.config = {'SKILLS_ENABLED': True}

    app = MockApp()
    skill_system = SkillSystem(app)

    all_skills = skill_system.list_skills(enabled_only=False)

    if not all_skills:
        print("⚠ 没有技能可供测试")
        return

    # 获取第一个技能的分类
    first_category = all_skills[0].get('category')
    if first_category:
        filtered_skills = skill_system.list_skills(category=first_category, enabled_only=False)
        assert all(s['category'] == first_category for s in filtered_skills)
        print(f"✓ 分类过滤正确: {first_category} ({len(filtered_skills)} 个技能)")
    else:
        print("⚠ 第一个技能没有分类")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("F10: 测试套件契约验证")
    print("=" * 60)

    tests = [
        test_skill_system_initialization,
        test_skill_system_disabled_by_config,
        test_list_skills,
        test_get_metadata,
        test_enabled_check,
        test_skill_error_structure,
        test_skill_metadata_structure,
        test_nonexistent_skill,
        test_category_filter,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\n✗ 测试失败: {test.__name__}")
            print(f"  {e}")
            failed += 1
        except Exception as e:
            print(f"\n✗ 测试错误: {test.__name__}")
            print(f"  {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
