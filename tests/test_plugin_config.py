import json
from pathlib import Path

from youtube_core.config_manager import ConfigManager
from youtube_core.storage.parse_record import ParseRecordManager

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_builds_only_youtube_parser() -> None:
    config = ConfigManager({})

    parsers = config.create_parsers()

    assert config.parser_output.modes == {"youtube": "全部发送"}
    assert config.download.send_video_max_mb == 48.0
    assert config.download.oversize_delivery == "cover"
    assert config.download.transcode_mode == "above_size"
    assert len(parsers) == 1
    assert parsers[0].name == "youtube"
    assert parsers[0].can_parse("https://youtu.be/dQw4w9WgXcQ")


def test_group_file_and_compression_policy_are_independent() -> None:
    config = ConfigManager(
        {
            "download": {
                "max_video_size_mb": 800,
                "send_video_max_mb": 50,
                "oversize_delivery": "上传为群文件",
                "transcode_oversize_video": True,
                "transcode_mode": "始终压缩",
                "transcode_trigger_mb": 120,
                "transcode_target_size_mb": 80,
                "transcode_video_codec": "libx265",
                "transcode_preset": "medium",
                "transcode_max_height": 720,
                "transcode_max_fps": 24,
                "transcode_video_bitrate_kbps": 1800,
                "transcode_audio_bitrate_kbps": 96,
                "transcode_crf": 22,
                "transcode_max_attempts": 3,
                "transcode_extra_args": "-threads 2",
            }
        }
    )

    download = config.download
    assert download.group_file_enabled is True
    assert download.transcode_mode == "always"
    assert download.transcode_trigger_mb == 120
    assert download.transcode_target_size_mb == 80
    assert download.transcode_video_codec == "libx265"
    assert download.transcode_preset == "medium"
    assert download.transcode_max_height == 720
    assert download.transcode_max_fps == 24
    assert download.transcode_video_bitrate_kbps == 1800
    assert download.transcode_audio_bitrate_kbps == 96
    assert download.transcode_crf == 22
    assert download.transcode_max_attempts == 3
    assert download.transcode_extra_args == "-threads 2"
    assert download.stream_budget_mb == 800


def test_youtube_parser_can_be_disabled() -> None:
    config = ConfigManager({"parsers": {"youtube": "关闭"}})

    assert config.create_parsers() == []
    assert config.youtube_parser is None


def test_proxy_is_applied_to_parser_and_download_path() -> None:
    config = ConfigManager(
        {
            "proxy": {
                "address": "http://127.0.0.1:7890",
                "youtube": True,
            }
        }
    )

    parser = config.create_parsers()[0]

    assert config.proxy.youtube_use_proxy is True
    assert parser.proxy == "http://127.0.0.1:7890"


def test_schema_exposes_no_other_platform_parser_or_proxy() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))

    assert set(schema["parsers"]["items"]) == {"youtube"}
    assert set(schema["proxy"]["items"]) == {"address", "youtube"}
    assert set(schema["message"]["items"]["hot_comments"]["items"]) == {
        "count",
        "show_in_text",
        "youtube",
    }


def test_schema_exposes_only_the_ytdlp_stream_route() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
    youtube_items = schema["youtube"]["items"]

    assert "stream_source" not in youtube_items
    assert "player_clients" not in youtube_items
    assert "ytdlp_fallback" not in youtube_items
    assert "ytdlp_timeout" in youtube_items


def test_legacy_platform_skins_migrate_to_youtube() -> None:
    for legacy in ("B站卡片", "推特卡片", "哔哩哔哩", "twitter"):
        config = ConfigManager(
            {"message": {"card_render": {"skin": legacy}}}
        )
        assert config.message.card_render.skin == "youtube"


def test_youtube_share_parameters_do_not_bypass_link_rate_limit() -> None:
    first = ParseRecordManager.canonicalize_url(
        "https://youtu.be/dQw4w9WgXcQ?si=first&t=42"
    )
    second = ParseRecordManager.canonicalize_url(
        "https://youtu.be/dQw4w9WgXcQ?si=second"
    )

    assert first == second == "https://youtu.be/dQw4w9WgXcQ"
