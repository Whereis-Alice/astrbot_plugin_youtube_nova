"""yt-dlp 兼容补丁的回归测试。

覆盖 yt-dlp issue #14826：embed 页被取成空页时 ``_fix_embedded_ytcfg({})``
会抛出 ``KeyError('INNERTUBE_CONTEXT')`` 并掀翻整条取流。补丁后空 ytcfg 应安静
跳过，合法 ytcfg 行为保持与原版一致。
"""

import pytest

from youtube_core.parser.runtime_manager.youtube import ytdlp as ytdlp_mod


def _youtube_base():
    """没装 yt-dlp（或内部结构变动）时跳过，避免误判为失败。"""
    return pytest.importorskip("yt_dlp.extractor.youtube._base")


def _apply_fresh():
    """清掉模块级“已打补丁”状态位后再重打一次补丁。

    全量套件里其它用例会把假的 ``yt_dlp`` 塞进 ``sys.modules`` 再调用取流探测，
    导致本模块的 ``_YTDLP_PATCHED`` 被置真却没真正包裹真实函数。这里显式清零，
    让补丁针对还原后的真实 yt_dlp 重新评估，使测试不依赖套件执行顺序。
    """
    ytdlp_mod._YTDLP_PATCHED = False
    ytdlp_mod._apply_ytdlp_compat_patches()


def test_patch_guards_empty_ytcfg():
    yt_base = _youtube_base()
    _apply_fresh()
    fix = yt_base._fix_embedded_ytcfg
    assert getattr(fix, "_nova_guarded", False) is True
    # 空 / 非 dict 的 ytcfg 过去会 KeyError，补丁后必须安静跳过并返回 None。
    assert fix({}) is None
    assert fix(None) is None


def test_patch_preserves_valid_ytcfg():
    yt_base = _youtube_base()
    _apply_fresh()
    ytcfg = {"INNERTUBE_CONTEXT": {}}
    yt_base._fix_embedded_ytcfg(ytcfg)
    # 合法 ytcfg 仍应被原逻辑注入 thirdParty.embedUrl。
    assert ytcfg["INNERTUBE_CONTEXT"]["thirdParty"]["embedUrl"]


def test_patch_is_idempotent():
    yt_base = _youtube_base()
    _apply_fresh()
    first = yt_base._fix_embedded_ytcfg
    _apply_fresh()
    second = yt_base._fix_embedded_ytcfg
    # 重复调用不能层层包裹，必须仍是同一个 guarded 函数。
    assert first is second
    assert getattr(second, "_nova_guarded", False) is True
