"""完整音视频候选的预算比较与未知轨道体积。"""

import pytest

from youtube_core.parser.runtime_manager.youtube import YtDlpStreamResolver


def _format(name, size, *, video=False, audio=False, codec="mp4a", height=720):
    return {
        "format_id": name,
        "url": f"https://media.example/{name}",
        "protocol": "https",
        "vcodec": "avc1" if video else "none",
        "acodec": codec if audio else "none",
        "height": height if video else 0,
        "filesize": size,
        "ext": "mp4",
    }


def test_fitting_progressive_is_used_when_all_dash_pairs_exceed_budget():
    stream = YtDlpStreamResolver(max_bytes=10_000_000).select({"formats": [
        _format("video", 20_000_000, video=True),
        _format("audio", 1_000_000, audio=True),
        _format("progressive", 4_000_000, video=True, audio=True, height=360),
    ]})
    assert stream.kind == "progressive"
    assert stream.filesize == 4_000_000


def test_smaller_audio_can_make_dash_pair_fit_budget():
    stream = YtDlpStreamResolver(max_bytes=23_000_000).select({"formats": [
        _format("video", 22_000_000, video=True),
        _format("large-audio", 2_000_000, audio=True),
        _format("small-audio", 500_000, audio=True, codec="opus"),
    ]})
    assert stream.kind == "dash"
    assert "small-audio" in stream.url
    assert stream.filesize == 22_500_000


def test_audio_choice_preserves_highest_fitting_video_quality():
    stream = YtDlpStreamResolver(max_bytes=23_000_000).select({"formats": [
        _format("video1080", 22_000_000, video=True, height=1080),
        _format("video720", 15_000_000, video=True),
        _format("large-audio", 2_000_000, audio=True),
        _format("small-audio", 500_000, audio=True, codec="opus"),
    ]})
    assert stream.height == 1080
    assert "small-audio" in stream.url


@pytest.mark.parametrize("video_size,audio_size", [(0, 1_000_000), (20_000_000, 0)])
def test_unknown_track_size_makes_aggregate_unknown(video_size, audio_size):
    stream = YtDlpStreamResolver(max_bytes=23_000_000).select({"formats": [
        _format("video", video_size, video=True),
        _format("audio", audio_size, audio=True),
    ]})
    assert stream.kind == "dash"
    assert stream.filesize == 0


def test_known_partial_dash_size_still_participates_in_budget():
    stream = YtDlpStreamResolver(max_bytes=10_000_000).select({"formats": [
        _format("video", 20_000_000, video=True),
        _format("unknown-audio", 0, audio=True),
        _format("progressive", 4_000_000, video=True, audio=True),
    ]})
    assert stream.kind == "progressive"


def test_all_oversize_candidates_keep_smallest_complete_stream():
    stream = YtDlpStreamResolver(max_bytes=1_000_000).select({"formats": [
        _format("video", 20_000_000, video=True),
        _format("audio", 1_000_000, audio=True),
        _format("progressive", 4_000_000, video=True, audio=True),
    ]})
    assert stream.kind == "progressive"
    assert stream.filesize == 4_000_000


def test_fitting_dash_is_still_preferred_over_progressive():
    stream = YtDlpStreamResolver(max_bytes=30_000_000).select({"formats": [
        _format("video", 20_000_000, video=True),
        _format("audio", 1_000_000, audio=True),
        _format("progressive", 4_000_000, video=True, audio=True),
    ]})
    assert stream.kind == "dash"
    assert stream.filesize == 21_000_000
