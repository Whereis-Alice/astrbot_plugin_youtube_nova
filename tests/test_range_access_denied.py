import asyncio
from pathlib import Path
from unittest import mock

import pytest

from youtube_core.downloader.budget import ByteBudget
from youtube_core.downloader.handler import base


class Response:
    def __init__(self, status: int):
        self.status = status
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.parametrize("status", [401, 403])
def test_range_access_denial_preserves_status(status):
    async def run():
        with mock.patch.object(base, "safe_request", new=mock.AsyncMock(return_value=Response(status))):
            with pytest.raises(base._RangeAccessDenied) as caught:
                await base._download_range(mock.Mock(), "https://example.com/video", 0, 65535)
        assert caught.value.status == status

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 403])
def test_range_denial_stops_queued_requests_and_releases_budget(tmp_path: Path, status):
    async def run():
        calls = 0
        initial_batch_started = asyncio.Event()
        chunk_size = 65536
        file_size = 30 * chunk_size
        output = tmp_path / "video.mp4"
        budget = ByteBudget(file_size + 7)
        await budget.consume(7)

        async def request(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                initial_batch_started.set()
            await initial_batch_started.wait()
            return Response(status)

        with (
            mock.patch.object(base, "_get_file_size", new=mock.AsyncMock(return_value=file_size)),
            mock.patch.object(base, "safe_request", new=request),
        ):
            result = await base.range_download_file(
                mock.Mock(), "https://example.com/video", str(output),
                chunk_size=chunk_size, max_concurrent=4, budget=budget,
            )
        assert result is None
        assert calls == 4
        assert budget.used == 7
        assert not tmp_path.exists() or not list(tmp_path.iterdir())

    asyncio.run(run())


def test_non_permission_failure_preserves_existing_request_behavior(tmp_path: Path):
    async def run():
        chunk_size = 65536
        budget = ByteBudget(30 * chunk_size)
        with (
            mock.patch.object(base, "_get_file_size", new=mock.AsyncMock(return_value=30 * chunk_size)),
            mock.patch.object(base, "safe_request", new=mock.AsyncMock(return_value=Response(500))) as request,
        ):
            result = await base.range_download_file(
                mock.Mock(), "https://example.com/video", str(tmp_path / "video.mp4"),
                chunk_size=chunk_size, max_concurrent=4, budget=budget,
            )
        assert result is None
        assert request.await_count == 30
        assert budget.used == 0
        assert not tmp_path.exists() or not list(tmp_path.iterdir())

    asyncio.run(run())


def test_inflight_requests_finish_after_another_range_is_denied(tmp_path: Path):
    async def run():
        chunk_size = 65536
        calls = 0
        completed = []
        batch_started = asyncio.Event()
        denied = asyncio.Event()

        async def download(session, url, start, end, headers, proxy, index, total_size):
            nonlocal calls
            calls += 1
            if calls == 4:
                batch_started.set()
            await batch_started.wait()
            if index == 0:
                denied.set()
                raise base._RangeAccessDenied(403)
            await denied.wait()
            await asyncio.sleep(0)
            completed.append(index)
            return bytes(end - start + 1)

        budget = ByteBudget(30 * chunk_size)
        with (
            mock.patch.object(base, "_get_file_size", new=mock.AsyncMock(return_value=30 * chunk_size)),
            mock.patch.object(base, "_download_range", new=download),
        ):
            result = await base.range_download_file(
                mock.Mock(), "https://example.com/video", str(tmp_path / "video.mp4"),
                chunk_size=chunk_size, max_concurrent=4, budget=budget,
            )
        assert result is None
        assert calls == 4
        assert sorted(completed) == [1, 2, 3]
        assert budget.used == 0
        assert not tmp_path.exists() or not list(tmp_path.iterdir())

    asyncio.run(run())
