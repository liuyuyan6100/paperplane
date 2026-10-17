# 测试入口

2026-10-15：原文档使用过不存在的API与模拟数据库方法。现在以真实临时Studio和外部模型边界模拟为集成验证。
当前验收见 [技能改造记录](../docs/skill-system/acceptance-20261015.md)。

在source目录，向隔离Python环境安装requirements-desktop.txt和requirements-test.txt。
运行 `python -m pytest -q` 执行默认完整测试；`python -m pytest tests/verify_f10.py` 运行额外契约检查。
默认不输出局部变量或强制生成覆盖率文件。覆盖率按需要显式指定，避免将部分模块覆盖率冒充整个项目。

关键用例：

- test_skill_pipeline.py：真实Studio、提纲确认、正文采用、审稿、风险与无效输出阻断、模型连接变化、Windows目录联接越界。
- test_skills_acceptance.py：根据第三轮独立审核迁入的验收探针，适配当前model_route必填契约；历史探针保留在原证据目录。
- test_f08_review_skill.py：真实临时Database验证时间函数，避免MagicMock人为创造不存在的db.now()。
- test_workflows.py、test_outlines.py、test_reviews.py：持久化、并发、版本冲突、人工确认与采用。
- test_chat_flow.py、test_memories.py、test_profiles.py、test_model_routing.py：对话、画像记忆、冻结模型与既有集成回归。

专项覆盖率示例：

```powershell
python -m pytest tests/test_skill_pipeline.py tests/test_skills.py tests/test_prompt_loader.py --cov=studio.skills --cov=studio.skill_executors --cov=studio.prompt_loader --cov=studio.sanitize --cov-report=term
```

该示例不是完整专项集合；实际本轮完整命令、JUnit和coverage JSON保存在项目work/test-results/skills-completion-20261015。
一个文件符号链接用例可能因Windows权限跳过；目录联接越界另有实际验证，不能把跳过写成通过。
模型响应均为合成或本机测试服务；这些测试不代表真实模型质量或外部平台公开发表验通。
