# Changelog

## v1.0.0

- 从 Nova 流媒体解析中拆出完整 YouTube 链路，作为可独立安装的 AstrBot 插件发布。
- 保留 Innertube、yt-dlp、JS challenge、PO Token、Cookie 轮换与失效提醒。
- 保留 DASH 下载合并、体积预算、超限压缩、热评、头像、卡片、翻译与消息发送能力。
- 配置面板仅展示 YouTube 与通用输出设置；默认发送预算调整为 48 MiB。
- 缓存标记、ZIP 工作区与归档文件使用独立的 `youtube_nova` 命名空间，避免和原插件共用缓存目录时互相清理。
- 清理未使用的 B 站扫码依赖，修正独立插件的日志名称与 YouTube 卡片素材 Referer。
- 收窄运行数据忽略规则，确保 `youtube_core/parser/runtime_manager/` 中的 Cookie 与 yt-dlp 源码会进入发布包。
