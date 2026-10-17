"""
测试运行脚本 - 生成详细的测试报告
"""
import sys
import subprocess
from pathlib import Path
import json
from datetime import datetime


def run_tests_with_report():
    """运行测试并生成详细报告"""

    print("=" * 80)
    print("纸飞机项目 - Phase 1 测试套件")
    print("=" * 80)
    print()

    # 确定项目根目录
    project_root = Path(__file__).parent.parent

    # 运行 pytest 并生成 JSON 报告
    pytest_args = [
        'pytest',
        'tests/test_skills.py',
        '-v',                           # 详细输出
        '--tb=short',                   # 简短的回溯
        '--junit-xml=test-results.xml', # JUnit XML 格式
        '--json-report',                # JSON 报告
        '--json-report-file=test-report.json',
    ]

    print("运行测试命令:")
    print(" ".join(pytest_args))
    print()

    # 执行测试
    result = subprocess.run(
        pytest_args,
        cwd=project_root,
        capture_output=True,
        text=True
    )

    # 输出测试结果
    print(result.stdout)
    if result.stderr:
        print("错误输出:")
        print(result.stderr)

    print()
    print("=" * 80)

    # 解析 JSON 报告
    json_report_path = project_root / 'test-report.json'
    if json_report_path.exists():
        try:
            with open(json_report_path, 'r', encoding='utf-8') as f:
                report = json.load(f)

            print("测试摘要:")
            print(f"  总计: {report['summary']['total']} 个测试")
            print(f"  通过: {report['summary'].get('passed', 0)} 个")
            print(f"  失败: {report['summary'].get('failed', 0)} 个")
            print(f"  跳过: {report['summary'].get('skipped', 0)} 个")
            print(f"  耗时: {report['duration']:.2f} 秒")
            print()

            # 如果有失败的测试，显示详情
            if report['summary'].get('failed', 0) > 0:
                print("失败的测试:")
                for test in report['tests']:
                    if test['outcome'] == 'failed':
                        print(f"  ❌ {test['nodeid']}")
                        if 'call' in test and 'longrepr' in test['call']:
                            print(f"     {test['call']['longrepr'][:200]}...")
                print()

        except Exception as e:
            print(f"解析 JSON 报告失败: {e}")

    # 生成人类可读的报告
    report_path = project_root / 'test-report.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("纸飞机项目 - Phase 1 测试报告\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("测试命令:\n")
        f.write(" ".join(pytest_args) + "\n\n")

        f.write("测试输出:\n")
        f.write("-" * 80 + "\n")
        f.write(result.stdout)
        f.write("-" * 80 + "\n\n")

        if result.stderr:
            f.write("错误输出:\n")
            f.write("-" * 80 + "\n")
            f.write(result.stderr)
            f.write("-" * 80 + "\n\n")

        f.write(f"退出码: {result.returncode}\n")
        f.write(f"状态: {'✅ 全部通过' if result.returncode == 0 else '❌ 有测试失败'}\n")

    print(f"详细报告已保存到: {report_path}")
    print("=" * 80)

    return result.returncode


if __name__ == '__main__':
    exit_code = run_tests_with_report()
    sys.exit(exit_code)
