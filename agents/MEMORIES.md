# 画像经验：开发与验证

经验属于一个画像。模型抽取的内容先是 pending 候选，经使用者确认才可能参与后续任务；禁用、忽略、删除与主题/平台不匹配的经验不会进入该次召回。每次操作要求当前 version，避免覆盖他人的后续修改。任务使用启动时的快照，不能将修改文档描述为运行时已生效。

以下示例可从仓库根目录完整运行，使用临时目录和合成反馈，无外部 API 调用：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from studio.service import Studio
from studio import profiles, memories

with TemporaryDirectory(prefix="paperplane-memory-example-") as folder:
    app = Studio(Path(folder))
    try:
        profile = profiles.save(app, {"name": "Documentation demo"})
        item = memories.propose(app, profile["id"], {
            "content": "End articles with the configured blog link.",
            "source_type": "feedback",
            "source_text": "Please end articles with the configured blog link.",
            "quote": "end articles with the configured blog link.",
        })
        assert item["status"] == "pending"
        assert not memories.recall(app, profile["id"])
        item = memories.update(app, profile["id"], item["id"], {
            "action": "confirm", "version": item["version"],
        })
        captured = memories.snapshot(app, profile["id"])
        assert captured["recalled_memories"][0]["id"] == item["id"]
        item = memories.update(app, profile["id"], item["id"], {
            "action": "disable", "version": item["version"],
        })
        assert not memories.recall(app, profile["id"])
        assert memories.changed(app, captured)
        assert len(memories.revisions(app, profile["id"], item["id"])) == 3
        print("memory lifecycle OK")
    finally:
        app.close()
```

来源由应用回读和校验，不能让模型伪造出处：

- feedback：source_text 是反馈原文；quote 必须是原文真实子串。
- post：source_id 为同画像未归档文章的 ID，source_version 为当前整数版本；应用回读正文。它不等同于“已公开发表文章”。
- conversation：source_id 是同画像对话的 **turn ID**，不是 conversation ID。

edit 后重新进入 pending；restore 也回到 pending。状态转换与版本冲突以 studio/memories.py 的校验为准，业务调用不要绕过校验直接写 SQL。

memories.extract 会调用配置的模型，不是上面离线示例的一部分。本次未用真实付费模型验证抽取质量；实际使用应主动触发、核对来源与候选，再确认采用。完整本地回归见 tests/test_memories.py，环境与限制见 [核验表](../docs/VERIFICATION.md)。
