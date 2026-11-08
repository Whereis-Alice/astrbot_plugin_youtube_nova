"""用小范围真实读取识别只允许头部访问的 Googlevideo 直链。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from ....downloader.handler.base import _parse_content_range
from ....downloader.security import safe_request
from ...utils import build_request_headers
from .ytdlp import YtDlpStream, YtDlpStreamResolver, _has_audio, _has_video, _is_direct


_PROBE_BYTES = 1024
_MAX_CANDIDATES = 3
_PROBE_TIMEOUT_SECONDS = 12.0
_FAILURE_NOTE = "视频流预检未通过（访问受限或超过体积上限），仅展示封面与信息"


def stream_request_headers(stream: YtDlpStream) -> dict[str, str]:
    """预检和实际下载使用相同的非凭据请求头。"""
    if stream.headers:
        return dict(stream.headers)
    headers = build_request_headers(
        is_video=True, referer="https://www.youtube.com/",
        origin="https://www.youtube.com", user_agent=stream.user_agent or None,
    )
    headers["Accept-Encoding"] = "identity"
    return headers


def _track_urls(stream: YtDlpStream) -> list[str]:
    payload = stream.url[5:] if stream.url.startswith("dash:") else stream.url
    return [part.removeprefix("range:") for part in payload.split("||") if part]


def _googlevideo_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme in {"http", "https"}
            and parsed.username is None and parsed.password is None
            and (host == "googlevideo.com" or host.endswith(".googlevideo.com"))
        )
    except ValueError:
        return False


def needs_media_probe(stream: YtDlpStream) -> bool:
    urls = _track_urls(stream)
    return bool(urls) and all(_googlevideo_url(url) for url in urls)


async def _probe_range(
    session: aiohttp.ClientSession, url: str, start: int, end: int,
    headers: dict[str, str], proxy: str | None, total: int | None = None,
) -> int | None:
    request_headers = dict(headers)
    request_headers["Range"] = f"bytes={start}-{end}"
    expected = end - start + 1
    response = await safe_request(
        session, "GET", url, headers=request_headers, proxy=proxy,
        timeout=aiohttp.ClientTimeout(total=5),
    )
    async with response:
        if response.status != 206:
            return None
        parsed = _parse_content_range(response.headers.get("Content-Range"))
        if (
            not parsed or parsed[:2] != (start, end)
            or (total is not None and parsed[2] != total)
        ):
            return None
        received = 0
        async for data in response.content.iter_chunked(min(_PROBE_BYTES, expected + 1)):
            received += len(data)
            if received > expected:
                return None
        return parsed[2] if received == expected else None


async def probe_stream(
    session: aiohttp.ClientSession, stream: YtDlpStream, *, proxy: str | None = None,
    max_bytes: int = 0,
) -> int | None:
    """检查头、中部、尾部各少量字节；返回真实合计大小，失败返回 None。"""
    total_bytes = 0
    headers = stream_request_headers(stream)
    try:
        for url in _track_urls(stream):
            size = await _probe_range(session, url, 0, 0, headers, proxy)
            if size is None:
                return None
            total_bytes += size
            if max_bytes > 0 and total_bytes > max_bytes:
                return None
            positions = {
                (max(0, size // 2 - _PROBE_BYTES // 2), min(size - 1, size // 2 + _PROBE_BYTES // 2 - 1)),
                (max(0, size - _PROBE_BYTES), size - 1),
            }
            for start, end in sorted(positions):
                if await _probe_range(session, url, start, end, headers, proxy, size) is None:
                    return None
        return total_bytes or None
    except asyncio.CancelledError:
        raise
    except Exception:
        # 请求异常可能携带签名、Cookie 或代理 URL，不能把原文写进诊断。
        return None


async def select_verified_stream(
    session: aiohttp.ClientSession, resolver: YtDlpStreamResolver,
    selected: YtDlpStream, info: dict[str, Any], *, proxy: str | None = None,
    timeout_seconds: float = _PROBE_TIMEOUT_SECONDS,
) -> tuple[YtDlpStream | None, str]:
    """最多验证原格式和两个单文件候选，复用同次 info，不再发起取流。"""
    if not needs_media_probe(selected):
        return selected, ""
    formats = info.get("formats")
    remaining = [
        fmt for fmt in formats
        if _is_direct(fmt) and _has_video(fmt) and _has_audio(fmt)
        and _googlevideo_url(fmt["url"])
    ] if isinstance(formats, list) else []
    tried: set[str] = set()
    candidate = selected
    try:
        async with asyncio.timeout(timeout_seconds):
            for attempt in range(_MAX_CANDIDATES):
                tried.add(candidate.url)
                size = await probe_stream(
                    session, candidate, proxy=proxy, max_bytes=resolver.max_bytes,
                )
                if size is not None:
                    note = (
                        f"高画质流访问受限，已回退到 {candidate.height}p 可下载视频"
                        if attempt and candidate.height else
                        "原视频流访问受限，已回退到可下载视频" if attempt else ""
                    )
                    return replace(candidate, filesize=size), note
                remaining = [fmt for fmt in remaining if fmt["url"] not in tried]
                candidate = resolver.select({**info, "formats": remaining})
                if candidate is None:
                    break
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return None, "视频流预检超时，仅展示封面与信息"
    return None, _FAILURE_NOTE
