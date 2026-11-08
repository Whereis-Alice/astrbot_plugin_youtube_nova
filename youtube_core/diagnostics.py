"""不访问网络或登录态的管理员本机诊断。"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import math
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .parser.runtime_manager.youtube import probe_ytdlp_environment

if TYPE_CHECKING:
    from .config_manager import ConfigManager


def _version_label(value: str) -> str:
    """仅展示版本号，避免意外把探测器的原始诊断文本带入回复。"""
    value = str(value or "")
    return value if re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,48}", value) else "未知版本"


def _limit_label(value: float) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return "未知"
    if not math.isfinite(amount) or amount < 0:
        return "未知"
    return f"{amount:g} MiB" if amount else "不限"


def _pot_configuration_details(provider: str) -> tuple[list[str], list[str]]:
    """只检查明确配置的 bgutil 本机文件，不访问 HTTP 服务或运行生成脚本。"""
    if not provider:
        return [], []
    lines: list[str] = []
    advice: list[str] = []
    try:
        python_version = importlib.metadata.version("bgutil-ytdlp-pot-provider")
    except importlib.metadata.PackageNotFoundError:
        python_version = ""
    except Exception:
        python_version = "unknown"
    lines.append("bgutil Python 包：" + (_version_label(python_version) if python_version else "未安装"))
    if not python_version:
        advice.append("在 AstrBot 所用 Python 环境运行仓库 tools/setup_pot_provider.py 安装配套组件，详见 docs/installation.md。")
    if re.match(r"^https?://", provider, re.I):
        lines.append("PO Token 配置：HTTP 服务（未连接验证）")
        advice.append("确认 HTTP 服务与 Python 提供方主版本一致；本机诊断不会连接服务或验证令牌。")
        return lines, advice
    lines.append("PO Token 配置：本机脚本")
    try:
        server = Path(provider).expanduser()
        if not server.is_dir():
            lines.append("PO Token 脚本：目录不存在或不是目录")
            advice.append("提供方位置应填写含 package.json 与 build/generate_once.js 的 server 目录，不能填写脚本文件。")
            return lines, advice
        package_path = server / "package.json"
        if package_path.stat().st_size > 65536:
            raise ValueError("package metadata too large")
        package = json.loads(package_path.read_text(encoding="utf-8"))
        script_version = str(package.get("version") or "")
        lines.append("PO Token 脚本版本：" + _version_label(script_version))
        built = (server / "build" / "generate_once.js").is_file()
        lines.append("PO Token 编译产物：" + ("已找到（未执行）" if built else "缺失"))
        if not built:
            advice.append("使用仓库安装工具完成 npm 依赖安装与编译，再重载插件。")
        if python_version and re.match(r"^\d+\.", python_version) and re.match(r"^\d+\.", script_version):
            matched = python_version.split(".")[0] == script_version.split(".")[0]
            lines.append("PO Token 主版本：" + ("匹配（未验证令牌生成）" if matched else "不匹配"))
            if not matched:
                advice.append("安装主版本匹配的 Python 提供方和脚本；仅升级 Python 包不能修复脚本版本不匹配。")
    except (OSError, ValueError, TypeError, AttributeError):
        lines.append("PO Token 脚本：元数据无法读取")
        advice.append("检查 server 目录的 package.json、文件权限及编译产物；仓库安装说明提供配套部署步骤。")
    return lines, advice


async def build_diagnostic_report(
    config_manager: ConfigManager, *, active_media_flows: int = 0
) -> str:
    """检查依赖和配置状态；不读取 Cookie、不启动浏览器，也不验证账号有效性。"""
    cfg = config_manager
    environment, ffmpeg, ffprobe, pot_details = await asyncio.gather(
        asyncio.to_thread(probe_ytdlp_environment, cfg.youtube.ytdlp_js_runtime),
        asyncio.to_thread(shutil.which, "ffmpeg"),
        asyncio.to_thread(shutil.which, "ffprobe"),
        asyncio.to_thread(_pot_configuration_details, str(getattr(cfg.youtube, "ytdlp_pot_provider", "") or "").strip()),
        return_exceptions=True,
    )
    lines = ["YouTube Nova 状态"]
    advice: list[str] = []

    if isinstance(environment, BaseException):
        if isinstance(environment, asyncio.CancelledError):
            raise environment
        lines.append("取流依赖：检查未完成")
        advice.append("请查看服务器日志，确认 AstrBot 所用 Python 环境可加载 yt-dlp。")
    else:
        env = environment
        lines.append("取流依赖：" + ("就绪" if env.ready else "缺件"))
        lines.append("yt-dlp：" + (_version_label(env.version) if env.available else "未安装"))
        if not env.available:
            advice.append("在 AstrBot 所用 Python 环境安装或更新 yt-dlp、yt-dlp-ejs。")
        elif env.needs_js_runtime:
            lines.append("yt-dlp-ejs：" + ("已安装" if env.ejs_available else "缺失"))
            if not env.ejs_available:
                advice.append("在 AstrBot 所用 Python 环境安装或更新 yt-dlp-ejs。")
            runtime = env.runtime_name if env.runtime_name in {"node", "deno", "bun", "quickjs"} else ""
            lines.append("JS 运行时：" + (f"{runtime} {_version_label(env.runtime_version)}" if runtime else "缺失"))
            if not runtime:
                advice.append("安装 Node.js 22+、Deno 2.3+、Bun 或 QuickJS 中任意一个 JS 运行时。")
        else:
            lines.append("JS 运行时：使用 yt-dlp 内置解析器")
        lines.append("PO Token 提供方：" + ("已检测到" if env.pot_providers else "未检测到（可选）"))

    if isinstance(pot_details, BaseException):
        if isinstance(pot_details, asyncio.CancelledError):
            raise pot_details
        lines.append("PO Token 配置：检查未完成")
    else:
        detail_lines, detail_advice = pot_details
        lines.extend(detail_lines)
        advice.extend(detail_advice)

    for name, result in (("FFmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            status = "检查未完成"
        else:
            status = "已找到" if result else "缺失"
            if not result:
                advice.append(f"安装 {name} 并加入 AstrBot 进程的 PATH。")
        lines.append(f"{name}：{status}")

    cache_available = cfg.download.cache_dir_available
    lines.append("媒体缓存：" + ("可用（初始化检查）" if cache_available else "不可用"))
    if not cache_available:
        advice.append("检查媒体缓存目录配置和写入权限。")
    if cfg.youtube.cookie_source == "browser":
        cookie_label = "浏览器 Profile（未验证登录态）"
    elif cfg.youtube.cookie:
        cookie_label = "手动 Cookie（已配置，未验证登录态）"
    else:
        cookie_label = "匿名（未配置手动 Cookie）"
    lines.extend(
        [
            f"Cookie 来源：{cookie_label}",
            "YouTube 代理：" + ("已启用" if cfg.proxy.youtube_use_proxy and cfg.proxy.address else "未启用"),
            f"普通视频发送上限：{_limit_label(cfg.download.send_video_max_mb)}",
            f"下载硬上限：{_limit_label(cfg.download.max_video_size_mb)}",
            f"当前媒体处理：{max(0, int(active_media_flows))}",
        ]
    )
    if advice:
        lines.append("建议：\n" + "\n".join(f"- {item}" for item in advice))
    return "\n".join(lines)
