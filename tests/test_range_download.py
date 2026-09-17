import asyncio
import tempfile
from pathlib import Path
from unittest import mock

from youtube_core.constants import Config
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
