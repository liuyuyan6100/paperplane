# 数据与安全

源码版默认 data/；可用 --data-dir 或 PAPERPLANE_DATA_DIR 指定独立目录。Windows 安装版默认 %LOCALAPPDATA%/PaperPlaneStudio/local；两者不自动合并。

数据包含文章、版本、对话、任务和回执的 SQLite 数据库，以及图片、视频与独立凭据配置。Windows 凭据使用当前系统用户 DPAPI 加密，复制到别的电脑或用户不能直接解密；其他系统靠文件权限保护，应自行保护磁盘和备份。

## 完整内容备份与恢复

在设置页使用完整内容备份（ZIP），保存到自己的安全目录。备份包含数据库和支持的本地图片/视频，**不包含 private.json、API Key、平台登录 Cookie 或外部共享库原件**。恢复内容后需要重新配置模型/平台身份；不要误把它当成整台电脑完整备份。

先停止要恢复到的工作台及桌面托盘进程，保留原数据副本。推荐先恢复到一个新的独立目录，避免直接覆盖正式数据。下面命令在仓库根目录执行，路径换成自己的 ZIP 和独立目录：

```sh
python tools/restore_studio.py "C:/backup/paperplane.zip" --data-dir "C:/paperplane-restore"
python run.py --data-dir "C:/paperplane-restore" --open
```

如使用虚拟环境，将 python 换成对应环境的解释器。macOS/Linux 的路径须自行替换；本轮 CLI 恢复在 Windows 合成数据中实测，非原生跨机恢复验收。

工具拒绝超出 200 MB / 5000 条目、校验和不符、重复/不支持路径和不兼容数据库的 ZIP。它仅接受自身完整备份格式，不接受任意文件夹压缩包。目标已有数据库时先保存 before-restore 备份，再恢复；请仍自行保留独立副本。

恢复会暂停/取消原排期与未完成任务，外部送稿结果可能标为 uncertain，**不会自动重放历史发送或模型调用**。打开后核对文章、图片/视频、任务与原平台回执，再决定后续操作。跨机 DPAPI 凭据、真实历史库的所有版本组合以及超大库恢复本轮未实测。

## 配置与隐私

请勿提交 data/、private.json、Cookie、.env、数据库、业务备份、浏览器配置目录、平台真实回执或个人文章。报告问题使用合成数据和脱敏日志。模型请求会把选用上下文交给你配置的接口，先确认资料适合交给该服务。

本地服务默认仅监听 127.0.0.1。远程访问使用 [部署说明](DEPLOYMENT.md) 的认证方案，不要把无认证本地端口直接暴露公网。

若凭据疑似泄露，先撤销/轮换，再处理公开文件；仅从最新文件删除不能清除 Git 历史。此次核验与限制见 [核验表](VERIFICATION.md)。

## 提交前检查公开源码

仓库的 `Public source security` 工作流会在 push、pull request 和手动运行时检查公开文件与完整可达历史。Gitleaks 固定为 8.24.3，Linux CI 下载官方发布包并验证固定 SHA256；使用内置规则、最多三层解码、脱敏输出，不接受 `gitleaks:allow` 注释或 `.gitleaksignore` 绕过。仅原脱敏测试文件中的两个精确合成值对 `generic-api-key` 规则例外；其他测试、凭据类型和值仍参与扫描。

先从 [Gitleaks 官方版本页](https://github.com/gitleaks/gitleaks/releases/tag/v8.24.3) 安装对应系统的 8.24.3，并用同版本官方 checksums 验证下载。然后在完整 Git 仓库根目录运行：

```sh
python tools/check_public_source.py
python tools/check_public_source.py --all-history
gitleaks git . --config .gitleaks.toml --log-opts="--all --full-history" --redact --ignore-gitleaks-allow --max-decode-depth 3
python -m unittest discover -s tests -p test_public_source_security.py -v
```

隐私检查默认读取 Git 已跟踪文件的工作区内容；新增文件须先加入暂存区，或用 `--directory` 检查待导出的完整目录。它拒绝个人用户目录、所有者服务域名、运行数据库、凭据文件及浏览器登录状态等，保留通用示例和公开品牌。`.env*.example` 可提交，但其中的内容仍接受密钥检查。历史模式要求完整克隆，会检查所有可达提交中的文件版本及文件路径；浅克隆须先补齐历史。诊断仅显示文件路径、行号、类别和历史提交，不输出匹配到的私人值。

```sh
python tools/check_public_source.py --directory ./public-export
gitleaks dir ./public-export --config .gitleaks.toml --redact --ignore-gitleaks-allow --max-decode-depth 3
```

源码导出工具也调用目录隐私检查，发现问题时拒绝成功交付。CI 还用隔离的合成凭据与个人目录验证检查确实会失败，防止误把“扫描器失效”当作没有泄露。规则无法识别所有私人文章、图片或业务内容，发布前仍需人工检查；仓库扫描也不包含旧 Actions 日志、附件、fork 和他人已下载的副本，必要时须分别处理。
