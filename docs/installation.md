# 安装与首次验证

本文面向新服务器。插件安装、系统依赖、可选的 PO Token 和账号配置分别完成；安装插件不会自动改动系统软件、读取浏览器账号或安装第三方令牌服务。

## 运行要求

| 项目 | 要求 | 谁负责安装 |
| --- | --- | --- |
| AstrBot | `>=4.25.0,<5` | 管理员；先完成 AstrBot 自身部署 |
| Python | 新部署使用 3.12 或 3.13，两者由本仓库 CI 验证 | AstrBot 的运行环境 |
| Python 依赖 | 以仓库 `requirements.txt` 为准，包括 aiohttp、Pillow、yt-dlp、yt-dlp-ejs | AstrBot 插件安装流程 |
| FFmpeg / ffprobe | 同一套 FFmpeg 发行包，进程可执行 | 管理员；DASH 合并及媒体处理需要 |
| JS 运行时 | Node.js 22+、Deno 2.3+、Bun 1.2.11+ 或兼容的 QuickJS；由 yt-dlp 判断实际支持版本 | 管理员；推荐 Node.js 24 LTS |
| bgutil PO Token | 可选；本仓库安装工具配套使用 Python 提供方 2.0.2 与官方脚本 2.0.2 | 需要时显式执行安装工具；脚本模式还需 Node.js 与 npm |
| Cookie / 浏览器 / 代理 | 可选，按内容限制与出口网络配置 | 安装者，不随插件分发 |

AstrBot 需要能访问 YouTube、Googlevideo 和缩略图 CDN；安装阶段还需能访问 Python 包索引，安装 PO Token 时需访问 GitHub 和 npm。PO Token 不能保证通过机器人验证、地区或账号限制。

## 确定 AstrBot 使用的 Python

以下命令中的 Python 必须是 **启动 AstrBot 的那个解释器**。不要只在宿主机或另一个虚拟环境执行 `pip install`。

- 使用 `.venv` 启动时，Linux 常见为 `<AstrBot目录>/.venv/bin/python`，Windows 常见为 `<AstrBot目录>\.venv\Scripts\python.exe`。
- 使用系统 Python 启动时，使用同一个 `python` 的完整路径。
- Docker 中先执行 `docker exec astrbot python -c "import sys; print(sys.executable)"`；若部署使用虚拟环境，请将 `python` 替换为启动命令中的解释器。`astrbot` 是示例容器名。
- 服务部署可从 systemd 的 `ExecStart`、容器的启动配置或自己的启动脚本确认。命令行安装成功不代表后台服务继承了该终端的 PATH。

## Linux 安装

以下以已部署 AstrBot 的 Debian / Ubuntu 为例，`/opt/AstrBot` 是示例路径，请替换为实际目录。

安装 FFmpeg：

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg ca-certificates
```

已有受支持的 Node.js 和 npm 时跳过这一步。新 Debian / Ubuntu 服务器可通过 NodeSource 的 Node 24 软件源安装（包含 npm）：

```bash
sudo apt-get install -y curl gnupg
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
  | sudo gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
printf '%s\n' "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" \
  | sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
sudo apt-get update
sudo apt-get install -y nodejs
```

也可通过 [Node.js 官方下载页](https://nodejs.org/en/download)安装适合自己发行版与架构的 Node.js。系统原有软件源提供的 `nodejs` 可能较旧，安装后务必检查；基础取流也可以使用表中的其他运行时。可选 PO Token 的脚本模式使用 Node.js，不能用 Deno 代替。

```bash
node --version
npm --version
ffmpeg -version
ffprobe -version
```

将解释器与插件目录保存为本次终端变量，确认解释器：

```bash
ASTRBOT_DIR=/opt/AstrBot
ASTRBOT_PYTHON="$ASTRBOT_DIR/.venv/bin/python"
PLUGIN_DIR="$ASTRBOT_DIR/data/plugins/astrbot_plugin_youtube_nova"
"$ASTRBOT_PYTHON" -c 'import sys; print(sys.executable); print(sys.version)'
```

接着在 AstrBot 管理面板的插件市场选择从仓库安装：

```text
https://github.com/Whereis-Alice/astrbot_plugin_youtube_nova
```

安装流程会处理 `requirements.txt`。只有面板报告依赖安装失败或需要手动修复时，再使用同一解释器执行：

```bash
"$ASTRBOT_PYTHON" -m pip install -r "$PLUGIN_DIR/requirements.txt"
```

如果该虚拟环境由 uv 管理且没有 pip，不要切换到系统 pip；使用已有的 uv 明确指定目标解释器：

```bash
uv pip install --python "$ASTRBOT_PYTHON" -r "$PLUGIN_DIR/requirements.txt"
```

安装系统软件或改变服务 PATH 后，完整重启 AstrBot 服务，再进行[首次验证](#首次验证)。

## Windows 安装

在 PowerShell 中，通过系统软件管理器或官方下载包准备 Node.js 24 LTS、FFmpeg。例如使用已有的 winget：

```powershell
winget install --id OpenJS.NodeJS.LTS --exact
winget install --id Gyan.FFmpeg --exact
```

winget 的 LTS 项会安装当时可用的 LTS 版本；如需固定 Node.js 24，请从官方归档选择该版本。安装后检查实际版本是否受 yt-dlp 和 PO Token 上游支持。

重新打开启动 AstrBot 的终端；如以 Windows 服务运行，还需重启该服务，让进程获得新的 PATH。确认命令都能运行：

```powershell
node --version
npm --version
ffmpeg -version
ffprobe -version
```

设置实际路径；以下 `D:\Apps\AstrBot` 仅为示例：

```powershell
$AstrBotDir = 'D:\Apps\AstrBot'
$AstrBotPython = Join-Path $AstrBotDir '.venv\Scripts\python.exe'
$PluginDir = Join-Path $AstrBotDir 'data\plugins\astrbot_plugin_youtube_nova'
& $AstrBotPython -c 'import sys; print(sys.executable); print(sys.version)'
```

在 AstrBot 管理面板使用上述仓库地址安装插件。只有自动安装依赖失败时才执行：

```powershell
& $AstrBotPython -m pip install -r (Join-Path $PluginDir 'requirements.txt')
```

没有 pip 的 uv 环境使用：

```powershell
uv pip install --python $AstrBotPython -r (Join-Path $PluginDir 'requirements.txt')
```

完整重启 AstrBot，然后进行首次验证。

## Docker 安装

依赖必须存在于 **运行 AstrBot 的容器**。只在宿主机安装 FFmpeg / Node.js，容器不会自动获得它们。不要把 `docker exec ... apt install` 作为长期部署方式；重建容器会丢失这类改动。

仓库提供基于官方 Debian 系 AstrBot 镜像的[扩展示例 Dockerfile](../examples/docker/Dockerfile)和 [Compose 示例](../examples/docker/compose.yaml)。它们把 FFmpeg、ffprobe、Node.js 与 npm 放入镜像，不预置 Cookie 或 PO Token 脚本。

下载本仓库作为构建上下文，首次部署可执行：

```bash
git clone https://github.com/Whereis-Alice/astrbot_plugin_youtube_nova.git
cd astrbot_plugin_youtube_nova
docker compose -f examples/docker/compose.yaml up -d --build
```

首次启动后通过 AstrBot 面板安装插件。Compose 中的 `./data` 按 Compose 文件所在目录解析，也就是 `examples/docker/data`；这是持久化数据卷。已有部署请把原数据卷、协议端端口、网络和其他配置合并到自己的 Compose，保持原挂载路径，不要直接启动一套空数据替代现有服务。

示例默认使用官方 `soulter/astrbot:latest` 和 Node 24 镜像。生产部署可通过 `ASTRBOT_IMAGE`、`NODE_IMAGE` 指定已验证的版本标签或镜像 digest，保留原镜像以便回退，例如：

```bash
ASTRBOT_IMAGE='soulter/astrbot:latest' NODE_IMAGE='node:24-bookworm-slim' \
  docker compose -f examples/docker/compose.yaml build
```

如果使用非 Debian 镜像，请按镜像的发行版安装同等依赖；示例 Dockerfile 不适用于 Alpine。默认官方镜像使用 `/AstrBot` 和 `python`，定制镜像应按自己的实际布局调整。

在容器内检查，而不是在宿主机检查：

```bash
docker exec astrbot python -c 'import sys; print(sys.executable)'
docker exec astrbot node --version
docker exec astrbot npm --version
docker exec astrbot ffmpeg -version
docker exec astrbot ffprobe -version
```

如需 PO Token，先通过面板安装插件，再在容器内执行下节工具。把 `--data-dir` 放在持久化的 `/AstrBot/data/plugin_data/astrbot_plugin_youtube_nova`，并填写工具输出的 **容器内路径**。容器重建后 Python 包可能随基础镜像恢复，需要重新执行工具或把 `requirements-pot.txt` 安装步骤加入自己的镜像；持久数据中的脚本仍可复用。

## 首次验证

1. 在 AstrBot 设置中确认自己的账号是管理员。
2. 插件先保持默认配置：登录凭据来源为“手动填写”，Cookie 留空；不启用浏览器自动续活；PO Token 策略为“自动”。按网络需要配置插件的 YouTube 代理。
3. 发送 `/yt_status`（别名 `/油管状态`），确认取流依赖、JS 运行时、FFmpeg、ffprobe 与媒体缓存可用。依赖安装后重载插件以刷新检查结果；PATH 改变后完整重启服务或容器。
4. 给机器人发送自己确认可以公开访问的短视频链接，等待下载并检查声音。日志中“yt-dlp 取流成功”只表示选流及采样通过，最终还需看到媒体下载完成并在会话中收到视频。

自检命令只检查本机环境，不验证登录态、不访问 YouTube、不保证账号或出口可用。首次验证无需先准备浏览器、Cookie 或 PO Token；遇到具体限制时再增加这些配置。

## 可选部署 PO Token

当基础环境齐全，仍出现“能取得元数据，媒体中后段 403 / SABR 受限”时，可安装 [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)。它是独立第三方组件，不是账号登录凭据。

本仓库的[安装工具](../tools/setup_pot_provider.py)固定配套版本 **2.0.2 + 2.0.2**，校验官方源码压缩包，按锁文件安装 npm 依赖并编译。工具需要 Node.js 22+、npm 9+（推荐 Node.js 24 LTS），在当前 AstrBot Python 环境安装 Python 提供方，不自动安装系统软件、不修改面板配置、不覆盖未知安装目录。网络下载和编译会执行可信上游的 npm 安装脚本，请在维护时间主动运行。

Linux，在前面的路径变量已设置后：

```bash
POT_DATA_DIR="$ASTRBOT_DIR/data/plugin_data/astrbot_plugin_youtube_nova"
"$ASTRBOT_PYTHON" "$PLUGIN_DIR/tools/setup_pot_provider.py" --data-dir "$POT_DATA_DIR"
"$ASTRBOT_PYTHON" "$PLUGIN_DIR/tools/setup_pot_provider.py" --check --data-dir "$POT_DATA_DIR"
```

Windows，在前面的路径变量已设置后：

```powershell
$PotDataDir = Join-Path $AstrBotDir 'data\plugin_data\astrbot_plugin_youtube_nova'
& $AstrBotPython (Join-Path $PluginDir 'tools\setup_pot_provider.py') --data-dir $PotDataDir
& $AstrBotPython (Join-Path $PluginDir 'tools\setup_pot_provider.py') --check --data-dir $PotDataDir
```

Docker（官方布局示例）：

```bash
docker exec astrbot python /AstrBot/data/plugins/astrbot_plugin_youtube_nova/tools/setup_pot_provider.py \
  --data-dir /AstrBot/data/plugin_data/astrbot_plugin_youtube_nova
docker exec astrbot python /AstrBot/data/plugins/astrbot_plugin_youtube_nova/tools/setup_pot_provider.py \
  --check --data-dir /AstrBot/data/plugin_data/astrbot_plugin_youtube_nova
```

按工具输出，在插件面板中填写：

| 配置 | 值 |
| --- | --- |
| YouTube → PO Token 提供方地址 | 工具输出的 `server` 绝对目录，在 AstrBot 运行环境内可读 |
| YouTube → PO Token 取用策略 | 为修复分片 403 安装时，按工具输出选“总是”；仅预装组件时可保持“自动” |

保存配置后完整重启 AstrBot，再执行 `/yt_status` 并重新测试短视频。Python 插件导入/发现状态可能已缓存，安装新的提供方后完整重启比只重载当前插件更稳妥。`--check` 是离线组件检查，不证明令牌能在当前出口生成成功。

如果工具日志显示 `canvas` 预编译包不可用并转入本地编译，Debian / Ubuntu 可补充构建依赖后重试：

```bash
sudo apt-get install -y build-essential pkg-config python3 libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev
```

容器中应把这些依赖加入自定义镜像后重建；Windows 如遇同类问题，按 [node-canvas 上游安装说明](https://github.com/Automattic/node-canvas#installation)准备本机编译工具，或采用受支持的平台与 Node 版本。失败的安装不会成为可用的配套目录，先查看工具输出的本地日志再重试。

手动部署者可用同一 AstrBot 解释器安装 [requirements-pot.txt](../requirements-pot.txt)，并按[上游文档](https://github.com/Brainicism/bgutil-ytdlp-pot-provider#readme)部署匹配的脚本或 HTTP 服务。仅安装 Python 包还不够；提供方 2.x 不能搭配 1.x 脚本。HTTP 服务必须从 AstrBot 环境可达，跨容器时 `127.0.0.1` 指向 AstrBot 自己，应使用服务容器名或实际地址。无需向公网开放令牌服务。

插件升级不会自动升级 PO Token 组件。将来的主版本升级应把 Python 提供方与脚本/服务一起更换，并保留旧版本目录和配置。

### 使用预构建 HTTP 服务

Windows 缺少 canvas 本机构建工具，或希望多个请求共用常驻服务时，可改用上游预构建镜像，无需在 AstrBot 主机编译生成脚本。仍需在 **AstrBot 的 Python 环境**安装 `requirements-pot.txt`，不要同时执行脚本安装工具：

```bash
"$ASTRBOT_PYTHON" -m pip install -r "$PLUGIN_DIR/requirements-pot.txt"
# 没有 pip 时使用已有的 uv：
uv pip install --python "$ASTRBOT_PYTHON" -r "$PLUGIN_DIR/requirements-pot.txt"
```

PowerShell 对应命令：

```powershell
& $AstrBotPython -m pip install -r (Join-Path $PluginDir 'requirements-pot.txt')
# 没有 pip 时：
uv pip install --python $AstrBotPython -r (Join-Path $PluginDir 'requirements-pot.txt')
```

AstrBot 直接运行在同一台宿主机时，可使用下面这条 Docker 命令启动固定配套版本，只把端口发布到本机回环地址：

```bash
docker run -d --init --name youtube-nova-pot --restart unless-stopped -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider:2.0.2
```

将插件提供方地址填为 `http://127.0.0.1:4416`。若 AstrBot 也运行在 Docker 中，使用仓库的[可选服务叠加文件](../examples/docker/compose.pot.yaml)：

```bash
docker compose -f examples/docker/compose.yaml -f examples/docker/compose.pot.yaml up -d --build
docker exec astrbot python -m pip install -r /AstrBot/data/plugins/astrbot_plugin_youtube_nova/requirements-pot.txt
```

此时填写 `http://pot-provider:4416`，两服务共享 Compose 网络，不向宿主机发布令牌服务端口。AstrBot 若使用虚拟环境或没有 pip，同样改用它的实际 Python 或 `uv pip --python`。Python 包安装需要在重建 AstrBot 容器后重做，或加入自己的镜像构建步骤；HTTP 服务容器则由 Compose 管理。

保存配置并完整重启 AstrBot；为修复分片 403 时选择“总是”，然后验证真实视频。脚本模式与 HTTP 模式二选一；上游在两者都可用时优先 HTTP。远程 HTTP 服务应仅允许受信任的 AstrBot 环境访问，不要直接暴露到公网。

## Cookie、浏览器与网络出口

匿名基础验证完成后，如视频内容确实要求登录，可在面板填写自己的 Cookie，或选择自己的浏览器 Profile，详见[YouTube 说明](youtube.md#手动-cookie)。浏览器模式要求 AstrBot 运行用户能读取该 Profile 及所需密钥环；有头续活还需图形显示环境。无桌面服务器不需要为基础安装额外部署浏览器。

Cookie、代理口令、浏览器数据库与令牌不属于公共插件内容，不要写进 Compose、镜像、GitHub Issue 或日志。Docker 中要使用容器能访问的代理地址；容器的 `localhost` 不是宿主机。解析、PO Token 相关访问和媒体下载应使用兼容的网络出口，YouTube 直链可能绑定取链时的 IP。

大视频作为文件投递时，协议端必须能读取文件来源。分离部署推荐配置协议端可访问的 `callback_api_base`，例如同一 Docker 网络中的 `http://astrbot:6185`；`localhost` 只适用于双方确实共享该网络命名空间的部署。共享路径和 HTTP 文件服务的具体设置见[配置说明](configuration.md#视频体积压缩与群文件)。

## 按现象排查

| 现象 | 检查与处理 |
| --- | --- |
| `/yt_status` 提示 Python 依赖缺失 | 用 AstrBot 的实际解释器安装 `requirements.txt`；uv 环境用 `uv pip --python`；重载插件 |
| 本机终端能运行 Node / FFmpeg，自检仍缺失 | 在服务用户或容器内检查 PATH；完整重启 AstrBot，让后台进程继承新环境 |
| PO Token 提供方已检测到，但取流仍报脚本失败 | 运行工具 `--check`；核对 Python 包和脚本版本、完整路径、Node/npm 以及目录权限；仅“检测到”不代表可用 |
| 首字节 206，中后段 403 | 配套安装 PO Token 后尝试“总是”；检查解析和下载出口一致；插件会预检并尝试候选回退 |
| `LOGIN_REQUIRED` / 机器人验证 | 核对视频本身的访问条件、账号状态与出口；PO Token 不能替代有效登录，也不能保证解决 IP 信誉限制 |
| 只收到封面 | 阅读卡片提示与后台降级原因；核对体积上限、是否为直播/受限内容、实际依赖与网络 |
| 视频下载成功但群文件/私聊文件失败 | 核对协议端文件接口、权限、共享路径或回调地址；回执未确认时先查看会话，避免重复上传 |

提 Issue 时提供 AstrBot / 插件 / yt-dlp / JS 运行时版本、部署方式、自检文字和去除凭据的故障摘要。不要提交完整签名 URL、Cookie、带口令代理地址或私有视频内容。

## 升级与回退

升级前备份 AstrBot 数据、插件配置和当前插件目录；自行记录 PO Token 目录、配套版本及容器镜像标签。数据和下载缓存放在插件源码之外的持久目录，避免插件更新覆盖。

v1.6.0 将容器中的空白缓存设置统一为 AstrBot 插件数据目录。旧版使用共享缓存的部署，应在升级前显式填写原缓存路径，以继续使用原目录中的 Cookie 运行状态与解析记录；不会自动移动旧数据。新部署保持留空即可，详见[缓存与持久化](configuration.md#媒体缓存与持久化)。

通过面板更新插件后，查看 [CHANGELOG](../CHANGELOG.md)，重新运行 `/yt_status` 并验证一个完整短视频。依赖升级、提供方安装或 PATH 变化后完整重启 AstrBot；仅改插件配置或插件源码时可先尝试热重载。

回退时停止 AstrBot，恢复原插件及对应配置，再启动并验证；如果本次还改动了 Python 提供方或容器镜像，也要恢复对应版本。请保留媒体/运行时数据和原来的 Cookie 配置，插件更新不能替代账号登录维护。

## v1.6.0 验证范围

| 环境 | 已完成验证 |
| --- | --- |
| Linux / Python 3.12 / Node.js 24 | 独立空虚拟环境安装 Python 依赖，空目录安装配套 PO Token，离线检查与重复安装成功；494 项测试和 104 项子测试通过；无 Cookie 完整下载并合并 19 秒公开视频，包含声音 |
| Windows | CLI、Node/npm 检测和失败清理已验证；本机因缺少 canvas 所需 Visual Studio C++ 工具未完成脚本构建，可准备上游构建依赖或使用预构建 HTTP 服务 |
| Docker | 示例配置与文档命令已做静态检查，尚未执行镜像构建和完整容器下载验证；使用前按自己的镜像、网络、数据卷和运行用户验证 |

匿名样本成功不代表所有内容均可下载；视频限制、出口网络和上游版本变化仍需按前述步骤排查。
