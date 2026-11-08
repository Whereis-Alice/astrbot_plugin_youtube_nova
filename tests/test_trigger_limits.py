import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from youtube_core.config_manager import ConfigManager


@pytest.mark.parametrize("value, expected", [(None, 3), ("invalid", 3), (0, 1), (-2, 1), (100, 20), ("5", 5)])
def test_link_limit_config_is_bounded(value, expected):
    cfg = ConfigManager({"trigger": {"max_links_per_message": value}})
    assert cfg.trigger.max_links_per_message == expected


@pytest.mark.parametrize("archive", [False, True])
def test_message_link_limit_applies_before_parsing_and_rate_accounting(tmp_path, archive):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent)) if str(root.parent) not in sys.path else None
    module = importlib.import_module(f"{root.name}.main")
    cls = module.YouTubeNovaPlugin
    plugin = cls.__new__(cls)
    cfg = ConfigManager({"trigger": {"max_links_per_message": 2}, "download": {"cache_dir": str(tmp_path)}})
    cfg.message.archive.command = "归档"
    cfg.permission.check = Mock(return_value=True)
    plugin.config_manager = cfg
    plugin.logger = Mock()
    plugin._start_expired_cache_cleanup = Mock()
    plugin._start_youtube_cookie_keepalive = Mock()
    plugin._trigger_youtube_cookie_notice_if_needed = Mock()
    plugin.youtube_cookie_notice = SimpleNamespace(try_update_admin_origin=Mock())
    parser = SimpleNamespace(name="youtube")
    links = [(f"https://www.youtube.com/watch?v=example{i:04}", parser) for i in range(4)]
    plugin.parser_manager = SimpleNamespace(extract_all_links=Mock(return_value=links), parse_text=AsyncMock(return_value=[]))
    plugin._try_extract_reply_links = Mock(return_value=(links, "quoted"))
    plugin.message_sender = SimpleNamespace(get_sender_info=Mock(return_value=("tester", "1")))
    records = SimpleNamespace(enabled=True, filter_links=Mock(return_value=(links[:2], [])), same_link=SimpleNamespace(enabled=False))
    plugin.parse_record_manager = records
    event = SimpleNamespace(
        message_str="归档" if archive else "\n".join(url for url, _ in links),
        message_obj=SimpleNamespace(message_id="message", message=[]),
        is_private_chat=lambda: True, get_sender_id=lambda: "1", get_self_id=lambda: "2",
        get_platform_name=lambda: "webchat", plain_result=lambda text: text, send=AsyncMock(),
    )
    asyncio.run(plugin.auto_parse(event))
    assert records.filter_links.call_args.args[0] == links[:2]
    assert plugin.parser_manager.parse_text.call_args.kwargs["links_with_parser"] == links[:2]
    assert any("最多解析 2 个链接" in call.args[0] for call in event.send.call_args_list)
