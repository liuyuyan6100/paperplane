# 纸飞机提示词系统

**版本**: 1.0.0  
**日期**: 2026-10-13  
**状态**: 已实施

---

## 概述

提示词系统将分散在代码中的硬编码提示词提取到独立的 Markdown 文件中，实现统一管理、版本控制和按需加载。

## 目录结构

```
agents/
├── README.md           # 本文档
├── PERSONA.md          # 助手人格定义
├── WORKFLOWS.md        # 对话工作流提示词
└── skills/             # 技能专用提示词（未来）
    ├── generate-outline.md
    └── review-draft.md
```

## 提示词文件

### PERSONA.md - 助手人格

**用途**: 定义纸飞机助手的核心特质、工作原则和边界

**包含内容**:
- 角色定位：实用导向、尊重创作者、务实准确、贴心周到
- 工作原则：尊重画像、任务执行、素材处理、对话交互
- 权限边界：可以做什么、不能做什么
- 质量标准：准确性、完整性、可读性
- 用语规范：状态表述、任务结果、等待确认、错误说明

**使用场景**: 
- 对话系统的基础人格
- 技能系统的通用指导
- 新功能开发的参考标准

### WORKFLOWS.md - 工作流提示词

**用途**: 定义对话任务助手的具体工作流程和操作规范

**包含内容**:
- 角色与输出格式
- 核心工作流程：
  - 素材查询与取材
  - 创建选题与文章
  - 生成提纲
  - 生成正文
  - 审稿建议
  - 平台改编
  - 配图规划
- 任务管理：创建、状态、避免重复、失败处理
- 上下文管理：当前消息、历史对话、实时状态
- 画像与记忆
- 系列与编号
- 参数格式
- 错误处理
- 权限边界

**使用场景**:
- chat.py 对话系统
- 对话相关的 API 调用

### skills/ - 技能提示词（未来）

**用途**: 每个技能的专用提示词

**规划**:
- `skills/generate-outline.md`: 提纲生成提示词
- `skills/write-article.md`: 正文生成提示词
- `skills/review-draft.md`: 审稿提示词

当前这些提示词已在 `skills/content/*/prompts/` 中定义。

## 提示词加载器

### 核心类：PromptLoader

位置：`studio/prompt_loader.py`

**功能**：
- 从 `agents/` 目录加载提示词文件
- 支持缓存，提高性能
- 降级到硬编码版本，确保向后兼容
- 自动去除 frontmatter

**使用方法**：

```python
from studio.prompt_loader import load_prompt, FALLBACK_WORKFLOWS

# 加载提示词，失败时降级到硬编码版本
workflows_prompt = load_prompt('WORKFLOWS', FALLBACK_WORKFLOWS)

# 加载技能提示词
outline_prompt = load_prompt('skills/generate-outline', fallback_text)
```

### 便捷函数

```python
# 加载提示词
from studio.prompt_loader import load_prompt
content = load_prompt('PERSONA')

# 重新加载所有提示词（清除缓存）
from studio.prompt_loader import reload_prompts
reload_prompts()

# 获取加载器实例
from studio.prompt_loader import get_prompt_loader
loader = get_prompt_loader()
```

## 代码集成

### 已集成模块

#### 1. studio/chat.py

**修改位置**: 第 586-627 行

**原实现**: 硬编码的长字符串提示词

**新实现**:
```python
try:
    from .prompt_loader import load_prompt, FALLBACK_WORKFLOWS
    workflows_prompt = load_prompt('WORKFLOWS', FALLBACK_WORKFLOWS)
except Exception:
    # 降级到硬编码版本
    workflows_prompt = (原硬编码内容)

instructions = workflows_prompt + platform_context + tool_specs
```

**优势**:
- 提示词可独立编辑和版本管理
- 失败时自动降级，不影响功能
- 代码更简洁，逻辑更清晰

### 待集成模块

未来可以集成提示词加载的模块：

- [ ] `studio/workflows.py`: 提纲和正文生成
- [ ] `studio/reviews.py`: 审稿建议
- [ ] `studio/topics.py`: 选题生成
- [ ] `studio/repurpose.py`: 平台改编

## 向后兼容策略

### 降级机制

所有提示词加载都有硬编码的降级版本：

```python
FALLBACK_PERSONA = """..."""
FALLBACK_WORKFLOWS = """..."""
```

加载失败时自动使用降级版本，确保功能正常运行。

### 渐进式迁移

- ✅ 第一步：提取对话工作流提示词（已完成）
- ⏳ 第二步：提取技能系统提示词（规划中）
- ⏳ 第三步：提取其他模块提示词（规划中）

每一步都保持向后兼容，不破坏现有功能。

## 提示词编写规范

### 文件格式

```markdown
# 提示词标题

**版本**: 1.0.0  
**日期**: 2026-10-13

---

## 章节1

内容...

## 章节2

内容...

## 变更日志

- **1.0.0** (2026-10-13): 初始版本
```

### 编写原则

1. **清晰具体**: 每条规则要明确，避免模糊
2. **分类组织**: 按功能或场景分类
3. **示例说明**: 复杂规则配合示例
4. **版本记录**: 记录变更历史

### 变量占位符

提示词中可使用变量占位符（在代码中替换）：

- `{profile.name}`: 画像名称
- `{post.title}`: 文章标题
- `{materials_content}`: 素材内容
- `{datetime}`: 当前时间

## 性能考虑

### 缓存机制

- 提示词加载后缓存在内存中
- 避免重复读取文件
- 缓存可手动清除重新加载

### 启动性能

- 首次加载：约 5-10ms/文件
- 后续使用：从缓存读取，< 1ms
- 对整体性能影响可忽略

## 维护指南

### 修改提示词

1. 编辑对应的 `.md` 文件
2. 更新版本号和变更日志
3. 测试功能是否正常
4. 提交代码

### 添加新提示词

1. 在 `agents/` 或 `agents/skills/` 创建 `.md` 文件
2. 按规范编写内容
3. 在代码中使用 `load_prompt()` 加载
4. 提供降级版本（可选但推荐）
5. 更新本文档

### 测试

```python
# 测试提示词加载
from studio.prompt_loader import load_prompt

# 应该成功加载
persona = load_prompt('PERSONA')
assert len(persona) > 0

# 应该降级到 fallback
fake = load_prompt('NON_EXISTENT', 'fallback content')
assert fake == 'fallback content'
```

## 故障排查

### 问题：提示词加载失败

**现象**: 日志中出现 "加载提示词失败"

**原因**:
- 文件不存在或路径错误
- 文件权限问题
- 编码问题（非 UTF-8）

**解决**:
1. 检查文件是否存在
2. 检查文件编码（应为 UTF-8）
3. 查看详细日志
4. 系统会自动降级，功能不受影响

### 问题：提示词未更新

**现象**: 修改了 `.md` 文件但未生效

**原因**: 缓存未清除

**解决**:
```python
from studio.prompt_loader import reload_prompts
reload_prompts()
```

或重启应用。

## 最佳实践

### 1. 提示词版本管理

- 每次重要修改更新版本号
- 记录变更历史
- 保留旧版本在 `versions/` 子目录

### 2. 分离关注点

- 通用规则放在 PERSONA.md
- 工作流规则放在 WORKFLOWS.md
- 技能专用放在 skills/

### 3. 测试驱动

- 修改提示词后测试相关功能
- 确认输出符合预期
- 检查边界情况

### 4. 文档同步

- 提示词即文档
- 保持代码注释与提示词一致
- 重大变更通知相关开发者

## 未来改进

### 短期（Phase 1）

- [x] 提取对话工作流提示词
- [ ] 提取技能系统提示词
- [ ] 单元测试覆盖

### 中期（Phase 2）

- [ ] 提示词模板引擎（变量替换）
- [ ] 提示词国际化支持
- [ ] 提示词版本兼容性检查

### 长期（Phase 3）

- [ ] 可视化提示词编辑器
- [ ] A/B 测试不同提示词版本
- [ ] 用户自定义提示词

## 参考资料

- [Easel Prompt Stack 设计](../../docs/easel-paperplane-对比分析报告.md)
- [技能系统规范](../skills/SPEC.md)
- [Phase 1 实施计划](../../../archive/windows/agent-workspaces/claude/Phase1-实施计划.md)

## 变更日志

- **1.0.0** (2026-10-13): 初始版本
  - 创建 PERSONA.md
  - 创建 WORKFLOWS.md
  - 实现 PromptLoader
  - 集成到 chat.py

---

**维护者**: 开发团队  
**最后更新**: 2026-10-13
