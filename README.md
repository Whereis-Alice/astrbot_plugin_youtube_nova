# YouTube Nova

面向 AstrBot 的独立 YouTube 媒体解析插件。它从 Nova 流媒体解析中拆出，保留完整的 YouTube 解析、下载、卡片和 Cookie 维护链路，不依赖原插件。

![YouTube 卡片](docs/images/youtube-card.png)

## 功能

- Innertube 与 yt-dlp 双取流，可按环境自动切换
- 支持普通视频、Shorts、直播回放、`youtu.be` 与 YouTube Music 链接
- DASH 音视频合并、画质与发送体积预算、超限自动压缩
- 作者头像、播放量、点赞数、评论数与公开热评
- YouTube、哔哩哔哩、X（推特）三套仿站皮肤及五套通用皮肤，支持深浅色和四种布局
- Netscape / JSON / Cookie Header 三种 Cookie 输入，自动吸收服务端轮换
- yt-dlp JS challenge 与可选 PO Token provider
- 文本/卡片/富媒体输出控制、翻译、聚合、归档和频率限制

## 安装

在 AstrBot 插件市场通过仓库地址安装：

```text
https://github.com/Whereis-Alice/astrbot_plugin_youtube_nova
```

完整取流还需要服务器安装 `ffmpeg`，以及 Node.js 22+、Deno 2.3+、Bun 或 QuickJS 中任意一个 JS 运行时。Python 依赖会随插件安装，其中包含 `yt-dlp` 与 `yt-dlp-ejs`。

## 从 Nova 流媒体解析迁移

先将 Nova 流媒体解析升级到 `v1.16.0` 或更高版本，再安装本插件；旧版 Nova 仍内置 YouTube 解析，同时启用会重复响应。随后把旧配置中的 `youtube`、`proxy`、`download` 和需要的消息/卡片选项填入本插件。

Cookie 属于登录凭据，不要上传到 GitHub、日志或聊天记录。旧插件缓存中的 Cookie 轮换状态不会自动跨插件复制；建议在新插件中重新填写原始导出内容，让它建立自己的运行时状态。

## 关键配置

- “视频流取用来源”建议保持“自动”。机房 IP 连续触发门禁后会暂时直接交给 yt-dlp。
- “可发送视频体积上限”默认 48 MiB，适配 LLOneBot/QQ Highway 的实测边界；其他适配器可自行提高。
- Cookie 不是必填。需要登录态时使用专用小号，并保持浏览器与 Bot 的出口稳定。
- PO Token 只能增强部分媒体流请求，不能替代有效 Cookie 或改善高风险出口 IP。

完整链路、Cookie 维护和 PO Token 配置见 [YouTube 说明](docs/youtube.md)。

## 安全边界

- 下载器拒绝本机、内网、链路本地与保留地址，重定向后会再次校验。
- 代理地址和 Cookie 仅从 AstrBot 插件配置读取，不写入仓库。
- Cookie 运行时文件、媒体缓存和临时归档均被 `.gitignore` 排除。

## 许可

主体遵循 [GNU Affero General Public License v3.0](LICENSE)。`LICENSES/` 目录保留随源码迁移的第三方许可文本。
