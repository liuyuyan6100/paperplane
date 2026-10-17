# 提示词开发说明

本目录包含应用读取的提示词资源：[PERSONA.md](PERSONA.md) 和 [WORKFLOWS.md](WORKFLOWS.md)。它们是发给模型的模板，不是要求使用者在终端执行的命令，也不授予模型发布权限。技能模板实际位于 [skills/content](../skills/content)，不在 agents/skills。

在仓库根目录、已安装基础依赖的 Python 环境中运行以下完整示例；不调用模型、不写入业务数据：

```python
from studio.prompt_loader import load_prompt, get_prompt_loader, reload_prompts

assert load_prompt("PERSONA").strip()
assert load_prompt("WORKFLOWS").strip()
loader = get_prompt_loader()
assert loader.load("PERSONA") == load_prompt("PERSONA")
reload_prompts()
assert load_prompt("PERSONA").strip()
print("prompt resources OK")
```

加载器在内存缓存文本。修改模板后重新启动服务，或在同一进程调用 reload_prompts() 清缓存。传入 fallback 只处理缺失资源的兼容场景，不能用它掩盖打包遗漏；上面的检查故意不传 fallback。

PromptLoader 只读取 agents 下的资源；不要用不存在的 skills/generate-outline 作为该加载器路径。技能元数据和专用提示词由 studio/skills.py 与 studio/skill_executors.py 处理，见 [技能协议](../skills/SPEC.md)。

长期经验与画像是另外的运行时数据，见 [记忆开发示例](MEMORIES.md)。修改本目录文档不等于用户的账号已经保存了写作习惯。示例验证范围见 [公开文档核验表](../docs/VERIFICATION.md)。
