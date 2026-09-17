# YouTube 说明

YouTube Nova 不依赖第三方解析站。媒体流统一由 yt-dlp 解析，官方 oEmbed 与 Innertube 只承担轻量信息和评论：

```text
元数据   oEmbed + Innertube TVHTML5_SIMPLY 并发
         -> 缺字段时读取 watch 页
媒体流   yt-dlp + yt-dlp-ejs + JS 运行时
增强     Innertube web -> 头像 / 播放量 / 点赞 / 评论 / 热评
汇合     yt-dlp info 只补仍为空的元数据
下载     Range 下载；DASH 由 ffmpeg 合并；超限时可压缩
```

这套分工避免重复维护容易返回半截 403 地址的 Innertube 选流实现。任何一层失败都只减少可发送内容；只要还能取得标题或封面，就会退化为信息卡片并把原因写入后台日志。

支持 `youtube.com/watch?v=`、`youtu.be/`、`/shorts/`、`/live/`、`/embed/`、`/v/`、`music.youtube.com` 和 attribution link。

## 运行依赖

插件 Python 依赖已经包含：

- `yt-dlp >= 2026.8.19`
- `yt-dlp-ejs >= 0.8.0`

服务器还需要：

- `ffmpeg`：合并 DASH 音视频、压缩超限视频；
- 一个 JS 运行时：Deno 2.3+、Node.js 22+、Bun 1.2.11+ 或 QuickJS。

插件会自动探测运行时并显式传给 yt-dlp。只安装 yt-dlp 而没有 `yt-dlp-ejs` 或 JS 运行时，通常无法处理当前 YouTube 的播放器挑战。

## 主要配置

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| 画质上限 | 1080 | 在上限内选择质量最高的可用格式 |
| 允许 DASH 分离流 | 开 | 允许选择独立视频轨和音轨；需要 ffmpeg |
| 元数据与增强时间预算 | 45 秒 | 限制 oEmbed、Innertube 和评论请求的累计等待 |
| yt-dlp 取流超时 | 60 秒 | 限制播放器 JS 与格式解析；它和元数据请求并发运行 |
| 登录凭据来源 | 手动填写 | 可切换为浏览器 Profile |
| 登录态体检间隔 | 6 小时 | 定期同步、续活并验证登录状态；0 表示关闭后台维护 |
| PO Token 取用策略 | 自动 | 自动 / 总是 / 从不 |
| 可发送视频体积上限 | 48 MiB | 选流预算，下载后还会复检并按配置压缩 |

热评开关位于“消息输出 -> 附加内容：热评 -> YouTube”。

yt-dlp 优先选择预算内的 DASH，其次是带音频的单文件流，最后才是纯视频轨。格式大小优先读取 `filesize` / `filesize_approx`，缺失时按码率和时长估算。所有候选都超出预算时会选择最小格式，再交给发送前压缩流程处理。

## 浏览器 Profile

有持久磁盘和图形桌面的服务器优先使用此模式：

1. 用运行 AstrBot 的同一系统用户启动 Chromium/Chrome，并登录 YouTube 小号。
2. 在插件配置中选择“登录凭据来源 -> 浏览器 Profile”。
3. 浏览器类型必须与实际浏览器一致。
4. Profile 建议填写完整目录，例如 `/root/.config/chromium/Default`。
5. 有桌面时将“自动启动浏览器续活”设为“有头（推荐）”。
6. Linux 密钥环通常保持 `auto`；Chromium 使用 `--password-store=basic` 时可选 `BASICTEXT`。

插件会限频读取 Profile 快照；到体检周期后短暂访问 YouTube、关闭自己启动的浏览器，再同步和验证。已有浏览器进程不会被插件误关。有头模式通常比无头模式更接近日常浏览器指纹，但仍不能替代账号重新登录或改善机房出口 IP 的信誉。

浏览器模式下，Innertube 使用同步后的 Cookie，yt-dlp 直接使用同一个 Profile 的 `cookiesfrombrowser`。登录态被服务端判死后，两边都会暂时退回匿名，避免失效 Cookie 破坏 yt-dlp 自带的匿名客户端链；后续体检确认恢复后会自动重新启用。

### DISPLAY

有头续活需要可用的 X11 `DISPLAY`。留空时插件先读取 AstrBot 进程环境，再尝试从 `/tmp/.X11-unix` 发现显示号；也可明确填写 `:10.0`。填写 `10.0` 时会自动规范为 `:10.0`。

## 手动 Cookie

没有可读取的浏览器 Profile 时，可以填写：

- Netscape `cookies.txt`；
- 浏览器扩展导出的 JSON Cookie 数组；
- `a=1; b=2` 请求头格式。

必须包含 `SAPISID` 或 `__Secure-3PAPISID` 才能生成 `SAPISIDHASH`。插件会吸收允许轮换的 `Set-Cookie` 并把运行状态保存在插件数据目录；yt-dlp 每次取流前从当前权威状态生成权限 0600 的临时 Netscape jar，避免复用被 yt-dlp 改写过的旧文件。

Cookie 不是永久凭据。退出账号、修改密码、Google 吊销会话或要求重新验证时，仍需人工重新登录。建议始终使用专用小号，不要把 Cookie 放进仓库、截图、聊天、Issue 或公开日志。

## PO Token provider

PO Token 用于部分 SABR / GVS 媒体请求。它是可选增强，不是登录凭据，也不能改善 IP 信誉。

| 现象 | PO Token 的作用 |
| --- | --- |
| yt-dlp 能取得元数据，但格式被 403 / SABR 限制 | 可能有效 |
| 账号需要重新登录或 Cookie 已被吊销 | 无法替代登录 |
| 机房 IP 频繁触发机器人验证 | 可能增加可用客户端，但住宅出口通常更关键 |

推荐复用 `bgutil-ytdlp-pot-provider`，插件不自行实现 BotGuard。安装 Python 插件：

```bash
pip install -U bgutil-ytdlp-pot-provider
```

provider 的生成脚本或 HTTP 服务按其上游文档部署。脚本模式通常不常驻进程，适合小服务器；HTTP 模式默认监听 `127.0.0.1:4416`，延迟更低但会常驻。

- `youtube.ytdlp_pot_provider` 留空时使用 provider 默认位置；
- 填 `http://` 或 `https://` 地址时使用 HTTP 模式；
- 填目录或脚本路径时使用脚本模式；
- “自动”让 yt-dlp 按需取令牌；
- “总是”每次都生成，通常额外增加 1 到 3 秒；
- “从不”用于排查 provider 自身故障。

日志中的“无 POT 提供方”不是错误。当前视频不需要令牌时，匿名或登录态良好的 yt-dlp 仍可正常取流。

## 代理

“代理设置 -> YouTube”同时控制元数据、yt-dlp 和媒体下载。googlevideo 地址可能绑定取链时的出口 IP，解析走代理而下载直连，或反过来，都可能导致 403。

机房 IP 长期触发 `LOGIN_REQUIRED` 时，住宅/家宽出口通常比频繁更换 Cookie 稳定。代理地址属于本机部署凭据，不应写入仓库。

## 体积、Range 与压缩

yt-dlp 返回的 googlevideo 地址可能拒绝普通整文件 GET，却接受 Range 请求。插件会为视频轨和音轨使用 Range 下载，并限制并发，DASH 下载完成后由 ffmpeg 合并。

两层体积限制各自负责不同阶段：

- “视频大小上限”限制允许下载的最大文件；
- “可发送视频体积上限”限制聊天平台上传预算；
- 开启“超限视频自动压缩”后，下载结果超出发送预算会先转码，再决定是否发送。

LLOneBot/QQ Highway 的实际边界可能随账号、客户端和线路变化。默认 48 MiB 是保守值，不代表 QQ 的固定协议上限。

## 失败与日志

取不到媒体流时，插件仍会尝试发送封面、标题、作者、时长、统计和热评。常见原因包括机器人验证、地区/年龄/会员限制、私享视频、直播状态、依赖缺失或取流超时。

关键日志：

- `yt-dlp 取流就绪`：环境探测通过；
- `yt-dlp 取流成功`：已经选出流，包含格式、清晰度和耗时；
- `yt-dlp 取流暂不可用`：缺少依赖，日志会给出安装建议；
- `未取到可下载视频流`：退化为封面卡片，并列出登录态、代理和失败摘要；
- `Cookie 维护`：浏览器同步、服务端验证和轮换状态。

日志不会输出 Cookie 值，也不会输出完整的临时签名 URL。
