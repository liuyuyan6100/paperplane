# 纸飞机内置技能

当前实现和验证入口：[测试说明](../tests/README.md)。

七个内置技能：generate-outline、expand-section、review-draft、extract-materials、plan-topics、extract-memories、review-performance。
执行器全部位于 studio/skill_executors.py，元数据由 studio/skills.py 扫描。
技能是应用内确定的Python逻辑和Markdown资源，不是自动运行任意代码的插件市场。

依赖PyYAML 6.0.3；桌面打包和Docker包含skills/与agents/。默认启用，可用PAPERPLANE_USE_SKILLS=0回退原实现。
调用入口为 app.skill_system.execute(name, **inputs)，返回success/data/error/metadata。
完整协议见[SPEC.md](SPEC.md)，各技能输入见对应SKILL.md。

三个候选类技能复用现有业务提示词，返回raw供原业务解析器校验。generate_structured统一分派，只有执行前不可用才进入兼容路径；执行失败不回退重发。选题选择、候选确认和复盘证据校验仍由原业务模块负责。

旧文档曾列出尚未实现的发布技能、性能数值和覆盖率，现以实际测试报告为准。
