# YouTube Nova

面向 AstrBot 的独立 YouTube 媒体解析插件。视频流统一由 yt-dlp 解析，Innertube 负责补充标题、头像、统计、热评与登录态诊断。

![YouTube 卡片](docs/images/youtube-card.png)

## 功能

- 支持普通视频、Shorts、直播回放、YouTube Music 与常见分享链接
- yt-dlp + JS challenge 取流，可选 PO Token provider
- 浏览器 Profile 或手动 Cookie，支持自动同步、轮换、续活和失效提醒
- DASH 音视频合并、可选超限群文件投递与独立可调的视频压缩策略
- 作者头像、播放量、点赞数、评论数和公开热评
- YouTube 仿站皮肤与五套通用皮肤，支持深浅色和四种布局
- 文本、卡片、富媒体、翻译、聚合、归档和频率限制

## 安装

在 AstrBot 插件市场使用仓库地址安装：

```text
https://github.com/Whereis-Alice/astrbot_plugin_youtube_nova
```

完整取流需要服务器安装 `ffmpeg`，以及 Node.js 22+、Deno 2.3+、Bun 或 QuickJS 中任意一个 JS 运行时。Python 依赖会随插件安装，其中包含 `yt-dlp` 与 `yt-dlp-ejs`。

## 配置要点

- 有图形桌面的长期服务器优先使用“浏览器 Profile”登录态，并启用有头浏览器续活。
- “可发送视频体积上限”默认 48 MiB；超限后可选上传 QQ 群文件，压缩则按独立策略执行。
- 代理解析与媒体下载必须走同一出口，否则 googlevideo 直链可能返回 403。
- PO Token 是可选增强，不能替代有效登录态，也不能改善机房 IP 的信誉。
- Cookie、代理和浏览器 Profile 都是本机凭据，不要提交到仓库、Issue 或日志。

完整配置见 [配置说明](docs/configuration.md)，取流、Cookie 与排障见 [YouTube 说明](docs/youtube.md)。

## 许可

主体遵循 [GNU Affero General Public License v3.0](LICENSE)。`LICENSES/` 保留第三方许可文本。
