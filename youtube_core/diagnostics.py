"""不访问网络或登录态的管理员本机诊断。"""

from __future__ import annotations

import asyncio
import math
import re
import shutil
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


async def build_diagnostic_report(
    config_manager: ConfigManager, *, active_media_flows: int = 0
) -> str:
    """检查依赖和配置状态；不读取 Cookie、不启动浏览器，也不验证账号有效性。"""
    cfg = config_manager
    environment, ffmpeg, ffprobe = await asyncio.gather(
        asyncio.to_thread(probe_ytdlp_environment, cfg.youtube.ytdlp_js_runtime),
        asyncio.to_thread(shutil.which, "ffmpeg"),
        asyncio.to_thread(shutil.which, "ffprobe"),
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
