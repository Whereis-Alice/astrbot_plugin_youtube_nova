"""OneBot HTTP file sources and local paths without connecting to QQ."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from youtube_core.message_adapter.group_file import (
    GroupFileUploader,
    GroupFileUploadError,
)


def event_for(bot, *, private=False):
    return SimpleNamespace(
        bot=bot,
        get_group_id=lambda: "" if private else "123456",
        get_sender_id=lambda: "456789",
        get_platform_name=lambda: "aiocqhttp",
        is_private_chat=lambda: private,
    )


def setup_file_service(monkeypatch, callback_base):
    import astrbot.core
    from astrbot.core.file_token_service import FileTokenService

    service = FileTokenService()
    monkeypatch.setattr(
        astrbot.core, "astrbot_config", {"callback_api_base": callback_base}
    )
    monkeypatch.setattr(astrbot.core, "file_token_service", service)
    return service


@pytest.mark.parametrize("private", [False, True])
def test_onebot_fetches_http_source_and_receives_original_bytes(
    tmp_path, monkeypatch, private
):
    async def run():
        path = tmp_path / "video.mp4"
        original = bytes(range(256)) * 100
        path.write_bytes(original)
        service = setup_file_service(monkeypatch, "")

        async def download(request):
            local_path = await service.handle_file(request.match_info["token"])
            return web.FileResponse(local_path)

        app = web.Application()
        app.router.add_get("/api/file/{token}", download)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        callback_base = f"http://127.0.0.1:{port}"
        calls = []

        async def action(action_name, **payload):
            calls.append((action_name, payload))
            async with (
                aiohttp.ClientSession() as client,
                client.get(payload["file"]) as response,
            ):
                assert response.status == 200
                assert await response.read() == original
            return {"file_id": "uploaded"}

        try:
            uploader = GroupFileUploader(
                600, source_mode="http", callback_api_base=callback_base
            )
            event = event_for(SimpleNamespace(call_action=action), private=private)
            await uploader.upload(event, str(path), "original.mp4")
        finally:
            await runner.cleanup()
        assert len(calls) == 1
        assert calls[0][0] == (
            "upload_private_file" if private else "upload_group_file"
        )
        assert calls[0][1]["file"].startswith(callback_base + "/api/file/")
        assert calls[0][1]["name"] == "original.mp4"
        assert not service.staged_files

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["auto", "local"])
def test_missing_callback_uses_shared_local_path(tmp_path, monkeypatch, mode):
    setup_file_service(monkeypatch, "")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    bot = SimpleNamespace(call_action=AsyncMock(return_value={"file_id": "uploaded"}))
    asyncio.run(
        GroupFileUploader(source_mode=mode).upload(event_for(bot), str(path), path.name)
    )
    assert bot.call_action.await_args.kwargs["file"] == str(path.resolve())


def test_local_mode_ignores_configured_http_service(tmp_path, monkeypatch):
    service = setup_file_service(monkeypatch, "http://callback.example:6185")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    bot = SimpleNamespace(call_action=AsyncMock(return_value={"file_id": "uploaded"}))
    asyncio.run(
        GroupFileUploader(source_mode="local").upload(
            event_for(bot), str(path), path.name
        )
    )
    assert bot.call_action.await_args.kwargs["file"] == str(path.resolve())
    assert not service.staged_files


@pytest.mark.parametrize(
    "callback_base",
    [
        "",
        "ftp://callback.example",
        "http://host:invalid",
        "http://user:secret@host",
        "http://host/?secret=x",
    ],
)
def test_http_source_with_missing_or_invalid_callback_is_not_uploaded(
    tmp_path, monkeypatch, callback_base
):
    service = setup_file_service(monkeypatch, callback_base)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    bot = SimpleNamespace(call_action=AsyncMock())
    with pytest.raises(GroupFileUploadError):
        asyncio.run(
            GroupFileUploader(source_mode="http").upload(
                event_for(bot), str(path), path.name
            )
        )
    bot.call_action.assert_not_awaited()
    assert not service.staged_files


def test_auto_http_token_lives_through_upload_and_uncertain_retention(
    tmp_path, monkeypatch
):
    service = setup_file_service(monkeypatch, "http://callback.example:6185")
    service.register_file = AsyncMock(return_value="secret-token")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    bot = SimpleNamespace(call_action=AsyncMock(return_value={"file_id": "uploaded"}))
    asyncio.run(GroupFileUploader(1200).upload(event_for(bot), str(path), path.name))
    service.register_file.assert_awaited_once_with(str(path.resolve()), timeout=2400)
    assert (
        bot.call_action.await_args.kwargs["file"]
        == "http://callback.example:6185/api/file/secret-token"
    )


def test_callback_override_takes_precedence_and_source_url_is_redacted(
    tmp_path, monkeypatch
):
    service = setup_file_service(monkeypatch, "http://wrong.example:6185")
    service.register_file = AsyncMock(return_value="secret-token")
    source = "http://correct.example:6185/api/file/secret-token"
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    bot = SimpleNamespace(
        call_action=AsyncMock(side_effect=RuntimeError(f"{source} 下载失败"))
    )
    with pytest.raises(GroupFileUploadError) as caught:
        asyncio.run(
            GroupFileUploader(callback_api_base="http://correct.example:6185/").upload(
                event_for(bot), str(path), path.name
            )
        )
    assert "下载失败" in str(caught.value)
    assert "secret-token" not in str(caught.value)
    assert "correct.example" not in str(caught.value)
    assert bot.call_action.await_count == 1
