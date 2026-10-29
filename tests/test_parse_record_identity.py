"""YouTube 视频身份限流与已有记录的兼容性。"""

import json
from types import SimpleNamespace

import pytest

from youtube_core.storage.parse_record import ParseRecordManager

VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PARSER = SimpleNamespace(name="youtube")


@pytest.mark.parametrize("url", [
    f"https://youtu.be/{VIDEO_ID}?si=share",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://m.youtube.com/watch?v={VIDEO_ID}&list=playlist&index=4",
    f"https://www.youtube.com/watch?v={VIDEO_ID}&arbitrary=anything",
    f"https://www.youtube.com/embed/{VIDEO_ID}",
])
def test_aliases_and_arbitrary_parameters_share_one_key(url):
    assert ParseRecordManager.build_link_key(url, "youtube") == (
        f"youtube:{CANONICAL}"
    )


def test_repeated_video_with_different_query_is_blocked(tmp_path):
    manager = ParseRecordManager(
        record_file=str(tmp_path / "records.json"),
        same_link_max_count=1,
        same_link_window_seconds=60,
    )
    first = f"{CANONICAL}&arbitrary=first"
    second = f"https://youtu.be/{VIDEO_ID}?arbitrary=second"
    allowed, blocked = manager.filter_links(
        [(first, PARSER)], user_key="aiocqhttp:123", now=100
    )
    assert allowed and not blocked
    allowed, blocked = manager.filter_links(
        [(second, PARSER)], user_key="aiocqhttp:456", now=101
    )
    assert not allowed
    assert blocked[0].scope == "link"


def test_existing_canonical_record_still_blocks_share_link(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps({
        "version": 1,
        "links": {f"youtube:{CANONICAL}": [100]},
        "users": {},
        "updated_at": 100,
    }), encoding="utf-8")
    manager = ParseRecordManager(
        record_file=str(path), same_link_max_count=1,
        same_link_window_seconds=60,
    )
    allowed, blocked = manager.filter_links(
        [(f"https://youtu.be/{VIDEO_ID}", PARSER)],
        user_key="aiocqhttp:123", now=101,
    )
    assert not allowed and blocked


def test_non_youtube_keys_keep_query_semantics():
    assert ParseRecordManager.build_link_key(
        "https://example.com/watch?v=one&other=two", "other"
    ) == "other:https://example.com/watch?other=two&v=one"
