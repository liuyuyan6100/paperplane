# 纸飞机画像记忆系统

**版本**: 1.0.0  
**日期**: 2026-10-13  
**状态**: 生产就绪

---

## 概述

画像记忆系统允许用户积累、管理和应用长期的创作经验。每条记忆都有明确的来源、适用范围和生命周期管理。

## 核心概念

### 记忆条目（Memory Entry）

每条记忆包含：
- **内容**（content）：具体的经验或规则
- **来源**（source）：从哪里提炼出来的
- **范围**（scope）：在什么情况下适用
  - `platform`: 适用平台（如 "小红书"、"" 表示通用）
  - `keywords`: 适用主题（如 "产品评测,开箱"）
- **状态**（status）：生命周期状态

### 生命周期

```
propose (提出)
   ↓
pending (待确认) ──→ deleted (删除)
   ↓
accepted (已确认) ──→ disabled (停用) ──→ deleted
   ↑                     ↓
   └──── restore ────────┘
```

**状态说明**：
- `pending`: 候选经验，尚未确认
- `accepted`: 已确认，召回时生效
- `disabled`: 已停用，不再召回但保留记录
- `deleted`: 已删除，仅历史可见

### 来源类型

1. **post**（文章）
   - 从已发布文章中提炼
   - 记录文章 ID、版本、标题、正文摘录
   
2. **conversation**（对话）
   - 从对话记录中提炼
   - 记录对话 ID、标题、用户消息
   
3. **feedback**（用户反馈）
   - 用户手动输入的反馈
   - 记录反馈标题、原文

所有来源都包含 `quote`（原句引用），确保可追溯。

---

## API 使用

### 提出候选经验

```python
from studio.memories import propose

# 从文章提炼
memory = propose(app, profile_id, {
    'content': '产品评测文章要在开头说明测试环境和设备',
    'quote': '读者反馈说不知道我用的什么设备测试的',
    'platform': 'xiaohongshu',
    'keywords': '产品评测,开箱',
    'source_type': 'post',
    'source_id': 'abc123...',
    'source_version': 5
})

# 从对话提炼
memory = propose(app, profile_id, {
    'content': '标题不要用"竟然"等震惊体',
    'quote': '之前用震惊体的文章阅读量都很差',
    'platform': '',  # 通用规则
    'keywords': '',  # 通用规则
    'source_type': 'conversation',
    'source_id': 'def456...'
})

# 从用户反馈提炼
memory = propose(app, profile_id, {
    'content': '技术文章代码要注明语言和版本',
    'quote': '代码块要有语言标识',
    'platform': '',
    'keywords': '技术文章',
    'source_type': 'feedback',
    'source_title': '2026-10-13 反馈',
    'source_text': '代码块要有语言标识，不然不知道是什么语言'
})
```

**返回值**：
```python
{
    'id': '...',
    'profile_id': '...',
    'content': '...',
    'platform': '...',
    'keywords': '...',
    'source': {
        'type': 'post',
        'id': '...',
        'version': 5,
        'title': '...',
        'text': '...',
        'quote': '...',
        'at': '2026-10-13T...'
    },
    'status': 'pending',
    'version': 1,
    'created_at': '...',
    'updated_at': '...'
}
```

### AI 辅助提炼

```python
from studio.memories import extract

# 从文章自动提炼多条候选
result = extract(app, profile_id, {
    'source_type': 'post',
    'source_id': 'abc123...',
    'source_version': 5,
    'platform': 'xiaohongshu'  # 提炼时指定平台范围
})

# 返回最多 5 条候选
# result = {'memories': [memory1, memory2, ...]}
```

**AI 提炼规则**：
- 最多提炼 5 条
- 每条必须有原句引用（`quote`）
- 内容最多 1200 字
- 关键词最多 120 字
- 不虚构个人经历
- 不推断流量因果

### 确认经验

```python
from studio.memories import update

# 确认候选经验
memory = update(app, profile_id, memory_id, {
    'version': 1,
    'action': 'confirm'
})
# status: pending → accepted
```

### 编辑经验

```python
# 编辑内容或范围
memory = update(app, profile_id, memory_id, {
    'version': 1,
    'action': 'edit',
    'content': '修改后的内容',
    'platform': 'xiaohongshu',
    'keywords': '产品评测'
})
# status 变为 pending，需重新确认
```

### 停用经验

```python
# 停用不再适用的经验
memory = update(app, profile_id, memory_id, {
    'version': 2,
    'action': 'disable'
})
# status: accepted → disabled
```

### 恢复经验

```python
# 恢复停用或删除的经验
memory = update(app, profile_id, memory_id, {
    'version': 3,
    'action': 'restore'
})
# status: disabled/deleted → pending
```

### 删除经验

```python
# 永久删除（仅历史可见）
memory = update(app, profile_id, memory_id, {
    'version': 2,
    'action': 'delete'
})
# status: * → deleted
```

### 列出所有经验

```python
from studio.memories import listing

memories = listing(app, profile_id)
# 返回所有状态的经验，按更新时间倒序
```

### 召回适用经验

```python
from studio.memories import recall

# 召回适用于当前场景的经验
memories = recall(app, profile_id, 
                  query='写一篇产品评测文章',
                  platform='xiaohongshu')

# 返回：
# - status='accepted' 的经验
# - platform 为空或匹配的经验
# - keywords 为空或在 query 中出现的经验
# - 最多 30 条，总长度 < 12000 字
```

**召回逻辑**：
```python
for memory in all_memories:
    # 1. 必须已确认
    if memory.status != 'accepted':
        continue
    
    # 2. 平台匹配
    if memory.platform and memory.platform != platform:
        continue
    
    # 3. 关键词匹配
    if memory.keywords:
        terms = memory.keywords.split(',')
        if not any(term in query for term in terms):
            continue
    
    # 通过所有过滤
    selected.append(memory)
```

### 生成画像快照

```python
from studio.profiles import snapshot

# 生成包含适用经验的快照
profile_snapshot = snapshot(app, profile_id,
                           query='写一篇产品评测文章',
                           platform='xiaohongshu')

# 快照包含：
# - 画像基本信息
# - recalled_memories: 召回的经验列表
# - memory_scope: 召回参数
# - memory_signature: 经验签名（检测变化用）
```

### 查看变更历史

```python
from studio.memories import revisions

history = revisions(app, profile_id, memory_id)

# 返回所有变更记录
# [
#   {
#     'action': 'propose',
#     'at': '2026-10-13T10:00:00Z',
#     'snapshot': {...}
#   },
#   {
#     'action': 'confirm',
#     'at': '2026-10-13T11:00:00Z',
#     'snapshot': {...}
#   },
#   ...
# ]
```

---

## 数据库结构

### profile_memories 表

```sql
CREATE TABLE profile_memories (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(id),
    content TEXT NOT NULL,              -- 经验内容（最多 1200 字）
    platform TEXT NOT NULL DEFAULT '',  -- 适用平台（空 = 通用）
    keywords TEXT NOT NULL DEFAULT '',  -- 适用主题（逗号分隔，最多 120 字）
    source TEXT NOT NULL,               -- 来源信息（JSON）
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/accepted/disabled/deleted
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_memories_profile 
    ON profile_memories(profile_id, status);
```

### memory_revisions 表

```sql
CREATE TABLE memory_revisions (
    memory_id TEXT NOT NULL REFERENCES profile_memories(id),
    version INTEGER NOT NULL,
    action TEXT NOT NULL,      -- propose/confirm/edit/disable/restore/delete
    snapshot TEXT NOT NULL,    -- 完整快照（JSON）
    at TEXT NOT NULL,
    PRIMARY KEY(memory_id, version)
);
```

### source 字段结构

```json
{
    "type": "post",           // post/conversation/feedback
    "id": "abc123...",        // 来源 ID
    "version": 5,             // 来源版本（post 类型）
    "title": "文章标题",
    "text": "正文摘录...",    // 最多 16000 字
    "quote": "原句引用",      // 必须在 text 中
    "at": "2026-10-13T...",
    "truncated": false        // text 是否被截断（post 类型）
}
```

---

## 使用场景

### 场景 1：从成功文章提炼经验

```python
# 1. 用户发现某篇文章效果很好
post_id = 'abc123...'

# 2. 使用 AI 自动提炼经验
result = extract(app, profile_id, {
    'source_type': 'post',
    'source_id': post_id,
    'source_version': post['version'],
    'platform': 'xiaohongshu'
})

# 3. 系统生成 2-5 条候选经验
# result['memories'] = [
#   {"content": "...", "quote": "...", "status": "pending"},
#   ...
# ]

# 4. 用户在界面上逐条确认或编辑
for memory in result['memories']:
    if user_confirms(memory):
        update(app, profile_id, memory['id'], {
            'version': memory['version'],
            'action': 'confirm'
        })
```

### 场景 2：手动记录反馈

```python
# 用户收到读者反馈，手动记录
memory = propose(app, profile_id, {
    'content': '技术文章要在开头列出前置知识，避免新手读者迷茫',
    'quote': '读者留言说看不懂，因为不知道需要什么基础',
    'platform': '',
    'keywords': '技术文章',
    'source_type': 'feedback',
    'source_title': '读者反馈 - 2026-10-13',
    'source_text': '读者留言说看不懂，因为不知道需要什么基础。建议开头列出需要的前置知识。'
})

# 立即确认
update(app, profile_id, memory['id'], {
    'version': 1,
    'action': 'confirm'
})
```

### 场景 3：创作时应用经验

```python
# 用户开始创作
query = '写一篇产品评测文章：iPhone 16 Pro Max 开箱'
platform = 'xiaohongshu'

# 1. 生成画像快照（自动召回经验）
profile_snapshot = snapshot(app, profile_id, query, platform)

# 2. 快照中包含适用经验
# profile_snapshot['recalled_memories'] = [
#   {
#     "content": "产品评测要在开头说明测试环境",
#     "platform": "xiaohongshu",
#     "keywords": "产品评测,开箱",
#     "source": {"type": "post", "title": "...", "quote": "..."}
#   },
#   ...
# ]

# 3. 将快照传给生成接口
result = app.workflows.generate_outline({
    'post_id': post_id,
    'version': post_version,
    'instruction': '开箱评测',
    'reference_ids': [],
    'profile_snapshot': profile_snapshot  # 包含经验
})

# 4. 生成时自动应用经验
```

### 场景 4：定期整理经验

```python
# 列出所有经验
memories = listing(app, profile_id)

# 按状态分类
pending = [m for m in memories if m['status'] == 'pending']
accepted = [m for m in memories if m['status'] == 'accepted']
disabled = [m for m in memories if m['status'] == 'disabled']

# 停用过时经验
for memory in accepted:
    if is_outdated(memory):
        update(app, profile_id, memory['id'], {
            'version': memory['version'],
            'action': 'disable'
        })

# 确认有价值的候选
for memory in pending:
    if user_confirms(memory):
        update(app, profile_id, memory['id'], {
            'version': memory['version'],
            'action': 'confirm'
        })
```

---

## 最佳实践

### 1. 写好经验内容

✅ **好的经验**：
- 具体明确：「产品评测要在开头说明测试环境和设备型号」
- 有依据：来源于真实文章或读者反馈
- 可执行：清楚告诉 AI 要做什么

❌ **不好的经验**：
- 模糊笼统：「写得好一点」
- 无依据：「我觉得应该这样」
- 不可执行：「注意质量」

### 2. 合理使用范围

**platform**：
- 平台特有规则才指定平台
- 通用规则留空
- 例如：「小红书标题不超过 20 字」→ `platform='xiaohongshu'`
- 例如：「标题不用震惊体」→ `platform=''`

**keywords**：
- 主题相关规则才填关键词
- 通用规则留空
- 关键词用逗号分隔
- 例如：「产品评测要说明测试环境」→ `keywords='产品评测,开箱'`
- 例如：「段落不超过 3 行」→ `keywords=''`

### 3. 及时确认候选

- AI 提炼的候选需要人工确认
- 确认前不会在创作时生效
- 定期检查 `pending` 状态的经验

### 4. 定期整理

- 停用不再适用的经验（平台规则变化、策略调整等）
- 删除重复或错误的经验
- 合并相似的经验

### 5. 追溯来源

- 每条经验都有 `source` 和 `quote`
- 怀疑经验时可以追溯原文
- 使用 `revisions()` 查看变更历史

---

## 常见问题

### Q1: 经验太多会影响性能吗？

**A**: 不会。召回有限额保护：
- 最多 30 条
- 总长度 < 12000 字
- 超过限额会报错，提示用户整理

### Q2: 如何避免经验冲突？

**A**: 
1. 使用范围（platform + keywords）减少冲突
2. 定期检查 `accepted` 经验，停用冲突的
3. AI 生成时如果发现冲突会提示

### Q3: 可以批量导入经验吗？

**A**: 当前不支持。建议：
1. 手动逐条添加（`propose` + `update`）
2. 或从文章/对话用 `extract` 自动提炼

### Q4: 经验会自动过期吗？

**A**: 不会。需要手动停用或删除。
建议定期（如每季度）回顾一次。

### Q5: 删除的经验可以恢复吗？

**A**: 可以。使用 `restore` 操作，状态变回 `pending`。
但需要重新确认才能生效。

### Q6: 如何知道哪些经验被应用了？

**A**: 
1. 查看生成时的 `profile_snapshot['recalled_memories']`
2. AI 生成的内容会体现经验规则
3. 可以通过 A/B 测试验证效果

---

## 安全保障

### 1. 来源验证

- 系统自己解析来源，不接受模型提供的来源
- 文章来源验证：ID、版本、归属
- 对话来源验证：ID、归属

### 2. 引用验证

- `quote` 必须逐字出现在 `source.text` 中
- 最多 1200 字
- 防止虚构经验

### 3. 版本冲突检测

- 更新经验时检查版本号
- 版本不匹配返回 409 错误
- 提示用户刷新后重试

### 4. 权限控制

- 经验只属于对应画像
- 不能跨画像访问经验
- 所有操作验证 `profile_id`

---

## 性能优化

### 1. 索引

```sql
CREATE INDEX idx_memories_profile 
    ON profile_memories(profile_id, status);
```

查询 `profile_id + status` 时使用索引。

### 2. 召回限额

- 最多 30 条
- 总长度 < 12000 字
- 防止过载

### 3. 文本截断

- 文章来源最多保留 16000 字
- 超过部分截断，标记 `truncated: true`

---

## 变更日志

- **1.0.0** (2026-10-13): 初始文档
  - 基于现有实现编写
  - 覆盖所有核心功能
  - 补充使用示例和最佳实践

---

**维护者**: 开发团队  
**最后更新**: 2026-10-13
