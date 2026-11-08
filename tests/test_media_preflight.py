import asyncio
import re
from dataclasses import replace
from unittest import mock

import pytest

from youtube_core.parser.runtime_manager.youtube import media_probe
from youtube_core.parser.runtime_manager.youtube.ytdlp import (
    YtDlpStream, YtDlpStreamResolver, _format_headers,
)


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body
        self.content = self
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


def fmt(name, *, video=True, audio=False, height=1080, size=10000):
    return {
        "format_id": name, "url": f"https://rr1.googlevideo.com/{name}?sig=PRIVATE",
        "protocol": "https", "ext": "mp4", "height": height,
        "vcodec": "avc1" if video else "none",
        "acodec": "mp4a" if audio else "none", "filesize": size,
        "http_headers": {"User-Agent": "original-agent", "Sec-Fetch-Mode": "navigate"},
    }


def responses(monkeypatch, formats, *, reject=None, mutate=None):
    sizes = {f["url"]: f["filesize"] for f in formats}
    calls, returned = [], []

    async def request(session, method, url, **kwargs):
        span = re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs["headers"]["Range"])
        start, end = map(int, span.groups())
        calls.append((url, start, end, kwargs))
        if reject and reject(url, start, end):
            response = Response(403, {}, b"")
        else:
            response = Response(206, {"Content-Range": f"bytes {start}-{end}/{sizes[url]}"}, b"x" * (end - start + 1))
        if mutate:
            mutate(response, start, end)
        returned.append(response)
        return response

    monkeypatch.setattr(media_probe, "safe_request", request)
    return calls, returned


def test_dash_audio_tail_denied_falls_back_to_complete_file(monkeypatch):
    video, audio, complete = fmt("video"), fmt("audio", video=False, audio=True, size=5000), fmt("18", audio=True, height=360, size=7000)
    info = {"formats": [video, audio, complete]}
    resolver = YtDlpStreamResolver(max_bytes=20000)
    selected = resolver.select(info)
    calls, returned = responses(monkeypatch, info["formats"], reject=lambda url, _start, end: url == audio["url"] and end == audio["filesize"] - 1)
    stream, note = asyncio.run(media_probe.select_verified_stream(object(), resolver, selected, info))
    assert stream.kind == "progressive" and stream.url == complete["url"]
    assert stream.filesize == complete["filesize"]
    assert "360p" in note and "回退" in note
    assert any(url == audio["url"] and start == end == 0 for url, start, end, _ in calls)
    assert len(calls) == 9
    assert all(response.closed for response in returned)


def test_healthy_dash_keeps_quality_and_actual_sum(monkeypatch):
    formats = [fmt("video"), fmt("audio", video=False, audio=True, size=5000)]
    resolver = YtDlpStreamResolver()
    selected = replace(resolver.select({"formats": formats}), filesize=1)
    calls, _ = responses(monkeypatch, formats)
    stream, note = asyncio.run(media_probe.select_verified_stream(object(), resolver, selected, {"formats": formats}, proxy="http://proxy.invalid:1234"))
    assert stream.kind == "dash" and stream.height == 1080 and stream.filesize == 15000
    assert note == "" and len(calls) == 6
    assert all(call[3]["proxy"] == "http://proxy.invalid:1234" for call in calls)
    assert all(call[3]["headers"]["Accept-Encoding"] == "identity" for call in calls)
    assert all(call[3]["headers"]["Sec-Fetch-Mode"] == "navigate" for call in calls)


@pytest.mark.parametrize("problem", ["status", "range", "total", "short", "long"])
def test_invalid_sample_is_rejected_and_closed(monkeypatch, problem):
    formats = [fmt("18", audio=True, height=360)]
    stream = YtDlpStreamResolver().select({"formats": formats})

    def mutate(response, start, end):
        if start == end == 0:
            return
        if problem == "status":
            response.status = 200
        elif problem == "range":
            response.headers["Content-Range"] = "bytes 0-0/10000"
        elif problem == "total":
            response.headers["Content-Range"] = f"bytes {start}-{end}/20000"
        elif problem == "short":
            response.body = response.body[:-1]
        else:
            response.body += b"extra"

    _, returned = responses(monkeypatch, formats, mutate=mutate)
    assert asyncio.run(media_probe.probe_stream(object(), stream)) is None
    assert all(response.closed for response in returned)


def test_actual_size_limit_stops_after_head_probe(monkeypatch):
    formats = [fmt("18", audio=True, height=360)]
    stream = YtDlpStreamResolver().select({"formats": formats})
    calls, _ = responses(monkeypatch, formats)
    assert asyncio.run(media_probe.probe_stream(object(), stream, max_bytes=5000)) is None
    assert len(calls) == 1


def test_all_candidates_fail_without_unverified_url(monkeypatch):
    formats = [fmt("video"), fmt("audio", video=False, audio=True), fmt("18", audio=True, height=360)]
    resolver = YtDlpStreamResolver()
    calls, _ = responses(monkeypatch, formats, reject=lambda *_: True)
    stream, note = asyncio.run(media_probe.select_verified_stream(object(), resolver, resolver.select({"formats": formats}), {"formats": formats}))
    assert stream is None and "封面" in note and len(calls) == 2


def test_only_three_distinct_candidates_are_probed(monkeypatch):
    formats = [fmt(str(i), audio=True, height=1080 - i * 100) for i in range(6)]
    resolver, seen = YtDlpStreamResolver(), []

    async def deny(session, candidate, **kwargs):
        seen.append(candidate.url)

    monkeypatch.setattr(media_probe, "probe_stream", deny)
    stream, _ = asyncio.run(media_probe.select_verified_stream(object(), resolver, resolver.select({"formats": formats}), {"formats": formats}))
    assert stream is None and len(seen) == len(set(seen)) == 3


def test_fallback_respects_height_and_budget(monkeypatch):
    formats = [fmt("video", size=4000), fmt("audio", video=False, audio=True, size=1000), fmt("huge", audio=True, height=720, size=30000), fmt("tall", audio=True, height=2160, size=1000), fmt("18", audio=True, height=360, size=2000)]
    resolver = YtDlpStreamResolver(max_height=1080, max_bytes=8000)
    calls, _ = responses(monkeypatch, formats, reject=lambda url, *_: url != formats[-1]["url"])
    stream, _ = asyncio.run(media_probe.select_verified_stream(object(), resolver, resolver.select({"formats": formats}), {"formats": formats}))
    assert stream.url == formats[-1]["url"]
    assert not any(url in {formats[2]["url"], formats[3]["url"]} for url, *_ in calls)


def test_timeout_returns_no_unverified_stream(monkeypatch):
    format18, resolver = fmt("18", audio=True, height=360), YtDlpStreamResolver()

    async def block(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(media_probe, "probe_stream", block)
    stream, note = asyncio.run(media_probe.select_verified_stream(object(), resolver, resolver.select({"formats": [format18]}), {"formats": [format18]}, timeout_seconds=0.01))
    assert stream is None and "超时" in note


def test_cancellation_propagates_and_cleans_probe(monkeypatch):
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()
        format18, resolver = fmt("18", audio=True, height=360), YtDlpStreamResolver()

        async def block(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(media_probe, "probe_stream", block)
        task = asyncio.create_task(media_probe.select_verified_stream(object(), resolver, resolver.select({"formats": [format18]}), {"formats": [format18]}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(run())


def test_other_domains_and_lookalikes_are_not_contacted(monkeypatch):
    probe = mock.AsyncMock()
    monkeypatch.setattr(media_probe, "probe_stream", probe)
    resolver = YtDlpStreamResolver()
    for url in ["https://media.example/test", "https://googlevideo.com.evil.example/test"]:
        stream = YtDlpStream(url, "progressive")
        result, _ = asyncio.run(media_probe.select_verified_stream(object(), resolver, stream, {}))
        assert result is stream
    probe.assert_not_awaited()


def test_format_headers_preserve_contract_without_credentials():
    headers = _format_headers({"http_headers": {
        "User-Agent": "original", "Sec-Fetch-Mode": "navigate",
        "Cookie": "test-cookie", "Authorization": "test-auth",
        "Proxy-Authorization": "test-proxy", "Host": "private.internal",
        "Connection": "keep-alive", "Content-Length": "3", "Range": "bytes=0-10",
        "Transfer-Encoding": "chunked", "Accept-Encoding": "gzip",
    }})
    assert headers == {"User-Agent": "original", "Sec-Fetch-Mode": "navigate", "Accept-Encoding": "identity"}


def test_resolver_keeps_sanitized_format_headers():
    source = fmt("18", audio=True, height=360)
    source["http_headers"]["Cookie"] = "test-cookie"
    stream = YtDlpStreamResolver().select({"formats": [source]})
    assert stream.headers["Sec-Fetch-Mode"] == "navigate"
    assert "Cookie" not in stream.headers


def test_parser_fallback_reuses_info_without_second_extraction(monkeypatch):
    from youtube_core.parser.platform.youtube import YouTubeParser

    formats = [fmt("video"), fmt("audio", video=False, audio=True), fmt("18", audio=True, height=360)]
    resolver = YtDlpStreamResolver()
    info = {"formats": formats, "title": "original title"}
    selected = resolver.select(info)
    resolver.resolve_full = mock.AsyncMock(return_value=(selected, info))
    responses(monkeypatch, formats, reject=lambda url, *_: url != formats[-1]["url"])
    parser = YouTubeParser()
    with mock.patch.object(parser, "_ytdlp_resolver", return_value=resolver):
        stream, summary = asyncio.run(parser._resolve_with_ytdlp("JfMwrHFTvg8", object()))
    assert stream.kind == "progressive" and stream.height == 360
    assert summary["title"] == "original title"
    assert "360p" in summary["_stream_warning"]
    assert resolver.resolve_full.await_count == 1


def test_parser_output_explains_fallback_and_uses_verified_headers():
    from youtube_core.parser.platform.youtube import YouTubeParser

    parser, resolver = YouTubeParser(), YtDlpStreamResolver()
    complete = resolver.select({"formats": [fmt("18", audio=True, height=360)]})
    warning = "高画质流访问受限，已回退到 360p 可下载视频"
    player = {"videoDetails": {"title": "video", "author": "author", "lengthSeconds": "60"}, "playabilityStatus": {"status": "OK"}}
    with (
        mock.patch.object(parser, "_fetch_oembed", new=mock.AsyncMock(return_value={})),
        mock.patch.object(parser, "_fetch_player_light", new=mock.AsyncMock(return_value=(player, "web"))),
        mock.patch.object(parser, "_fetch_next", new=mock.AsyncMock(return_value={})),
        mock.patch.object(parser, "_resolve_with_ytdlp", new=mock.AsyncMock(return_value=(complete, {"_stream_warning": warning}))),
        mock.patch("youtube_core.parser.platform.youtube.logger.warning") as log_warning,
    ):
        metadata = asyncio.run(parser._parse(object(), "https://youtu.be/JfMwrHFTvg8"))
    assert warning in metadata["limit_warnings"]
    assert metadata["access_message"] == warning
    assert metadata["video_headers"] == media_probe.stream_request_headers(complete)
    logged = " ".join(str(call.args[0]) for call in log_warning.call_args_list)
    assert warning in logged
    assert "降级为封面卡片" not in logged


def test_malformed_header_field_is_safely_ignored():
    assert _format_headers({"http_headers": "not a mapping"}) == {"Accept-Encoding": "identity"}
