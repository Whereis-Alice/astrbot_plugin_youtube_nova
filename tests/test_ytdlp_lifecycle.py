"""取流超时、取消与 Cookie 串行访问的回归测试。"""

import asyncio
import threading
from pathlib import Path
from unittest import mock

from youtube_core.parser.platform.youtube import YouTubeParser
from youtube_core.parser.runtime_manager.youtube import ytdlp


def test_timeout_retains_gate_until_thread_exits_and_bounds_queue(tmp_path):
    async def run():
        resolver = ytdlp.YtDlpStreamResolver(cookie_dir=str(tmp_path))
        resolver.timeout = 0.03
        started = threading.Event()
        release = threading.Event()
        calls = []
        cookie_reads = []

        def extract(video_id, options):
            calls.append(video_id)
            if video_id == "first":
                started.set()
                assert release.wait(3)
                cookie_reads.append(
                    Path(options["cookiefile"]).read_text(encoding="utf-8")
                )
            return {"title": video_id}

        with mock.patch.object(
            ytdlp, "probe_ytdlp_environment",
            return_value=ytdlp.YtDlpEnvironment(available=True),
        ), mock.patch.object(resolver, "_extract_sync", side_effect=extract):
            try:
                first = asyncio.create_task(
                    resolver.resolve_full("first", cookie_header="SID=original")
                )
                assert await asyncio.to_thread(started.wait, 1)
                assert await asyncio.wait_for(first, 1) == (None, {})
                assert resolver._gate.locked()
                # 排队也有超时，并且不能改写运行中线程使用的 jar。
                assert await asyncio.wait_for(
                    resolver.resolve_full("second", cookie_header="SID=replacement"),
                    1,
                ) == (None, {})
                assert calls == ["first"]
            finally:
                release.set()
                if resolver._extraction_tasks:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *resolver._extraction_tasks, return_exceptions=True
                        ), 1
                    )
            assert "original" in cookie_reads[0]
            assert "replacement" not in cookie_reads[0]
            assert not resolver._gate.locked()
            # 线程收尾后下一次请求可以继续取流。
            _, info = await resolver.resolve_full("third")
            assert info == {"title": "third"}
            assert calls == ["first", "third"]

    asyncio.run(run())


def test_cancellation_returns_promptly_and_observes_late_thread_failure(tmp_path):
    async def run():
        resolver = ytdlp.YtDlpStreamResolver(cookie_dir=str(tmp_path))
        started = threading.Event()
        release = threading.Event()
        loop_errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: loop_errors.append(context))

        def extract(*_):
            started.set()
            assert release.wait(3)
            raise RuntimeError("late worker failure")

        with mock.patch.object(
            ytdlp, "probe_ytdlp_environment",
            return_value=ytdlp.YtDlpEnvironment(available=True),
        ), mock.patch.object(resolver, "_extract_sync", side_effect=extract):
            try:
                task = asyncio.create_task(resolver.resolve_full("first"))
                assert await asyncio.to_thread(started.wait, 1)
                task.cancel()
                result = await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True), 1
                )
                assert isinstance(result[0], asyncio.CancelledError)
                assert resolver._gate.locked()
            finally:
                release.set()
                if resolver._extraction_tasks:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *resolver._extraction_tasks, return_exceptions=True
                        ), 1,
                    )
                await asyncio.sleep(0)
                loop.set_exception_handler(previous_handler)
            assert not resolver._gate.locked()
            assert not resolver._extraction_tasks
            assert not loop_errors

    asyncio.run(run())


def test_parse_cancellation_closes_parallel_ytdlp_coroutine():
    async def run():
        parser = YouTubeParser()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def metadata(*_):
            await asyncio.Future()

        async def resolve(*_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        with mock.patch.object(
            parser, "_fetch_oembed", new=metadata
        ), mock.patch.object(
            parser, "_fetch_player_light", new=metadata
        ), mock.patch.object(parser, "_resolve_with_ytdlp", new=resolve):
            task = asyncio.create_task(
                parser._parse(object(), "https://youtu.be/dQw4w9WgXcQ")
            )
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            result = await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True), 1
            )
            assert isinstance(result[0], asyncio.CancelledError)
            assert stopped.is_set()

    asyncio.run(run())


def test_shutdown_waits_for_running_thread_and_cancels_queued_extracts(tmp_path):
    async def run():
        resolver = ytdlp.YtDlpStreamResolver(cookie_dir=str(tmp_path))
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        calls = []

        def extract(video_id, options):
            calls.append(video_id)
            started.set()
            assert release.wait(3)
            # 最后一次写入发生在shutdown返回之前，热重载不能越过它。
            Path(options["cookiefile"]).write_text("old worker done", encoding="utf-8")
            finished.set()
            return {}

        with mock.patch.object(
            ytdlp, "probe_ytdlp_environment",
            return_value=ytdlp.YtDlpEnvironment(available=True),
        ) as probe, mock.patch.object(
            resolver, "_extract_sync", side_effect=extract
        ):
            first = asyncio.create_task(
                resolver.resolve_full("first", cookie_header="SID=original")
            )
            assert await asyncio.to_thread(started.wait, 1)
            queued = asyncio.create_task(
                resolver.resolve_full("queued", cookie_header="SID=replacement")
            )
            await asyncio.sleep(0)
            shutdown = asyncio.create_task(resolver.shutdown())
            try:
                await asyncio.sleep(0)
                assert resolver._closed
                assert not shutdown.done()
                assert resolver._gate.locked()
                previous_calls = probe.call_count
                assert await resolver.resolve_full("after-close") == (None, {})
                assert probe.call_count == previous_calls
                assert calls == ["first"]
            finally:
                release.set()
                await asyncio.wait_for(shutdown, 1)
                await asyncio.gather(first, queued, return_exceptions=True)
            assert finished.is_set()
            assert not resolver._gate.locked()
            assert not resolver._extraction_tasks
            assert calls == ["first"]
            await resolver.shutdown()  # 重复关闭安全。

    asyncio.run(run())


def test_shutdown_cancellation_still_waits_for_cookie_writer(tmp_path):
    async def run():
        resolver = ytdlp.YtDlpStreamResolver(cookie_dir=str(tmp_path))
        started = threading.Event()
        release = threading.Event()

        def extract(*_):
            started.set()
            assert release.wait(3)
            return {}

        with mock.patch.object(
            ytdlp, "probe_ytdlp_environment",
            return_value=ytdlp.YtDlpEnvironment(available=True),
        ), mock.patch.object(resolver, "_extract_sync", side_effect=extract):
            first = asyncio.create_task(resolver.resolve_full("first"))
            assert await asyncio.to_thread(started.wait, 1)
            shutdown = asyncio.create_task(resolver.shutdown())
            await asyncio.sleep(0)
            shutdown.cancel()
            try:
                await asyncio.sleep(0)
                assert not shutdown.done()
                assert resolver._gate.locked()
            finally:
                release.set()
                result = await asyncio.wait_for(
                    asyncio.gather(shutdown, first, return_exceptions=True), 1
                )
            assert isinstance(result[0], asyncio.CancelledError)
            assert not resolver._gate.locked()
            assert not resolver._extraction_tasks

    asyncio.run(run())


def test_parser_close_does_not_create_lazy_resolver():
    async def run():
        parser = YouTubeParser()
        assert parser._ytdlp is None
        await parser.aclose()
        await parser.aclose()
        with mock.patch.object(parser, "_ytdlp_resolver") as create_resolver:
            assert await parser._resolve_with_ytdlp("dQw4w9WgXcQ") == (None, {})
            assert await parser.parse(object(), "https://youtu.be/dQw4w9WgXcQ") is None
            create_resolver.assert_not_called()
        assert parser._ytdlp is None

    asyncio.run(run())
