# 开发与反馈

使用问题先查看[新服务器安装指南](docs/installation.md)和 [YouTube 排障说明](docs/youtube.md)。提交 Issue 时提供插件、AstrBot、Python 与协议端版本，以及 `/yt_status` 的输出和脱敏后的日志。公开的视频链接可以帮助复现；不要上传整个配置文件、Cookie、API 密钥或签名媒体地址。

## 本地验证

使用 Python 3.12 或 3.13，在独立虚拟环境中安装依赖：

```bash
python -m venv .venv
# Linux/macOS；Windows 使用 .venv\Scripts\python.exe
.venv/bin/python -m pip install -r requirements.txt 'astrbot>=4.25,<5' 'pytest>=8,<10'
.venv/bin/python -m pytest -q
```

单元测试不要求登录 YouTube、不发送 QQ 消息，也不需要安装可选 PO Token 组件。与网络、运行时和文件投递有关的修改，应按安装指南在目标环境中再验证真实行为，并说明验证范围。

## 配套依赖更新

PO Token 安装工具使用固定的 Python 提供方版本、官方源码版本和源码 SHA-256。更新时同时检查 `requirements-pot.txt`、安装工具常量、上游 `server/package.json` 与编译产物版本；在空目录和独立 Python 环境中验证安装、重复运行、离线检查以及失败清理。不要只更新一端，也不要用跟随上游 main 的下载地址替代固定版本。

提交前更新相关配置说明及 `CHANGELOG.md`，保持 `metadata.yaml` 与文档一致。CI 在 Python 3.12、3.13 执行回归测试。运行产物、安装后的第三方源码、浏览器 Profile 和凭据留在仓库外的插件数据目录中。
