# 纸飞机应用框架图

生成与核对：2026-10-21，Archify `2.17.0-dev.1`。图反映源码基线`5e900ed7cabee83aeced10e2cf49cea1a16f21aa`的当前应用框架，包含12个已由工具核对的源码引用。框架代码在本轮未改动。

![纸飞机当前应用框架](paperplane-framework.svg)

| 文件 | 用途 |
| --- | --- |
| [SVG](paperplane-framework.svg) | README内嵌，可独立打开；包含明暗主题，未嵌入可执行脚本 |
| [PNG](paperplane-framework.png) | Archify导出的静态图片，可下载分享 |
| [交互HTML](paperplane-framework.html) | 下载后在本机浏览器打开，可查看源码引用、切换主题、搜索与导出；GitHub文件页只展示源码 |
| [JSON源图](paperplane-framework.architecture.json) | 可编辑的Archify规格，包含组件、关系和公开源码来源 |
| [校验摘要](validation.json) | 源图/HTML/导出文件哈希、9项结构检查及浏览器检查范围 |

## 图怎样对应代码

桌面壳加载React工作台；工作台经本机HTTP调用FastAPI，接口委托应用用例和窄接口，再通过兼容适配层复用原业务。文章、修订和任务落在SQLite，图片/视频保存在数据目录。生成类任务由唯一worker执行并调用自选API。

连线展示主依赖关系，不是一次请求的完整时序。桌面壳→前端、接口→应用服务、应用服务→兼容适配层的动作已由节点含义表达，因此没有重复的连线文字。worker是进程内后台执行器，没有在图中冒充独立消息队列服务。

原工作台及平台适配仍作为兼容能力保留；统一数据访问和剩余功能迁入见[Roadmap](../../ROADMAP.md)。本图没有把SQLAlchemy/Alembic、所有平台端到端发布或正式版迁移画成已完成。

## 生成与验证

在已安装Archify CLI的环境中，从仓库根运行：

```sh
archify validate architecture docs/diagrams/paperplane-framework.architecture.json --quality showcase --repo-root . --json
archify deliver architecture docs/diagrams/paperplane-framework.architecture.json docs/diagrams/paperplane-framework.html --quality showcase --repo-root . --json
archify visual-check docs/diagrams/paperplane-framework.html --json
```

本次结构检查为9/9、0错误、0警告。真实Chrome检查1440×900、1600×1000、1920×1080、2048×1320，均无页面溢出；明暗主题截图和导出的静态图已人工视觉核对。原始浏览器截图及含本机路径的完整工具回执留在本机测试证据中，公开仓库仅保留相同产物哈希的摘要。

图稿修正保留记录：初稿显式指定源码后需补充`--repo-root`；随后按诊断补足节点尺寸和连线标签间距。最终还将worker的语义类型改为后端，以免被理解为消息总线服务；重新验证、交付、截图与导出后使用最终哈希。没有修改生成HTML来绕过检查。

修改源图后需要重新执行验证和交付，并重新导出SVG/PNG、更新校验摘要。Archify是文档制图工具，不是纸飞机运行依赖。工具运行时的[MIT许可](licenses/Archify-MIT.txt)和内嵌字体的[OFL许可](licenses/JetBrainsMono-OFL.txt)随图保留。
