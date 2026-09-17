"""
YouTube 视频解析器。

分层降级设计（三层各自独立失败）：

1. 元数据层：oEmbed 与 Innertube player 端点并发请求；两者都失败时回退抓取
   watch 页面内嵌的 ytInitialPlayerResponse。
2. 媒体层：统一交给 yt-dlp 执行播放器 JS、选择格式并给出可下载直链，避免
   维护一套容易返回半截 403 地址的重复选流实现。
3. 增强层：next 端点补齐头像、点赞数、评论数与热评，失败只降级不报错。

任何一层失败都只是让卡片信息变少，不会让整次解析失败；详细降级链写入
后台日志，用户侧只看到最终结论。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import aiohttp

from .base import BaseVideoParser
from ...constants import Config
from ...logger import logger
from ...types import MediaMetadata
from ..runtime_manager.youtube import (
    BrowserCookieError,
    BrowserCookieSource,
    BrowserCookieSpec,
    YOUTUBE_ORIGIN,
    YouTubeCookieRuntime,
    YtDlpStream,
    YtDlpStreamResolver,
    build_sapisid_authorization,
    parse_cookie_header,
    probe_ytdlp_environment,
    summarize_ytdlp_info,
)
from ..utils import build_request_headers


__all__ = [
    "YouTubeParser",
    "build_sapisid_authorization",
    "build_youtube_stats_line",
    "detect_youtube_login_state",
    "extract_youtube_comments",
    "extract_youtube_comment_count",
    "extract_youtube_like_count",
    "extract_youtube_links",
    "extract_youtube_owner",
    "extract_youtube_publish_date",
    "extract_youtube_view_count",
    "find_comment_continuation",
    "localize_relative_time",
    "parse_compact_number",
    "parse_cookie_header",
    "parse_watch_html",
    "parse_youtube_identity",
    "thumbnail_candidates",
    "upscale_avatar_url",
]


# ── 常量 ──────────────────────────────────────────────────

# InnerTube 的 web 客户端 API key。这是 Google 自己内联在每个 youtube.com 页面里的
# 公开常量，全网所有 YouTube 逆向实现（yt-dlp、youtube.js、pytube 等）都硬编码同一个
# 值；它不由 Cloud Console 签发，不绑定账号也不绑定计费，因此可以随代码分发。
# GitHub 的 secret scanning 只按 AIza + 35 字符这个格式匹配，无法区分公开客户端常量
# 和私有 Cloud key，所以会在这一行报一条 Google API Key 告警——那是误报，无需轮换。
INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_BASE = "https://www.youtube.com/youtubei/v1"
_COMMENTS_PANEL_ID = "engagement-panel-comments-section"

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
YOUTUBE_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_ID_RE = re.compile(
    r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})(?:[/?].*)?$",
    re.IGNORECASE,
)
_SHORT_PATH_ID_RE = re.compile(r"^/([A-Za-z0-9_-]{11})(?:[/?].*)?$")

YOUTUBE_URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:/@-])(?:https?://)?"
    r"(?:(?:www|m|music)\.)?"
    r"(?:youtube\.com|youtube-nocookie\.com|youtu\.be)"
    r"/[^\s<>\"'()\[\]{}]+",
    re.IGNORECASE,
)
_LINK_TAIL_CHARS = ".,!?)]}>\"'，。！？；：）】》」、"
# 群聊里常见「链接 + 中文指令」直接粘连（例如 youtu.be/xxx媒体解析），
# 先截断到第一个非 URL 安全字符，避免把中文当成路径的一部分。
_URL_SAFE_PREFIX_RE = re.compile(r"^[A-Za-z0-9._~:/?#@!$&*+,;=%()\[\]'-]+")

_THUMBNAIL_NAMES = (
    "maxresdefault.jpg",
    "sddefault.jpg",
    "hqdefault.jpg",
    "mqdefault.jpg",
)

_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

# watch 页面里的访客身份令牌。Innertube 响应会在 responseContext 里给同样的
# 值，两处都读得到就都读，谁先到用谁。
_VISITOR_DATA_RE = re.compile(
    r"\"(?:VISITOR_DATA|visitorData)\"\s*:\s*\"([^\"]{8,})\""
)

# Innertube 只承担元数据与 next/comments 增强，不再直接产出媒体流。
INNERTUBE_CLIENTS: Dict[str, Dict[str, Any]] = {
    # TVHTML5_SIMPLY 在播放器门禁下仍经常下发 videoDetails，适合补标题、
    # 作者、时长和播放量；它不参与选流。
    "tv_simply": {
        "client_id": 75,
        "cookies": True,
        "user_agent": (
            "Mozilla/5.0 (PlayStation; PlayStation 4/12.00) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0 "
            "Safari/605.1.15"
        ),
        "context": {
            "clientName": "TVHTML5_SIMPLY",
            "clientVersion": "1.0",
            "platform": "TV",
        },
    },
    # WEB 上下文供 next / comments 端点使用。
    "web": {
        "client_id": 1,
        "cookies": True,
        "user_agent": _WEB_USER_AGENT,
        "context": {
            "clientName": "WEB",
            "clientVersion": "2.20250312.04.00",
            "platform": "DESKTOP",
        },
    },
}

# 元数据客户端顺序；它们不参与选流。
METADATA_PLAYER_CLIENTS: Tuple[str, ...] = ("tv_simply",)

# Cookie 鉴权原语统一由 runtime_manager.youtube 提供，这里只留别名，
# 避免同一份 SAPISIDHASH 逻辑在两处各写一遍。
_YOUTUBE_ORIGIN = YOUTUBE_ORIGIN

# 需要登录/验证才能放行的门禁状态，命中后给出可操作建议。
_GATED_STATUS_CODES = frozenset(
    {
        "LOGIN_REQUIRED",
        "AGE_VERIFICATION_REQUIRED",
        "CONTENT_CHECK_REQUIRED",
    }
)

_PLAYABILITY_LABELS = {
    "LOGIN_REQUIRED": "被 YouTube 机器人验证挡下",
    "AGE_VERIFICATION_REQUIRED": "年龄限制",
    "UNPLAYABLE": "无法播放",
    "ERROR": "视频不可用",
    "LIVE_STREAM_OFFLINE": "直播未开始",
    "CONTENT_CHECK_REQUIRED": "敏感内容",
}

# ── URL 解析 ──────────────────────────────────────────────

def parse_youtube_identity(url: str, _depth: int = 0) -> Optional[str]:
    """严格解析 YouTube 链接并返回 11 位视频 ID，非法输入返回 None。"""
    if not isinstance(url, str) or not url.strip() or _depth > 2:
        return None
    normalized = url.strip()
    if "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
    except (TypeError, ValueError):
        return None

    host = (parsed.hostname or "").lower().strip(".")
    path = parsed.path or "/"

    if host in YOUTUBE_SHORT_HOSTS:
        match = _SHORT_PATH_ID_RE.match(path)
        return match.group(1) if match else None

    if host not in YOUTUBE_HOSTS:
        return None

    if path.rstrip("/").lower() in {"/watch", "/watch_popup"}:
        candidates = parse_qs(parsed.query or "").get("v") or []
        for candidate in candidates:
            if VIDEO_ID_RE.match(candidate or ""):
                return candidate
        return None

    if path.lower().startswith("/attribution_link"):
        targets = parse_qs(parsed.query or "").get("u") or []
        for target in targets:
            if not target:
                continue
            nested = target
            if nested.startswith("/"):
                nested = "https://www.youtube.com" + nested
            found = parse_youtube_identity(nested, _depth + 1)
            if found:
                return found
        return None

    match = _PATH_ID_RE.match(path)
    return match.group(1) if match else None


def extract_youtube_links(text: str) -> List[str]:
    """从文本中提取 YouTube 链接，按视频 ID 去重并保留原始链接形态。"""
    links: List[str] = []
    seen: set[str] = set()
    for match in YOUTUBE_URL_PATTERN.finditer(text or ""):
        link = match.group(0)
        safe = _URL_SAFE_PREFIX_RE.match(link)
        if safe:
            link = safe.group(0)
        link = link.rstrip(_LINK_TAIL_CHARS)
        video_id = parse_youtube_identity(link)
        if video_id and video_id not in seen:
            seen.add(video_id)
            links.append(link)
    return links


def thumbnail_candidates(video_id: str) -> List[str]:
    """返回按清晰度从高到低排列的官方缩略图候选地址。"""
    return [
        f"https://i.ytimg.com/vi/{video_id}/{name}"
        for name in _THUMBNAIL_NAMES
    ]


# ── 通用工具 ──────────────────────────────────────────────

def _as_int(value: Any) -> int:
    """尽力把任意值转成非负整数，失败返回 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            try:
                return max(0, int(digits))
            except ValueError:
                return 0
    return 0


def parse_compact_number(value: Any) -> int:
    """解析 1.2K / 3.4M / 1.2万 这类紧凑计数文本。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    if not isinstance(value, str):
        return 0
    text = value.strip().replace(",", "").replace(" ", "")
    if not text:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KMBkmb万千亿億])?", text)
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").lower()
    multiplier = {
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "千": 1_000,
        "万": 10_000,
        "亿": 100_000_000,
        "億": 100_000_000,
    }.get(unit, 1)
    return max(0, int(number * multiplier))


def _format_count(value: int) -> str:
    """把计数格式化成中文习惯的紧凑写法。"""
    if value <= 0:
        return "0"
    if value >= 100_000_000:
        text = f"{value / 100_000_000:.1f}"
        return (text[:-2] if text.endswith(".0") else text) + "亿"
    if value >= 10_000:
        text = f"{value / 10_000:.1f}"
        return (text[:-2] if text.endswith(".0") else text) + "万"
    return str(value)


def build_youtube_stats_line(
    views: Any = 0,
    likes: Any = 0,
    comments: Any = 0,
) -> str:
    """拼装卡片统计行，值为 0 的条目会被省略。"""
    parts: List[str] = []
    for emoji, raw in (
        ("\U0001f440", views),
        ("\U0001f44d", likes),
        ("\U0001f4ac", comments),
    ):
        text = _format_count(_as_int(raw))
        if text != "0":
            parts.append(f"{emoji}{text}")
    return " ".join(parts)


def _text_of(node: Any) -> str:
    """从 Innertube 的各种文本包装结构中取出纯文本。"""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return str(node)
    if isinstance(node, list):
        return "".join(_text_of(item) for item in node).strip()
    if not isinstance(node, dict):
        return ""
    for key in ("simpleText", "content", "text", "label"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    runs = node.get("runs")
    if isinstance(runs, list):
        merged = "".join(_text_of(run) for run in runs).strip()
        if merged:
            return merged
    accessibility = node.get("accessibilityText")
    if isinstance(accessibility, str) and accessibility.strip():
        return accessibility.strip()
    return ""


def _deep_iter(node: Any, key: str, depth: int = 0) -> Iterable[Any]:
    """深度遍历嵌套结构，产出所有匹配指定键的值。"""
    if depth > 24 or node is None:
        return
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                yield value
            yield from _deep_iter(value, key, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _deep_iter(item, key, depth + 1)


def _deep_first(node: Any, key: str) -> Any:
    """返回第一个匹配指定键的值，找不到返回 None。"""
    for value in _deep_iter(node, key):
        return value
    return None


def _best_thumbnail(node: Any) -> str:
    """从缩略图集合中挑出面积最大的一张。"""
    candidates: List[Tuple[int, str]] = []
    for group in _deep_iter(node, "thumbnails"):
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url:
                continue
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith(("http://", "https://")):
                continue
            area = _as_int(item.get("width")) * _as_int(item.get("height"))
            candidates.append((area, url))
    if not candidates:
        return ""
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# next 端点的 dateText 形如 "Oct 24, 2009"，也可能带
# "Premiered" / "Streamed live on" / "Started streaming on" 前缀。
_EN_DATE_RE = re.compile(
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})"
)


def _parse_english_date(text: Any) -> str:
    """把英文日期文案解析成 YYYY-MM-DD（依赖 next 固定用 hl=en）。"""
    if not isinstance(text, str):
        return ""
    match = _EN_DATE_RE.search(text)
    if not match:
        return ""
    month = _MONTH_NAMES.get(match.group(1)[:3].lower())
    if not month:
        return ""
    day = int(match.group(2))
    year = int(match.group(3))
    if not 1 <= day <= 31 or not 1900 <= year <= 2999:
        return ""
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


# next 端点固定 hl=en，评论时间会回英文相对文案（"3 days ago"、
# "1 month ago (edited)"、"Streamed 2 weeks ago"）。卡片是中文界面，
# 直接透出会中英混排，所以在解析层就地本地化。
_REL_TIME_RE = re.compile(
    r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)
_REL_TIME_UNITS = {
    "second": "秒",
    "minute": "分钟",
    "hour": "小时",
    "day": "天",
    "week": "周",
    "month": "个月",
    "year": "年",
}


def localize_relative_time(text: Any) -> str:
    """把英文相对时间转成中文；无法识别时原样返回。"""
    if not isinstance(text, str):
        return ""
    raw = text.strip()
    if not raw:
        return ""
    lowered = raw.lower()
    edited = "(edited)" in lowered or "edited" == lowered.rsplit(" ", 1)[-1]
    match = _REL_TIME_RE.search(raw)
    if match:
        unit = _REL_TIME_UNITS.get(match.group(2).lower())
        if not unit:
            return raw
        result = f"{int(match.group(1))}{unit}前"
    elif "just now" in lowered or lowered in {"now", "moments ago"}:
        result = "刚刚"
    else:
        parsed = _parse_english_date(raw)
        if parsed:
            return parsed
        return raw
    if "streamed" in lowered:
        result = "直播于" + result
    elif "premiered" in lowered:
        result = "首播于" + result
    if edited:
        result += "（已编辑）"
    return result


# Google 头像直链把尺寸写在 URL 里（=s48-c-k-c0x00ffffff-no-rj）。
# Innertube 默认只给 48px，放进卡片会明显发虚，这里统一抬到 176px。
_AVATAR_S_RE = re.compile(r"=s\d+", re.IGNORECASE)
_AVATAR_WH_RE = re.compile(r"=w\d+-h\d+", re.IGNORECASE)


def upscale_avatar_url(url: Any, size: int = 176) -> str:
    """把 Google 头像直链的尺寸参数抬到更高分辨率。"""
    if not isinstance(url, str) or not url.strip():
        return ""
    text = url.strip()
    if text.startswith("//"):
        text = "https:" + text
    if "googleusercontent.com" not in text and "ggpht.com" not in text:
        return text
    size = max(48, min(900, int(size)))
    if _AVATAR_WH_RE.search(text):
        return _AVATAR_WH_RE.sub(f"=w{size}-h{size}", text, count=1)
    if _AVATAR_S_RE.search(text):
        return _AVATAR_S_RE.sub(f"=s{size}", text, count=1)
    return text

def _parse_iso_date(raw: Any) -> str:
    """解析 ISO8601 时间串，输出 YYYY-MM-DD[ HH:MM]。"""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    text = raw.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        return match.group(0) if match else ""
    if parsed.hour or parsed.minute:
        return parsed.strftime("%Y-%m-%d %H:%M")
    return parsed.strftime("%Y-%m-%d")


def extract_youtube_publish_date(
    player: Any,
    next_payload: Any = None,
) -> str:
    """解析发布时间，输出 YYYY-MM-DD[ HH:MM]。

    ios / android_vr / tv 这些原生客户端的 player 响应里**没有**
    playerMicroformatRenderer，所以只看 player 会永远拿不到日期。这里再
    退到 next 端点的 dateText（"Oct 24, 2009"）与 microformat 字段。
    """
    microformat = _deep_first(player, "playerMicroformatRenderer")
    if isinstance(microformat, dict):
        for key in ("publishDate", "uploadDate"):
            parsed = _parse_iso_date(microformat.get(key))
            if parsed:
                return parsed

    if next_payload:
        for key in ("publishDate", "uploadDate"):
            for value in _deep_iter(next_payload, key):
                parsed = _parse_iso_date(value)
                if parsed:
                    return parsed
        for primary in _deep_iter(next_payload, "videoPrimaryInfoRenderer"):
            if not isinstance(primary, dict):
                continue
            parsed = _parse_english_date(_text_of(primary.get("dateText")))
            if parsed:
                return parsed
        for value in _deep_iter(next_payload, "dateText"):
            parsed = _parse_english_date(_text_of(value))
            if parsed:
                return parsed
    return ""


# ── Cookie 鉴权（SAPISIDHASH）────────────────────────────
#
# parse_cookie_header / build_sapisid_authorization 现由
# runtime_manager.youtube 实现并在本模块顶部导入，此处仅作为向后兼容的
# 再导出点（__all__ 里仍然保留这两个名字）。


def detect_youtube_login_state(payload: Any) -> Optional[bool]:
    """
    从 Innertube 响应里读出「服务端是否认为本次请求已登录」。

    Returns:
        Optional[bool]: True=已登录，False=被当作未登录，None=响应里没有该信号。
    """
    if not isinstance(payload, dict):
        return None
    context = payload.get("responseContext")
    node: Any = None
    if isinstance(context, dict):
        node = context.get("mainAppWebResponseContext")
    if not isinstance(node, dict):
        node = _deep_first(payload, "mainAppWebResponseContext")
    if not isinstance(node, dict):
        return None
    logged_out = node.get("loggedOut")
    if isinstance(logged_out, bool):
        return not logged_out
    if isinstance(logged_out, str):
        lowered = logged_out.strip().lower()
        if lowered in ("true", "false"):
            return lowered == "false"
    return None

# ── next 端点数据提取 ─────────────────────────────────────

def extract_youtube_owner(payload: Any) -> Tuple[str, str, str]:
    """提取 UP 主名称、头像与频道 ID。"""
    name = ""
    avatar = ""
    channel_id = ""
    owner = _deep_first(payload, "videoOwnerRenderer")
    if isinstance(owner, dict):
        name = _text_of(owner.get("title"))
        avatar = _best_thumbnail(owner.get("thumbnail"))
        endpoint = owner.get("navigationEndpoint")
        browse = _deep_first(endpoint, "browseId")
        if isinstance(browse, str) and browse.startswith("UC"):
            channel_id = browse
    if not avatar:
        avatar = _best_thumbnail(_deep_first(payload, "avatar"))
    return name, upscale_avatar_url(avatar), channel_id


# 点赞数无障碍文案的两种句式：老式 "6,550 likes"，以及 2026 年的
# "like this video along with 6,550 other people"。
_LIKE_TEXT_PATTERNS = (
    re.compile(r"^\s*([\d.,]+\s*[KMB]?)\s+likes?\b", re.IGNORECASE),
    re.compile(
        r"along with\s+([\d.,]+\s*[KMB]?)\s+other\s+(?:people|person)",
        re.IGNORECASE,
    ),
)

# 新版 buttonViewModel 用 iconName 区分点赞/点踩/分享。
_LIKE_ICON_NAMES = frozenset({"LIKE", "LIKE_FILLED"})

# likeCountEntity 里数字字段的优先级：精确整数 > 展开文案 > 压缩文案。
_LIKE_ENTITY_KEYS = (
    "likeCountIfIndifferentNumber",
    "likeCountIfLikedNumber",
    "expandedLikeCountIfIndifferent",
    "expandedLikeCountIfLiked",
    "likeCountIfIndifferent",
    "likeCountIfLiked",
)


def _like_button_scopes(payload: Any) -> List[Any]:
    """按「点赞按钮子树 → 整个 payload」的顺序给出搜索范围。

    先在点赞按钮子树里找，避免把分享/订阅/评论的按钮文本误读成点赞数；
    找不到子树（或子树里没有数字）时再退回全量搜索，保持对老结构兼容。
    """
    scopes: List[Any] = []
    for key in (
        "segmentedLikeDislikeButtonViewModel",
        "segmentedLikeDislikeButtonRenderer",
        "likeButtonViewModel",
    ):
        node = _deep_first(payload, key)
        if node is None:
            continue
        if any(node is existing for existing in scopes):
            continue
        scopes.append(node)
    if not any(payload is existing for existing in scopes):
        scopes.append(payload)
    return scopes


def _like_count_in_scope(scope: Any) -> int:
    """在给定子树里依次尝试四种点赞数来源。"""
    for entity in _deep_iter(scope, "likeCountEntity"):
        if not isinstance(entity, dict):
            continue
        for key in _LIKE_ENTITY_KEYS:
            value = parse_compact_number(_text_of(entity.get(key)))
            if value:
                return value

    for text in _deep_iter(scope, "accessibilityText"):
        if not isinstance(text, str):
            continue
        for pattern in _LIKE_TEXT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            value = parse_compact_number(match.group(1))
            if value:
                return value

    for toggle in _deep_iter(scope, "toggleButtonRenderer"):
        if not isinstance(toggle, dict):
            continue
        value = parse_compact_number(_text_of(toggle.get("defaultText")))
        if value:
            return value

    for button in _deep_iter(scope, "buttonViewModel"):
        if not isinstance(button, dict):
            continue
        icon = str(button.get("iconName") or "").strip().upper()
        if icon not in _LIKE_ICON_NAMES:
            continue
        value = parse_compact_number(_text_of(button.get("title")))
        if value:
            return value
    return 0


def extract_youtube_like_count(payload: Any) -> int:
    """提取点赞数。

    2026 年的 next 响应有两种形态：likeCountEntity 被填充时直接读数字；
    被机器人门禁拦下的视频只给一个空壳 entity
    （{"key": "unset_like_count_entity_key"}），这时精确值只剩无障碍文案
    （"like this video along with 6,550 other people"）和新版
    buttonViewModel 的 title（"6.5K"）两个出处。所以 next 固定用 hl=en。
    """
    for scope in _like_button_scopes(payload):
        value = _like_count_in_scope(scope)
        if value:
            return value
    return 0


def extract_youtube_view_count(payload: Any) -> int:
    """提取播放量。

    player 被门禁拦下时 videoDetails 整块缺失，viewCount 也就没了；但
    next 端点即便在门禁下仍返回 videoViewCountRenderer，精确值可用。
    """
    for renderer in _deep_iter(payload, "videoViewCountRenderer"):
        if not isinstance(renderer, dict):
            continue
        for key in ("viewCount", "originalViewCount", "shortViewCount"):
            value = parse_compact_number(_text_of(renderer.get(key)))
            if value:
                return value
    return 0


def extract_youtube_comment_count(payload: Any) -> int:
    """提取评论总数。

    web 系客户端返回 commentsEntryPointHeaderRenderer.commentCount；ios /
    android_vr / tv 这些原生客户端不带它，评论数在评论面板标题的
    contextualInfo 里（形如 "2.4M"）。两种都要认。
    """
    for header in _deep_iter(payload, "commentsEntryPointHeaderRenderer"):
        if not isinstance(header, dict):
            continue
        value = parse_compact_number(_text_of(header.get("commentCount")))
        if value:
            return value

    # 只认评论面板，避免把章节等其他面板的 contextualInfo 当成评论数。
    for section in _deep_iter(payload, "engagementPanelSectionListRenderer"):
        if not isinstance(section, dict):
            continue
        if section.get("panelIdentifier") != _COMMENTS_PANEL_ID:
            continue
        header = _deep_first(section, "engagementPanelTitleHeaderRenderer")
        if not isinstance(header, dict):
            continue
        value = parse_compact_number(_text_of(header.get("contextualInfo")))
        if value:
            return value

    for header in _deep_iter(payload, "engagementPanelTitleHeaderRenderer"):
        if not isinstance(header, dict):
            continue
        if not _text_of(header.get("title")).strip().lower().startswith(
            "comment"
        ):
            continue
        value = parse_compact_number(_text_of(header.get("contextualInfo")))
        if value:
            return value
    return 0


def find_comment_continuation(payload: Any) -> str:
    """找出评论区的 continuation token。"""
    for section in _deep_iter(payload, "itemSectionRenderer"):
        if not isinstance(section, dict):
            continue
        if section.get("sectionIdentifier") != "comment-item-section":
            continue
        command = _deep_first(section, "continuationCommand")
        if isinstance(command, dict):
            token = command.get("token")
            if isinstance(token, str) and token:
                return token

    has_comments = any(
        True for _ in _deep_iter(payload, "commentsEntryPointHeaderRenderer")
    ) or any(
        isinstance(section, dict)
        and section.get("panelIdentifier") == _COMMENTS_PANEL_ID
        for section in _deep_iter(payload, "engagementPanelSectionListRenderer")
    )
    if has_comments:
        for command in _deep_iter(payload, "continuationCommand"):
            if not isinstance(command, dict):
                continue
            token = command.get("token")
            if isinstance(token, str) and token:
                return token
    return ""


def _accessibility_label(node: Any) -> str:
    """取出 Innertube 结构里的无障碍文案（常带精确数值）。"""
    if isinstance(node, str):
        return node.strip()
    if not isinstance(node, dict):
        return ""
    accessibility = node.get("accessibility")
    if isinstance(accessibility, dict):
        data = accessibility.get("accessibilityData")
        if isinstance(data, dict):
            label = data.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
    label = node.get("accessibilityText")
    return label.strip() if isinstance(label, str) else ""


_EXACT_COUNT_RE = re.compile(r"(\d[\d,]*)(?![\d,.])\s*([KMBkmb万千亿億])?")


def _exact_count(text: Any) -> Optional[int]:
    """只在文案给的是完整数字（1,100 / 1100）时返回精确值。"""
    if not isinstance(text, str) or not text.strip():
        return None
    match = _EXACT_COUNT_RE.search(text)
    if not match or match.group(2):
        return None
    digits = match.group(1).replace(",", "")
    if not digits:
        return None
    try:
        return max(0, int(digits))
    except ValueError:
        return None


def _comment_likes(compact: Any, a11y: Any) -> Tuple[int, str]:
    """返回 (点赞数, 原始压缩文案)。

    YouTube 的评论点赞只给压缩值（"1.1K"），把它换算成 1100 再显示是假精度：
    三条 1.1K~1.19K 的热评会一模一样都写成 1100。所以只有无障碍文案给出完整
    数字时才用精确值，否则把 YouTube 自己的压缩文案原样透给卡片。
    """
    exact = _exact_count(_accessibility_label(a11y) or (a11y if isinstance(a11y, str) else ""))
    compact_text = _text_of(compact)
    if exact is not None:
        return exact, ""
    value = parse_compact_number(compact_text)
    display = compact_text if re.search(r"[KMBkmb万千亿億]", compact_text) else ""
    return value, display

def _comment_from_entity(entity: Any) -> Optional[Dict[str, Any]]:
    """解析新版 commentEntityPayload 结构。"""
    if not isinstance(entity, dict):
        return None
    properties = entity.get("properties")
    author = entity.get("author")
    if not isinstance(properties, dict):
        return None
    message = _text_of(properties.get("content"))
    if not message:
        return None
    author = author if isinstance(author, dict) else {}
    toolbar = entity.get("toolbar")
    toolbar = toolbar if isinstance(toolbar, dict) else {}
    likes, likes_text = _comment_likes(
        toolbar.get("likeCountNotliked") or toolbar.get("likeCountLiked"),
        toolbar.get("likeCountA11y"),
    )
    avatar = upscale_avatar_url(author.get("avatarThumbnailUrl"))
    return {
        "comment_id": str(properties.get("commentId") or ""),
        "username": _text_of(author.get("displayName")),
        "uid": str(author.get("channelId") or ""),
        "likes": likes,
        "likes_text": likes_text,
        "time": localize_relative_time(_text_of(properties.get("publishedTime"))),
        "message": message,
        "avatar_url": avatar,
    }


def _comment_from_renderer(renderer: Any) -> Optional[Dict[str, Any]]:
    """解析旧版 commentRenderer 结构。"""
    if not isinstance(renderer, dict):
        return None
    message = _text_of(renderer.get("contentText"))
    if not message:
        return None
    vote = renderer.get("voteCount")
    likes, likes_text = _comment_likes(
        vote if isinstance(vote, (int, float)) else _text_of(vote),
        _accessibility_label(vote),
    )
    return {
        "comment_id": str(renderer.get("commentId") or ""),
        "username": _text_of(renderer.get("authorText")),
        "uid": str(renderer.get("authorExternalChannelId") or ""),
        "likes": likes,
        "likes_text": likes_text,
        "time": localize_relative_time(
            _text_of(renderer.get("publishedTimeText"))
        ),
        "message": message,
        "avatar_url": upscale_avatar_url(
            _best_thumbnail(renderer.get("authorThumbnail"))
        ),
    }


def extract_youtube_comments(payload: Any, limit: int = 5) -> List[Dict[str, Any]]:
    """提取热评列表，按点赞数降序排列。"""
    if limit <= 0:
        return []
    collected: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def push(comment: Optional[Dict[str, Any]]) -> None:
        if not comment:
            return
        key = comment.get("comment_id") or (
            f"{comment.get('uid', '')}::{comment.get('message', '')}"
        )
        if key in seen:
            return
        seen.add(key)
        collected.append(comment)

    for entity in _deep_iter(payload, "commentEntityPayload"):
        push(_comment_from_entity(entity))
    if not collected:
        for renderer in _deep_iter(payload, "commentRenderer"):
            push(_comment_from_renderer(renderer))

    collected.sort(key=lambda item: _as_int(item.get("likes")), reverse=True)
    return collected[:limit]


# ── watch 页面兜底 ────────────────────────────────────────

def _extract_json_after(text: str, marker: str) -> Optional[Dict[str, Any]]:
    """从 HTML 中定位 marker 之后的第一个 JSON 对象并解析。"""
    if not text:
        return None
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find("{", index + len(marker))
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start:position + 1]
                try:
                    parsed = json.loads(snippet)
                except (ValueError, TypeError):
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def parse_watch_html(
    html: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """从 watch 页面解析出 player 响应与初始数据。"""
    player = _extract_json_after(html, "ytInitialPlayerResponse")
    initial = _extract_json_after(html, "ytInitialData")
    return player, initial


class _Deadline:
    """整条解析链共享的总时间预算。

    每层单独设超时会让最坏耗时叠加成分钟级，改成共享总预算后：任意一层
    慢下来，后面的层会自动缩短超时甚至直接跳过，整次解析耗时可控。
    """

    def __init__(self, budget: float):
        self._expire_at = time.monotonic() + max(2.0, float(budget))

    @property
    def remaining(self) -> float:
        return self._expire_at - time.monotonic()

    def expired(self) -> bool:
        return self.remaining <= 0.5

    def timeout(self, cap: float) -> float:
        return max(1.0, min(float(cap), max(1.0, self.remaining)))


# ── 解析器 ────────────────────────────────────────────────

class YouTubeParser(BaseVideoParser):
    """YouTube 视频解析器。"""

    def __init__(
        self,
        cookie: str = "",
        proxy: Optional[str] = None,
        max_height: int = 1080,
        hot_comment_count: int = 0,
        total_budget_seconds: float = 45.0,
        allow_dash: bool = True,
        cookie_alert_enabled: bool = False,
        cookie_state_file: str = "",
        cookie_auto_refresh: bool = True,
        browser_cookie_name: str = "",
        browser_cookie_profile: str = "",
        browser_cookie_keyring: str = "",
        browser_cookie_refresh_seconds: int = 60,
        browser_cookie_executable: str = "",
        browser_cookie_display: str = "",
        browser_cookie_wakeup_mode: str = "off",
        browser_cookie_wakeup_timeout_seconds: int = 30,
        ytdlp_js_runtime: str = "auto",
        ytdlp_timeout: int = 60,
        ytdlp_cookie_dir: str = "",
        ytdlp_pot_provider: str = "",
        ytdlp_fetch_pot: str = "auto",
        send_video_max_mb: float = 0.0,
    ):
        super().__init__("youtube")
        self.cookie = (cookie or "").strip()
        # Cookie 交给运行时托管：它负责吸收服务端下发的轮换值、落盘并在
        # 重启后接续，避免配置里那份静态字符串随时间腐烂。
        self.cookie_runtime = YouTubeCookieRuntime(
            configured_cookie=self.cookie,
            state_path=cookie_state_file,
            auto_refresh=cookie_auto_refresh,
        )
        self.browser_cookie_source: Optional[BrowserCookieSource] = None
        browser_name = str(browser_cookie_name or "").strip().lower()
        if browser_name:
            try:
                self.browser_cookie_source = BrowserCookieSource(
                    BrowserCookieSpec(
                        browser=browser_name,
                        profile=browser_cookie_profile,
                        keyring=browser_cookie_keyring,
                        executable=browser_cookie_executable,
                        display=browser_cookie_display,
                    ),
                    refresh_seconds=browser_cookie_refresh_seconds,
                    wakeup_mode=browser_cookie_wakeup_mode,
                    wakeup_timeout_seconds=(
                        browser_cookie_wakeup_timeout_seconds
                    ),
                )
            except ValueError as exc:
                logger.warning(f"[youtube] 浏览器 Cookie 配置无效，退回匿名: {exc}")
        self.proxy = proxy
        self.max_height = max(0, _as_int(max_height))
        # 聊天平台发得出去的体积上限会传给 yt-dlp 参与选流；0 表示不设预算。
        self.stream_max_bytes = (
            int(float(send_video_max_mb) * 1024 * 1024)
            if float(send_video_max_mb or 0) > 0
            else 0
        )
        self.hot_comment_count = max(0, _as_int(hot_comment_count))
        self.total_budget_seconds = max(8.0, float(total_budget_seconds or 45))
        self.allow_dash = bool(allow_dash)
        self.cookie_alert_enabled = bool(cookie_alert_enabled)
        self._cookie_alert_pending = False
        self._cookie_alert_reason = ""
        # 媒体流统一由 yt-dlp 解析；Innertube 只负责元数据与评论增强。
        self.ytdlp_js_runtime = (ytdlp_js_runtime or "auto").strip() or "auto"
        self.ytdlp_timeout = max(10, _as_int(ytdlp_timeout) or 60)
        self.ytdlp_cookie_dir = (ytdlp_cookie_dir or "").strip()
        # PO Token 提供方由用户自备（第三方 yt-dlp 插件），这里只透传。
        self.ytdlp_pot_provider = (ytdlp_pot_provider or "").strip()
        self.ytdlp_fetch_pot = (ytdlp_fetch_pot or "auto").strip() or "auto"
        self._ytdlp: Optional[YtDlpStreamResolver] = None
        # 访客身份令牌：从任意一次 Innertube/watch 响应里顺手捞到，之后所有
        # 请求带上它，让 YouTube 把这些请求看成同一个会话而不是一堆散客。
        # 只驻内存不落盘——它本身是短期票据，跨重启复用没有意义。
        self._visitor_data = ""
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        if (
            self.cookie
            and self.browser_cookie_source is None
            and not self.cookie_authenticated
        ):
            recognized = len(parse_cookie_header(self.cookie))
            logger.warning(
                f"[youtube] 已配置 cookie（识别出 {recognized} 项），但其中"
                "找不到 SAPISID / __Secure-3PAPISID，无法生成 SAPISIDHASH "
                "鉴权头，本次仍按匿名请求处理；常见原因是只粘贴了片段、"
                "或导出时并不处于登录状态，请在已登录的窗口里重新整段导出 "
                "cookies.txt 再填入 youtube.cookie"
            )

    _NA = "n/a"

    @property
    def cookie_authenticated(self) -> bool:
        """当前快照能否生成 SAPISIDHASH；浏览器同步后会动态变化。"""
        return self.cookie_runtime.authenticated

    @property
    def cookie_maintenance_enabled(self) -> bool:
        """是否存在需要后台同步或维护的 Cookie 来源。"""
        return bool(self.browser_cookie_source or self.cookie_runtime.header())

    def cookie_recovery_hint(self) -> str:
        """返回与当前凭据来源一致的恢复建议。"""
        if self.browser_cookie_source is not None:
            return "请确认服务器 Chromium 仍保持 YouTube 登录并可访问该 Profile"
        return "请重新导出并填写 YouTube Cookie"

    async def _sync_browser_cookie(
        self,
        force: bool = False,
    ) -> Tuple[bool, str]:
        """把浏览器的最新快照同步进 Innertube Cookie 运行时。"""
        source = self.browser_cookie_source
        if source is None:
            return False, "未启用浏览器 Profile"
        try:
            snapshot, freshly_read = await source.refresh(force=force)
        except asyncio.CancelledError:
            raise
        except BrowserCookieError as exc:
            if source.failure_streak >= 3:
                self._mark_cookie_alert("browser_cookie_unavailable")
            return False, f"浏览器 Profile 读取失败: {exc}"

        changed = self.cookie_runtime.replace_from_source(
            snapshot.header,
            source_label=f"浏览器 {source.spec.label()}",
            source_fingerprint=snapshot.fingerprint,
        )
        if changed:
            if snapshot.authenticated:
                logger.info(
                    "[youtube] 已同步浏览器登录态: "
                    f"profile={source.spec.label()}，{snapshot.cookie_count} 项，已鉴权"
                )
            else:
                logger.warning(
                    "[youtube] 浏览器 Profile 未读到可鉴权的 YouTube 登录态: "
                    f"profile={source.spec.label()}，{snapshot.cookie_count} 项，"
                    "缺少 SAPISID / __Secure-3PAPISID"
                )
                self._mark_cookie_alert("browser_cookie_signed_out")
        freshness = "已读取最新快照" if freshly_read else "使用近期快照"
        auth = "已鉴权" if snapshot.authenticated else "缺少登录凭据"
        return snapshot.authenticated, (
            f"{freshness}，{snapshot.cookie_count} 项，{auth}"
        )

    def _login_label(self, cookie_expired: bool) -> str:
        """把当前登录态压缩成一个可读标签。"""
        source_prefix = (
            "browser" if self.browser_cookie_source is not None else "cookie"
        )
        if not self.cookie_runtime.header():
            if self.browser_cookie_source is not None:
                return "browser(未读取到登录态，按匿名处理)"
            return "匿名"
        if not self.cookie_authenticated:
            return f"{source_prefix}(缺少 SAPISID，按匿名处理)"
        if cookie_expired:
            return f"{source_prefix}(已失效)"
        if not self.cookie_runtime.usable:
            return f"{source_prefix}(已判定失效，按匿名请求)"
        return f"{source_prefix}(已鉴权)"

    def _proxy_label(self) -> str:
        """返回代理配置状态标签。"""
        return "已配置" if self.proxy else "未配置"

    @staticmethod
    def _gate_advice(status_code: str, cookie_expired: bool) -> str:
        """针对门禁类失败给出可操作建议，其余情况返回空串。"""
        if status_code in _GATED_STATUS_CODES:
            return (
                "；处理建议: 启用有效的浏览器 Profile 登录态或填写 "
                "youtube.cookie，也可给 proxy.youtube 换一个住宅/家宽出口"
                "（机房 IP 极易被要求人机验证）"
            )
        if cookie_expired:
            return (
                "；处理建议: 检查浏览器登录态，或重新导出手动 Cookie"
                "（现有凭据已失效）"
            )
        return ""

    def _ytdlp_advice(self) -> str:
        """yt-dlp 缺件时，把可操作建议一并写进降级告警。"""
        env = probe_ytdlp_environment(self.ytdlp_js_runtime)
        if not env.ready:
            return f"；yt-dlp 取流不可用（{env.summary()}）{env.advice()}"
        # 链路本身没问题，但少了 PO Token 提供方这个可选增强件时，顺手把
        # 安装方式写进降级告警——它正是这类「有元数据、没媒体流」的常见解。
        return env.pot_advice()

    def _ytdlp_resolver(self) -> YtDlpStreamResolver:
        """惰性构造唯一的媒体流解析器。"""
        if self._ytdlp is None:
            self._ytdlp = YtDlpStreamResolver(
                proxy=self.proxy,
                max_height=self.max_height,
                allow_dash=self.allow_dash,
                timeout=self.ytdlp_timeout,
                js_runtime=self.ytdlp_js_runtime,
                cookie_dir=self.ytdlp_cookie_dir,
                pot_provider=self.ytdlp_pot_provider,
                fetch_pot=self.ytdlp_fetch_pot,
                max_bytes=self.stream_max_bytes,
                cookies_from_browser=(
                    self.browser_cookie_source.ytdlp_tuple()
                    if self.browser_cookie_source is not None
                    else None
                ),
            )
        return self._ytdlp

    async def _resolve_with_ytdlp(
        self,
        video_id: str,
    ) -> Tuple[Optional[YtDlpStream], Dict[str, Any]]:
        """走 yt-dlp 取流，返回 (流, 元数据摘要)。

        即便没挑出可用流，info 里的标题/作者/时长往往还是完整的，所以元数据
        摘要独立返回——Innertube 被全线拦下时它就是卡片唯一的信息来源。
        """
        resolver = self._ytdlp_resolver()
        started = time.time()
        try:
            stream, info = await resolver.resolve_full(
                video_id,
                cookie_header=(
                    ""
                    if self.browser_cookie_source is not None
                    else self.cookie_runtime.active_header()
                ),
                cookie_revision=self.cookie_runtime.revision,
                use_browser_cookies=self.cookie_runtime.usable,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f"[youtube] yt-dlp 取流异常: video_id={video_id}; "
                f"{type(exc).__name__}: {exc}"
            )
            return None, {}
        summary = summarize_ytdlp_info(info)
        if stream is None:
            return None, summary
        logger.info(
            f"[youtube] yt-dlp 取流成功: video_id={video_id} "
            f"流={stream.kind}@{stream.height}p "
            f"格式={stream.detail or self._NA} "
            f"耗时={time.time() - started:.2f}s"
        )
        return stream, summary

    def consume_cookie_alert(self) -> Optional[str]:
        """读取并消费一次待通知的 Cookie 失效原因。"""
        if not self._cookie_alert_pending:
            return None
        self._cookie_alert_pending = False
        return self._cookie_alert_reason or "cookie_expired"

    def _mark_cookie_alert(self, reason: str) -> None:
        """标记 Cookie 已失效，供插件侧决定是否私聊管理员。"""
        has_managed_source = self.browser_cookie_source is not None
        if not self.cookie_alert_enabled or not (
            has_managed_source or self.cookie_authenticated
        ):
            return
        self._cookie_alert_pending = True
        self._cookie_alert_reason = reason or "cookie_expired"

    # ── URL 匹配 ──────────────────────────────────────────

    def can_parse(self, url: str) -> bool:
        return parse_youtube_identity(url) is not None

    def extract_links(self, text: str) -> List[str]:
        return extract_youtube_links(text)

    # ── Innertube 请求 ────────────────────────────────────

    def _remember_visitor_data(self, value: Any) -> None:
        """记住服务端下发的访客身份令牌（首个即用，不覆盖）。

        YouTube 对「没有任何身份的裸请求」风控最狠。带上一次响应里给的
        visitorData 之后，同一次解析的多个请求会被看成同一个会话，
        比每一层都当散客上门要顺很多，而且不需要额外发任何请求。
        """
        text = str(value or "").strip()
        if text and not self._visitor_data:
            self._visitor_data = text

    def _absorb_visitor_data(self, payload: Any) -> None:
        """从 Innertube 响应的 responseContext 里捞访客身份令牌。"""
        if self._visitor_data or not isinstance(payload, dict):
            return
        context = payload.get("responseContext")
        if isinstance(context, dict):
            self._remember_visitor_data(context.get("visitorData"))

    def _innertube_headers(self, client_key: str) -> Dict[str, str]:
        profile = INNERTUBE_CLIENTS.get(client_key) or INNERTUBE_CLIENTS["web"]
        context = profile.get("context") or {}
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.youtube.com",
            "Referer": "https://www.youtube.com/",
            "User-Agent": profile.get("user_agent") or _WEB_USER_AGENT,
            "X-YouTube-Client-Name": str(profile.get("client_id") or 1),
            "X-YouTube-Client-Version": str(
                context.get("clientVersion") or "2.20250312.04.00"
            ),
        }
        if self._visitor_data:
            headers["X-Goog-Visitor-Id"] = self._visitor_data
        # Cookie 已判失效时一律按匿名请求，避免死凭据拖累公开元数据接口。
        cookie_header = self.cookie_runtime.active_header()
        if cookie_header and profile.get("cookies"):
            headers["Cookie"] = cookie_header
            authorization = build_sapisid_authorization(cookie_header)
            if authorization:
                headers["Authorization"] = authorization
                headers["X-Origin"] = _YOUTUBE_ORIGIN
                headers["X-Goog-AuthUser"] = "0"
        return headers

    def _innertube_body(
        self,
        client_key: str,
        hl: str = "zh-CN",
    ) -> Dict[str, Any]:
        profile = INNERTUBE_CLIENTS.get(client_key) or INNERTUBE_CLIENTS["web"]
        client = dict(profile.get("context") or {})
        client["hl"] = hl
        client["gl"] = "US"
        client["userAgent"] = profile.get("user_agent") or _WEB_USER_AGENT
        if self._visitor_data:
            client["visitorData"] = self._visitor_data
        body: Dict[str, Any] = {
            "context": {"client": client},
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        if profile.get("third_party"):
            body["context"]["thirdParty"] = {
                "embedUrl": "https://www.youtube.com/"
            }
        return body

    async def _post_innertube(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        client_key: str,
        payload: Dict[str, Any],
        deadline: _Deadline,
    ) -> Dict[str, Any]:
        url = (
            f"{INNERTUBE_BASE}/{endpoint}"
            f"?key={INNERTUBE_API_KEY}&prettyPrint=false"
        )
        async with session.post(
            url,
            json=payload,
            headers=self._innertube_headers(client_key),
            timeout=aiohttp.ClientTimeout(total=deadline.timeout(12.0)),
            proxy=self.proxy,
        ) as response:
            # 先吸收轮换再判状态码：4xx 响应里同样可能带着新的 Cookie。
            self.cookie_runtime.absorb_response(response)
            response.raise_for_status()
            data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError(f"Innertube {endpoint} 返回非对象响应")
        self._absorb_visitor_data(data)
        return data

    async def _fetch_oembed(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
        deadline: _Deadline,
    ) -> Dict[str, Any]:
        url = (
            "https://www.youtube.com/oembed?format=json&url="
            "https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D" + video_id
        )
        async with session.get(
            url,
            headers={
                "User-Agent": _WEB_USER_AGENT,
                "Accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=deadline.timeout(8.0)),
            proxy=self.proxy,
        ) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        return data if isinstance(data, dict) else {}

    async def _fetch_player_metadata(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
        deadline: _Deadline,
        failures: List[str],
    ) -> Dict[str, Any]:
        """只为补元数据而跑的 player 请求。

        出流客户端被门禁挡下时会连 videoDetails 一起吞掉，卡片就只剩一个
        标题（来自 oembed），时长、播放量全丢。TVHTML5_SIMPLY 在同样的门禁下
        仍然完整下发 videoDetails，所以专门跑它一趟把这些字段捞回来。
        返回第一个带 title 的响应，全失败时返回空 dict。
        """
        for client_key in METADATA_PLAYER_CLIENTS:
            if deadline.expired():
                failures.append(f"{client_key}(元数据) -> 跳过（总预算耗尽）")
                break
            body = self._innertube_body(client_key)
            body["videoId"] = video_id
            try:
                player = await self._post_innertube(
                    session, "player", client_key, body, deadline
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures.append(
                    f"{client_key}(元数据) -> {type(exc).__name__}: {exc}"
                )
                continue
            details = player.get("videoDetails")
            if isinstance(details, dict) and details.get("title"):
                return player
            failures.append(f"{client_key}(元数据) -> 无 videoDetails")
        return {}

    async def _fetch_player_light(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
        deadline: _Deadline,
        failures: List[str],
    ) -> Tuple[Dict[str, Any], str]:
        """只跑元数据客户端，并附上实际使用的客户端名称。"""
        player = await self._fetch_player_metadata(
            session, video_id, deadline, failures
        )
        details = player.get("videoDetails")
        if isinstance(details, dict) and details.get("title"):
            return player, METADATA_PLAYER_CLIENTS[0]
        return player, ""

    async def _fetch_watch_html(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
        deadline: _Deadline,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        url = f"https://www.youtube.com/watch?v={video_id}&hl=en&has_verified=1"
        headers = {
            "User-Agent": _WEB_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self._visitor_data:
            headers["X-Goog-Visitor-Id"] = self._visitor_data
        cookie_header = self.cookie_runtime.active_header()
        if cookie_header:
            headers["Cookie"] = cookie_header
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=deadline.timeout(15.0)),
            proxy=self.proxy,
        ) as response:
            self.cookie_runtime.absorb_response(response)
            response.raise_for_status()
            html = await response.text()
        matched = _VISITOR_DATA_RE.search(html)
        if matched:
            self._remember_visitor_data(matched.group(1))
        return parse_watch_html(html)

    async def _fetch_next(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
        deadline: _Deadline,
    ) -> Dict[str, Any]:
        body = self._innertube_body("web", hl="en")
        body["videoId"] = video_id
        return await self._post_innertube(
            session, "next", "web", body, deadline
        )

    async def _fetch_comments(
        self,
        session: aiohttp.ClientSession,
        token: str,
        deadline: _Deadline,
    ) -> Dict[str, Any]:
        body = self._innertube_body("web", hl="en")
        body["continuation"] = token
        return await self._post_innertube(
            session, "next", "web", body, deadline
        )

    # ── 解析主流程 ────────────────────────────────────────

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        async with self.semaphore:
            try:
                if self.browser_cookie_source is not None:
                    # Normal parses only read the recent profile snapshot. If a
                    # previous request proved it dead, give Chromium one chance
                    # to refresh the account session before falling back anonymous.
                    if self.cookie_runtime.alive is False:
                        wake_ok, wake_detail = (
                            await self.browser_cookie_source.wakeup()
                        )
                        logger.info(
                            "[youtube] 浏览器登录态恢复尝试: "
                            f"{wake_detail}"
                        )
                        await self._sync_browser_cookie(force=wake_ok)
                        if wake_ok and self.cookie_runtime.authenticated:
                            logged_in, detail = await self.cookie_runtime.keepalive(
                                session,
                                proxy=self.proxy,
                                timeout_seconds=20.0,
                            )
                            logger.info(
                                "[youtube] 浏览器登录态恢复复检: " + detail
                            )
                            if logged_in is True:
                                self.cookie_runtime.mark_alive()
                    else:
                        await self._sync_browser_cookie()
                return await self._parse(session, url)
            finally:
                # 解析途中吸收到的 Cookie 轮换在这里统一落盘，
                # 保证下次启动用的是服务端最新认可的那份凭据。
                await self.cookie_runtime.flush()

    async def keepalive_cookie(
        self,
        session: aiohttp.ClientSession,
        timeout_seconds: float = 20.0,
    ) -> Tuple[Optional[bool], str]:
        """主动跑一次 Cookie 体检请求，返回 (登录态, 可读摘要)。"""
        if self.browser_cookie_source is not None:
            wake_ok, wake_detail = await self.browser_cookie_source.wakeup()
            _synced, sync_detail = await self._sync_browser_cookie(
                force=wake_ok
            )
            if not self.cookie_runtime.authenticated:
                return False, f"唤醒: {wake_detail}；同步: {sync_detail}"
        return await self.cookie_runtime.keepalive(
            session,
            proxy=self.proxy,
            timeout_seconds=timeout_seconds,
        )

    async def maintain_cookie(
        self,
        session: aiohttp.ClientSession,
        timeout_seconds: float = 20.0,
        verify: bool = False,
    ) -> Tuple[Optional[bool], str]:
        """跑一轮 Cookie 维护（轮换 + 按需验证），返回 (登录态, 摘要)。

        浏览器里 __Secure-1PSIDTS 每十几分钟就换一次，凭据放着不动反而更容易
        被判失效。verify=False 时只做轻量轮换，需要确认登录态时再传 True。
        """
        if self.browser_cookie_source is not None:
            parts: List[str] = []
            should_verify = verify or self.cookie_runtime.alive is False
            if should_verify:
                wake_ok, wake_detail = await self.browser_cookie_source.wakeup()
                parts.append(f"浏览器唤醒: {wake_detail}")
            else:
                wake_ok = False
            _synced, sync_detail = await self._sync_browser_cookie(
                force=(should_verify and wake_ok)
            )
            parts.append(f"Profile 同步: {sync_detail}")
            if not self.cookie_runtime.authenticated:
                self.cookie_runtime.mark_dead("浏览器 Profile 缺少登录凭据")
                await self.cookie_runtime.flush()
                return False, "；".join(parts)
            if not should_verify:
                return None, "；".join(parts)

            logged_in, verify_detail = await self.cookie_runtime.keepalive(
                session,
                proxy=self.proxy,
                timeout_seconds=timeout_seconds,
            )
            parts.append(f"验证: {verify_detail}")
            if logged_in is True:
                self.cookie_runtime.mark_alive()
            elif logged_in is False:
                self.cookie_runtime.mark_dead("服务端判定浏览器登录态无效")
            await self.cookie_runtime.flush()
            return logged_in, "；".join(parts)

        return await self.cookie_runtime.maintain(
            session,
            proxy=self.proxy,
            timeout_seconds=timeout_seconds,
            verify=verify,
        )

    def cookie_status_line(self) -> str:
        """返回当前 Cookie 运行时状态摘要（不含任何取值）。"""
        return self.cookie_runtime.status_line()

    async def _parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        video_id = parse_youtube_identity(url)
        if not video_id:
            raise ValueError(f"无法从链接中解析 YouTube 视频 ID: {url}")

        started = time.time()
        deadline = _Deadline(self.total_budget_seconds)
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        failures: List[str] = []

        cookie_used = self.cookie_runtime.usable

        # 第 1 层：轻量元数据与 yt-dlp 取流并发启动。先处理 HTTP 元数据，
        # yt-dlp 在后台执行播放器 JS，随后与 next/comments 汇合。
        oembed_task = asyncio.ensure_future(
            self._fetch_oembed(session, video_id, deadline)
        )
        player_task = asyncio.ensure_future(
            self._fetch_player_light(session, video_id, deadline, failures)
        )
        ytdlp_task = asyncio.ensure_future(
            self._resolve_with_ytdlp(video_id)
        )
        oembed_result, player_result = await asyncio.gather(
            oembed_task, player_task, return_exceptions=True
        )

        oembed: Dict[str, Any] = {}
        if isinstance(oembed_result, dict):
            oembed = oembed_result
        elif isinstance(oembed_result, BaseException):
            if isinstance(oembed_result, asyncio.CancelledError):
                raise oembed_result
            failures.append(
                f"oembed -> {type(oembed_result).__name__}: {oembed_result}"
            )

        player: Dict[str, Any] = {}
        player_client = ""
        if isinstance(player_result, tuple):
            player, player_client = player_result
        elif isinstance(player_result, BaseException):
            if isinstance(player_result, asyncio.CancelledError):
                raise player_result
            failures.append(
                f"player -> {type(player_result).__name__}: {player_result}"
            )

        details = player.get("videoDetails")
        details = details if isinstance(details, dict) else {}
        initial_data: Optional[Dict[str, Any]] = None

        # 元数据兜底：Innertube 没给标题时抓 watch 页面内嵌 JSON。
        if not details.get("title") and not deadline.expired():
            try:
                html_player, initial_data = await self._fetch_watch_html(
                    session, video_id, deadline
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures.append(
                    f"watch_html -> {type(exc).__name__}: {exc}"
                )
            else:
                if isinstance(html_player, dict) and isinstance(
                    html_player.get("videoDetails"), dict
                ):
                    player = html_player
                    details = html_player["videoDetails"]
                    player_client = player_client or "web"
                else:
                    failures.append("watch_html -> 页面未内嵌可用 player 数据")

        ytdlp_stream: Optional[YtDlpStream] = None
        ytdlp_meta: Dict[str, Any] = {}
        if not (details.get("title") or oembed.get("title")):
            ytdlp_stream, ytdlp_meta = await ytdlp_task

        title = (
            str(details.get("title") or "")
            or str(oembed.get("title") or "")
            or str(ytdlp_meta.get("title") or "")
        )
        if not title:
            raise RuntimeError(
                "YouTube 元数据获取失败（"
                + ("; ".join(failures) if failures else "无更多信息")
                + "）"
            )

        media_url = ""
        media_kind = ""
        media_height = 0
        media_size_bytes = 0
        covers = thumbnail_candidates(video_id)
        oembed_cover = oembed.get("thumbnail_url")
        if isinstance(oembed_cover, str) and oembed_cover.startswith("http"):
            if oembed_cover not in covers:
                covers.append(oembed_cover)

        video_headers = build_request_headers(
            is_video=True,
            referer="https://www.youtube.com/",
            origin="https://www.youtube.com",
            user_agent=_WEB_USER_AGENT,
        )
        image_headers = build_request_headers(
            is_video=False,
            referer="https://www.youtube.com/",
            user_agent=_WEB_USER_AGENT,
        )

        author = str(details.get("author") or "") or str(
            oembed.get("author_name") or ""
        )
        channel_id = str(details.get("channelId") or "")
        desc = str(details.get("shortDescription") or "")
        length_seconds = _as_int(details.get("lengthSeconds"))
        view_count = _as_int(details.get("viewCount"))
        is_live = bool(
            details.get("isLive")
            or details.get("isLiveContent")
            and not length_seconds
        )
        avatar_url = ""
        like_count = 0
        comment_count = 0
        hot_comments: List[Dict[str, Any]] = []

        # 第 3 层：next 端点补齐头像、点赞、评论；失败只降级。
        next_payload: Dict[str, Any] = initial_data or {}
        if not deadline.expired():
            try:
                next_payload = await self._fetch_next(
                    session, video_id, deadline
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures.append(f"next -> {type(exc).__name__}: {exc}")

        login_state = detect_youtube_login_state(next_payload)

        if next_payload:
            owner_name, owner_avatar, owner_channel = extract_youtube_owner(
                next_payload
            )
            author = author or owner_name
            avatar_url = owner_avatar
            channel_id = channel_id or owner_channel
            like_count = extract_youtube_like_count(next_payload)
            comment_count = extract_youtube_comment_count(next_payload)
            if not view_count:
                # player 被门禁拦下时 videoDetails 缺失，播放量只能从
                # next 的 videoViewCountRenderer 里补。
                view_count = extract_youtube_view_count(next_payload)

            if self.hot_comment_count > 0 and not deadline.expired():
                token = find_comment_continuation(next_payload)
                if token:
                    try:
                        comment_payload = await self._fetch_comments(
                            session, token, deadline
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures.append(
                            f"comments -> {type(exc).__name__}: {exc}"
                        )
                    else:
                        hot_comments = extract_youtube_comments(
                            comment_payload, self.hot_comment_count
                        )
                        if not hot_comments:
                            failures.append("comments -> 未解析到可用评论")
                else:
                    failures.append("comments -> 未找到评论区 continuation")

        if not avatar_url:
            avatar_url = upscale_avatar_url(self._extract_avatar_url(player))

        # 汇合后台 yt-dlp 任务。播放器 JS、格式选择和签名还原都由它负责。
        ytdlp_stream, ytdlp_meta = await ytdlp_task
        ytdlp_detail = ""
        if ytdlp_stream is not None and ytdlp_stream.url:
            media_url = ytdlp_stream.url
            media_kind = ytdlp_stream.kind
            media_height = ytdlp_stream.height
            media_size_bytes = max(0, _as_int(ytdlp_stream.filesize))
            ytdlp_detail = ytdlp_stream.detail
            if ytdlp_stream.user_agent:
                # yt-dlp 的直链与它取链时用的 UA 绑定，换 UA 会被 403。
                video_headers = build_request_headers(
                    is_video=True,
                    referer="https://www.youtube.com/",
                    origin="https://www.youtube.com",
                    user_agent=ytdlp_stream.user_agent,
                )

        # Innertube 缺失的字段用同一趟 yt-dlp info 补齐；已有值一律不动。
        if ytdlp_meta:
            author = author or str(ytdlp_meta.get("author") or "")
            desc = desc or str(ytdlp_meta.get("description") or "")
            length_seconds = length_seconds or _as_int(
                ytdlp_meta.get("duration")
            )
            view_count = view_count or _as_int(ytdlp_meta.get("views"))
            like_count = like_count or _as_int(ytdlp_meta.get("likes"))
            comment_count = comment_count or _as_int(
                ytdlp_meta.get("comments")
            )
            ytdlp_cover = str(ytdlp_meta.get("cover") or "")
            if ytdlp_cover.startswith("http") and ytdlp_cover not in covers:
                covers.append(ytdlp_cover)

        timestamp = extract_youtube_publish_date(player, next_payload) or str(
            ytdlp_meta.get("publish_date") or ""
        )

        # playabilityStatus 只用于 Cookie 健康和失败诊断，不再参与选流。
        status = player.get("playabilityStatus")
        status_code = ""
        if isinstance(status, dict):
            status_code = str(status.get("status") or "")

        status_label = _PLAYABILITY_LABELS.get(status_code, "")
        restriction_label = (
            status_label if status_code in _GATED_STATUS_CODES else ""
        )

        # 无可下载流时退化为封面卡片，并说明原因。
        limit_warnings: List[str] = []
        if not media_url:
            if restriction_label:
                limit_warnings.append(f"{restriction_label}，仅展示封面与信息")
            elif is_live:
                limit_warnings.append("直播内容，仅展示封面与信息")
            else:
                limit_warnings.append("未取到可下载的视频流，仅展示封面与信息")

        # Googlevideo 的部分新直链会拒绝不带 Range 的完整 GET（403），但相同
        # URL 的字节范围请求正常返回 206。下载器已有可靠的 Range + 普通回退，
        # 这里为 YouTube 直链显式选择该路由；DASH 的音视频子流会分别加前缀。
        video_urls: List[List[str]] = (
            self._add_range_prefix_to_video_urls([[media_url]])
            if media_url
            else []
        )
        image_urls: List[List[str]] = [] if media_url else [list(covers)]
        video_cover_urls: List[List[str]] = (
            [list(covers)] if media_url else []
        )

        metadata: MediaMetadata = {
            "url": canonical,
            "source_url": canonical,
            "title": title,
            "author": author,
            "avatar_url": avatar_url,
            "desc": desc,
            "timestamp": timestamp,
            "platform": "youtube",
            "parser_name": self.name,
            "video_urls": video_urls,
            "image_urls": image_urls,
            "video_cover_urls": video_cover_urls,
            "image_headers": image_headers,
            "video_headers": video_headers,
            "video_force_download": bool(media_url),
            "timelength_ms": length_seconds * 1000,
            "hot_comments": hot_comments,
            "stats_line": build_youtube_stats_line(
                view_count, like_count, comment_count
            ),
            "use_image_proxy": bool(self.proxy),
            "use_video_proxy": bool(self.proxy),
            "proxy_url": self.proxy or "",
            "has_valid_media": bool(media_url or covers),
        }

        # 把估算体积交给下载器：超过可发送上限的直接跳过下载，省掉那趟白跑
        # 的一两分钟，并让卡片如实写出"为什么没有视频"。
        if media_url and media_size_bytes > 0:
            metadata["video_size_estimates"] = [
                media_size_bytes / 1024 / 1024
            ]

        if limit_warnings:
            metadata["limit_warnings"] = limit_warnings
            metadata["access_message"] = limit_warnings[0]
            if status_code and status_code != "OK":
                metadata["access_status"] = status_code
                metadata["restriction_label"] = status_label or status_code
                metadata["can_access_full_video"] = False

        metadata["youtube_video_id"] = video_id
        metadata["youtube_channel_id"] = channel_id
        metadata["youtube_stream_kind"] = media_kind
        metadata["youtube_player_client"] = player_client
        if media_url:
            stream_source_label = f"yt-dlp {ytdlp_detail}".strip()
            metadata["youtube_stream_source"] = "ytdlp"
        else:
            stream_source_label = "无"
            metadata["youtube_stream_source"] = "none"

        # 登录态诊断：Cookie 被服务端当成未登录时要显式告警，否则用户只会
        # 看到一张「仅展示封面」的卡片，日志里却毫无线索。但只在健康态真的
        # 由「有效」翻成「失效」的那一次开口——一旦判失效，后续请求已改走
        # 匿名链，再刷同一条告警只是噪音。
        cookie_expired = bool(
            cookie_used
            and (login_state is False or status_code == "LOGIN_REQUIRED")
        )
        cookie_flipped = False
        if cookie_expired:
            cookie_flipped = self.cookie_runtime.mark_dead(
                "被 YouTube 判为未登录"
            )
            if cookie_flipped:
                self._mark_cookie_alert(
                    "player_login_required"
                    if status_code == "LOGIN_REQUIRED"
                    else "innertube_logged_out"
                )
        elif cookie_used and login_state is True:
            # 服务端确认在线，把之前的失效判定收回来。
            self.cookie_runtime.mark_alive()

        chain = "; ".join(failures) if failures else "无"
        diagnosis = ", ".join(
            [
                f"video_id={video_id}",
                f"playability={status_code or self._NA}",
                f"元数据客户端={player_client or self._NA}",
                "取流=yt-dlp",
                f"登录态={self._login_label(cookie_expired)}",
                f"代理={self._proxy_label()}",
            ]
        )
        if limit_warnings:
            logger.warning(
                f"[youtube] 未取到可下载视频流，已降级为封面卡片: "
                f"{limit_warnings[0]}（{diagnosis}）"
                f"{self._gate_advice(status_code, cookie_flipped)}"
                f"{self._ytdlp_advice()}"
                f"；降级链: {chain}"
            )
        else:
            if cookie_flipped:
                logger.warning(
                    f"[youtube] 当前 Cookie 已被服务端视为未登录，"
                    f"后续请求改走匿名链（{diagnosis}）"
                    f"；处理建议: {self.cookie_recovery_hint()}"
                )
            if failures:
                logger.debug(
                    f"[youtube] 降级链: video_id={video_id}; {chain}"
                )
        size_label = (
            f"/{media_size_bytes / 1024 / 1024:.1f}MB"
            if media_size_bytes > 0
            else ""
        )
        logger.info(
            f"[youtube] 解析完成 video_id={video_id} "
            f"标题={title[:40]} 作者={author} "
            f"流={media_kind}@{media_height}p{size_label} "
            f"取流={stream_source_label} "
            f"client={player_client or self._NA} "
            f"热评={len(hot_comments)} 耗时={time.time() - started:.2f}s"
        )
        return metadata
