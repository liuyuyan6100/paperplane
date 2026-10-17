# 运行与部署

本轮验证环境与未实测项目见 [核验表](VERIFICATION.md)。下面所有命令均从仓库根目录执行。模型与平台账号在自己的界面中配置，不需要开发者私有服务或 OpenClaw 登录。

## 本地 Web（Windows 已实测）

需要 Git、Python 3.13 x64。PowerShell 推荐使用独立环境，无需 activate 或改动全局脚本执行策略：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --data-dir ./data --open
```

访问 `http://127.0.0.1:8787`。自动开页失败时手动打开该地址；端口被占用可加 `--port 8788`。终端 Ctrl+C 停止服务，关闭浏览器不会停止后台进程。不要同时用两个服务打开同一数据目录。

macOS/Linux 的原生 Python 安装本轮未实测。相应命令为 python3 -m venv .venv、.venv/bin/python -m pip install -r requirements.txt、.venv/bin/python run.py --data-dir ./data --open；不要在这些系统运行 Windows 的 Scripts 路径。Linux 容器实测不等同于所有原生发行版已验通。

## Windows 桌面构建（已实测编译，安装向导未全程实测）

安装 Python 3.13 x64 与 Inno Setup 6。以下命令创建 .venv、安装 requirements-desktop.txt 并编译文件夹及安装包：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_desktop.ps1
```

默认产物为 dist/PaperPlaneStudio/ 和 dist/installer/PaperPlaneStudio-Setup-版本.exe 及其 .sha256。可使用 Get-FileHash 核对 SHA256。没有 Inno Setup 时追加 -SkipInstaller，只产生可运行目录；脚本不会替你安装 Inno Setup。

追加 -OpenInstaller 会打开普通安装向导，由使用者确认。**本轮只编译安装包并运行冻结程序 --smoke-test，没有在干净 Windows VM 中完成安装、旧版升级与卸载，也没有完整验证 WebView2 图形窗口。** 首次桌面运行依赖 Windows WebView2 Runtime；缺失时需先安装 Microsoft 官方运行时。不要把这一部分当作已完成跨系统兼容验收。

安装版默认数据为 %LOCALAPPDATA%/PaperPlaneStudio/local，源码版为仓库 data/；不会自动合并。

## 主 Docker Compose（Linux/amd64 已实测）

需要 Docker Engine 与 Compose v2，或启动中的 Docker Desktop（Linux containers）。compose.yaml 是带 Cloudflare Access 校验的云端方案。先复制示例：

```powershell
Copy-Item .env.cloud.example .env.cloud
```

使用编辑器修改 .env.cloud 的全部占位字段：公网 HTTPS origin、自己 Access 应用的 issuer 与 audience、允许的所有者邮箱。PAPERPLANE_DATA_DIR 设置为专用目录；Windows 使用 D:/... 形式的绝对路径。本次使用 Docker Desktop 的独立本地目录实测。**原生 Linux bind 目录需要容器 uid/gid 10001 可读写；目录所有权配置本轮未在原生 Linux 宿主机验证。** 不要指向其他实例的数据库。

```sh
docker compose --env-file .env.cloud config --quiet
docker compose --env-file .env.cloud up -d --build
docker compose --env-file .env.cloud ps
docker compose --env-file .env.cloud logs --tail 50 studio
```

端口默认只映射到宿主机 127.0.0.1:8790。访问 /healthz 可检查存活；主页面无有效 Access 身份时拒绝请求是预期行为。必须自行配置域名、HTTPS 入口、Cloudflare Access 应用与认证头转发。**本轮验证了镜像构建、服务存活、数据落盘、依赖/资源及未认证拒绝；真实 Cloudflare 登录、域名/TLS、代理入口与 ARM 构建未实测。** 不要删除认证来绕过错误。

停止并移除该项目容器：

```sh
docker compose --env-file .env.cloud down
```

绑定的数据目录会保留；备份和恢复见 [数据说明](DATA_AND_SECURITY.md)。本机/云端文章、素材、模型与平台身份各自保存，切换位置不迁移内容。

## 可选维护组件（不属于普通安装）

compose.knowledge.yaml 依赖飞书授权与独立资料库配置；compose.updates.yaml、init_private_updates.py、publish_desktop_release.py 是自建私有更新服务工具。**这些组件没有在本次文档核验中实际部署，也没有提供开箱即用的账号/配置；普通使用无需运行。** 不要把仓库中存在这些文件理解为公开更新服务或飞书连接已经开通。
