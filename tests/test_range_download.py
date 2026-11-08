import asyncio
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest

from youtube_core.constants import Config
from youtube_core.downloader.budget import ByteBudget
from youtube_core.downloader.handler import base


def test_range_download_accepts_a_single_small_chunk() -> None:
    payload = b"small youtube audio track"

    async def run() -> tuple[dict | None, bytes]:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audio.m4s"
            with (
                mock.patch.object(
                    base,
                    "_get_file_size",
                    new=mock.AsyncMock(return_value=len(payload)),
                ),
                mock.patch.object(
                    base,
                    "_download_range",
                    new=mock.AsyncMock(return_value=payload),
                ) as download,
            ):
                result = await base.range_download_file(
                    session=mock.Mock(),
                    url="https://example.com/audio",
                    output_path=str(output),
                    chunk_size=2 * 1024 * 1024,
                    max_concurrent=4,
                )
            download.assert_awaited_once()
            return result, output.read_bytes()

    result, written = asyncio.run(run())

    assert result is not None
    assert result["status_code"] == 206
    assert written == payload


def test_range_download_default_concurrency_is_conservative() -> None:
    assert Config.RANGE_DOWNLOAD_MAX_CONCURRENT == 4


@pytest.mark.parametrize("stage", ["prepare", "stat", "commit"])
@pytest.mark.parametrize("existing_output", [False, True])
def test_cancelled_range_download_reclaims_files_and_shared_budget(
    tmp_path: Path, stage: str, existing_output: bool
) -> None:
    """取消发生在线程内时也要收尾，且不能删除本次尚未覆盖的旧文件。"""

    async def run() -> None:
        output = tmp_path / "video.mp4"
        previous_content = b"existing download"
        if existing_output:
            output.write_bytes(previous_content)
        payload = b"new youtube stream"
        budget = ByteBudget(100)
        # 共享预算可能已经被另一音轨占用，只归还当前 Range 下载的预留。
        await budget.consume(7)
        started = asyncio.Event()
        release_worker = threading.Event()
        loop = asyncio.get_running_loop()
        target, attribute = {
            "prepare": (base, "_prepare_range_file"),
            "stat": (base.os.path, "getsize"),
            "commit": (base, "_replace_file"),
        }[stage]
        operation = getattr(target, attribute)

        def blocked_operation(*args, **kwargs):
            result = operation(*args, **kwargs)
            loop.call_soon_threadsafe(started.set)
            if not release_worker.wait(timeout=5):
                raise TimeoutError("test worker was not released")
            return result

        with (
            mock.patch.object(
                base, "_get_file_size", new=mock.AsyncMock(return_value=len(payload))
            ),
            mock.patch.object(
                base, "_download_range", new=mock.AsyncMock(return_value=payload)
            ),
            mock.patch.object(target, attribute, new=blocked_operation),
        ):
            task = asyncio.create_task(
                base.range_download_file(
                    mock.Mock(), "https://example.com/video", str(output), budget=budget
                )
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                task.cancel()
            finally:
                release_worker.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert budget.used == 7
        assert not list(tmp_path.glob("*.part"))
        if existing_output and stage != "commit":
            assert output.read_bytes() == previous_content
        else:
            assert not output.exists()

    asyncio.run(run())


def test_cancelled_range_download_waits_for_active_chunks(tmp_path: Path) -> None:
    async def run() -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()
        budget = ByteBudget(100)
        output = tmp_path / "video.mp4"

        async def download(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with (
            mock.patch.object(
                base, "_get_file_size", new=mock.AsyncMock(return_value=10)
            ),
            mock.patch.object(base, "_download_range", new=download),
        ):
            task = asyncio.create_task(
                base.range_download_file(
                    mock.Mock(), "https://example.com/video", str(output), budget=budget
                )
            )
            await asyncio.wait_for(started.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert stopped.is_set()
        assert budget.used == 0
        assert not tmp_path.exists() or not list(tmp_path.iterdir())

    asyncio.run(run())


def test_failed_range_download_preserves_previous_output(tmp_path: Path) -> None:
    async def run() -> None:
        output = tmp_path / "video.mp4"
        output.write_bytes(b"previous video")
        budget = ByteBudget(100)
        with (
            mock.patch.object(
                base, "_get_file_size", new=mock.AsyncMock(return_value=10)
            ),
            mock.patch.object(
                base, "_download_range", new=mock.AsyncMock(return_value=None)
            ),
        ):
            result = await base.range_download_file(
                mock.Mock(), "https://example.com/video", str(output), budget=budget
            )
        assert result is None
        assert budget.used == 0
        assert output.read_bytes() == b"previous video"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["video.mp4"]

    asyncio.run(run())


def test_successful_range_download_keeps_shared_budget_reservation(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        payload = b"youtube stream"
        output = tmp_path / "video.mp4"
        budget = ByteBudget(100)
        await budget.consume(7)
        with (
            mock.patch.object(
                base, "_get_file_size", new=mock.AsyncMock(return_value=len(payload))
            ),
            mock.patch.object(
                base, "_download_range", new=mock.AsyncMock(return_value=payload)
            ),
        ):
            result = await base.range_download_file(
                mock.Mock(), "https://example.com/video", str(output), budget=budget
            )
        assert result is not None
        assert output.read_bytes() == payload
        assert budget.used == 7 + len(payload)
        assert sorted(path.name for path in tmp_path.iterdir()) == ["video.mp4"]

    asyncio.run(run())
