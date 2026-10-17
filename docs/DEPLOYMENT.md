# 运行与部署

## 本地 Web

使用 Python 3.13，运行 python run.py --data-dir ./data --open，访问 http://127.0.0.1:8787。AI API、自有公众号与资料库由用户在界面配置；不需要开发者的私有服务或 OpenClaw 登录。

## Windows 桌面

安装 Python 与 Inno Setup 6 后运行 tools/build_desktop.ps1。-SkipInstaller 仅生成可运行目录；-OpenInstaller 打开普通向导。首次构建会建立 .venv 并安装 requirements-desktop.txt。不要把开发虚拟环境、构建目录或个人配置提交到仓库。

## Docker

compose.yaml 是带 Cloudflare Access 校验的云端方案，不是匿名公网服务模板。将 .env.cloud.example 复制为 .env.cloud，配置自己的域名、Access issuer/audience、所有者邮箱和独立数据目录，然后运行：

```sh
docker compose --env-file .env.cloud up -d --build
```

服务端口默认仅映射到宿主机回环地址；需要自行配置 HTTPS 入口、Cloudflare Access 应用及相关认证头转发。未正确配置时不要删除认证校验来绕过错误。云端额外依赖见 requirements-cloud.txt。

本机与云端的数据、模型配置和平台身份各自保存；切换运行位置不会自动迁移文章或素材。此开源发布不提供开发者正在使用的云端实例访问权限。
