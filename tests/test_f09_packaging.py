"""
F09: 验证打包资源和依赖配置
"""
import os
import re
from pathlib import Path


def test_spec_includes_skills_and_agents():
    """验证 spec 文件包含 skills 和 agents 资源"""
    spec_path = Path(__file__).parent.parent / "PaperPlaneStudio.spec"
    content = spec_path.read_text(encoding='utf-8')

    # 检查 datas 包含 skills 和 agents
    assert "('skills', 'skills')" in content, "spec 必须包含 skills 目录"
    assert "('agents', 'agents')" in content, "spec 必须包含 agents 目录"


def test_spec_no_nonexistent_modules():
    """验证 spec 不包含不存在的模块"""
    spec_path = Path(__file__).parent.parent / "PaperPlaneStudio.spec"
    content = spec_path.read_text(encoding='utf-8')

    # 不应包含不存在的 agent_profiles 模块
    assert "studio.agent_profiles" not in content, "spec 不应包含不存在的 studio.agent_profiles"


def test_spec_includes_yaml():
    """验证 spec 包含 yaml 隐藏导入"""
    spec_path = Path(__file__).parent.parent / "PaperPlaneStudio.spec"
    content = spec_path.read_text(encoding='utf-8')

    assert "'yaml'" in content, "spec 必须包含 yaml 隐藏导入"


def test_requirements_desktop_includes_pyyaml():
    """验证 requirements-desktop.txt 包含 PyYAML"""
    req_path = Path(__file__).parent.parent / "requirements-desktop.txt"
    content = req_path.read_text(encoding='utf-8')

    assert re.search(r'PyYAML[>=]', content), "requirements-desktop.txt 必须包含 PyYAML"


def test_requirements_cloud_includes_pyyaml():
    """验证 requirements-cloud.txt 包含 PyYAML"""
    req_path = Path(__file__).parent.parent / "requirements-cloud.txt"
    content = req_path.read_text(encoding='utf-8')

    assert re.search(r'PyYAML[>=]', content), "requirements-cloud.txt 必须包含 PyYAML"


def test_dockerfile_copies_skills_and_agents():
    """验证 Dockerfile 复制 skills 和 agents 目录"""
    dockerfile_path = Path(__file__).parent.parent / "Dockerfile"
    content = dockerfile_path.read_text(encoding='utf-8')

    assert "COPY skills/" in content, "Dockerfile 必须复制 skills 目录"
    assert "COPY agents/" in content, "Dockerfile 必须复制 agents 目录"


def test_build_script_includes_skills_and_agents():
    """验证构建脚本包含 skills 和 agents 资源"""
    build_script = Path(__file__).parent.parent / "tools" / "build_desktop.ps1"
    content = build_script.read_text(encoding='utf-8')

    # 检查 --add-data 参数
    assert '"$projectRoot\\skills;skills"' in content, "构建脚本必须包含 skills 目录"
    assert '"$projectRoot\\agents;agents"' in content, "构建脚本必须包含 agents 目录"


def test_build_script_includes_hidden_imports():
    """验证构建脚本包含必要的隐藏导入"""
    build_script = Path(__file__).parent.parent / "tools" / "build_desktop.ps1"
    content = build_script.read_text(encoding='utf-8')

    assert "--hidden-import studio.skills" in content, "构建脚本必须包含 studio.skills 隐藏导入"
    assert "--hidden-import studio.skill_executors" in content, "构建脚本必须包含 studio.skill_executors 隐藏导入"
    assert "--hidden-import yaml" in content, "构建脚本必须包含 yaml 隐藏导入"


def test_skills_directory_exists():
    """验证 skills 目录存在"""
    skills_dir = Path(__file__).parent.parent / "skills"
    assert skills_dir.exists(), "skills 目录必须存在"
    assert skills_dir.is_dir(), "skills 必须是目录"


def test_agents_directory_exists():
    """验证 agents 目录存在"""
    agents_dir = Path(__file__).parent.parent / "agents"
    assert agents_dir.exists(), "agents 目录必须存在"
    assert agents_dir.is_dir(), "agents 必须是目录"


if __name__ == "__main__":
    # 运行所有测试
    import sys

    tests = [
        ("spec 包含 skills 和 agents", test_spec_includes_skills_and_agents),
        ("spec 不包含不存在的模块", test_spec_no_nonexistent_modules),
        ("spec 包含 yaml", test_spec_includes_yaml),
        ("requirements-desktop 包含 PyYAML", test_requirements_desktop_includes_pyyaml),
        ("requirements-cloud 包含 PyYAML", test_requirements_cloud_includes_pyyaml),
        ("Dockerfile 复制 skills 和 agents", test_dockerfile_copies_skills_and_agents),
        ("构建脚本包含 skills 和 agents", test_build_script_includes_skills_and_agents),
        ("构建脚本包含隐藏导入", test_build_script_includes_hidden_imports),
        ("skills 目录存在", test_skills_directory_exists),
        ("agents 目录存在", test_agents_directory_exists),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            print(f"✓ {name}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {name}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
