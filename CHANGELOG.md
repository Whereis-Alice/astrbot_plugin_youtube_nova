# Changelog

## v1.1.1

- 新增 Innertube 直链尾段预检；遇到只能读取前半段、尾段返回 403 的伪可用流时，自动切换现有 yt-dlp 兜底，不再出现“解析成功、下载到一半失败”。
- YouTube 直链显式使用 Range 下载，修复部分 Googlevideo 地址拒绝整文件 GET 时的 403。
- 将 Range 下载并发收敛为四路，并支持不足一个分片的小音轨，避免高并发或小文件错误回退造成 403。
- 自动把 WebUI 中填写的 `DISPLAY=10.0` 规范为 X11 所需的 `:10.0`，修复有头 Chromium 退出码 1。
- 下载日志统一隐藏媒体直链的签名查询串，避免超长 Googlevideo URL 刷屏或暴露临时访问凭据。

## v1.1.0

- 新增浏览器 Profile 登录态来源，Innertube 与 yt-dlp 直接共用浏览器的最新 Cookie，不再依赖反复手工导出。
- 新增有头/无头浏览器自动续活；按体检周期访问 YouTube 后关闭插件启动的进程，已有桌面浏览器不会被误关。
- 浏览器凭据读取失败或被判失效时自动退回匿名 yt-dlp 链，避免死 Cookie 降低公开内容解析成功率。
- 登录态提醒会按浏览器或手动 Cookie 模式给出对应恢复步骤，日志仍不记录任何 Cookie 值。

## v1.0.0

- 从 Nova 流媒体解析中拆出完整 YouTube 链路，作为可独立安装的 AstrBot 插件发布。
- 保留 Innertube、yt-dlp、JS challenge、PO Token、Cookie 轮换与失效提醒。
- 保留 DASH 下载合并、体积预算、超限压缩、热评、头像、卡片、翻译与消息发送能力。
- 配置面板仅展示 YouTube 与通用输出设置；默认发送预算调整为 48 MiB。
- 缓存标记、ZIP 工作区与归档文件使用独立的 `youtube_nova` 命名空间，避免和原插件共用缓存目录时互相清理。
- 清理未使用的 B 站扫码依赖，修正独立插件的日志名称与 YouTube 卡片素材 Referer。
- 收窄运行数据忽略规则，确保 `youtube_core/parser/runtime_manager/` 中的 Cookie 与 yt-dlp 源码会进入发布包。
