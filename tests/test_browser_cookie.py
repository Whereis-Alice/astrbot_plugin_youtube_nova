import asyncio
import time
from http.cookiejar import Cookie
from unittest import mock

from youtube_core.config_manager import ConfigManager
from youtube_core.parser.platform.youtube import YouTubeParser
from youtube_core.parser.runtime_manager.youtube import (
    BrowserCookieSnapshot,
    BrowserCookieSource,
    BrowserCookieSpec,
    YouTubeCookieRuntime,
    YtDlpEnvironment,
    YtDlpStreamResolver,
)
from youtube_core.parser.runtime_manager.youtube import (
    browser_cookie as browser_runtime,
)
from youtube_core.parser.runtime_manager.youtube import ytdlp as ytdlp_runtime


def _cookie(
    name: str,
    value: str,
    domain: str = ".youtube.com",
    path: str = "/",
    expires: int | None = None,
) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=True,
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _snapshot(header: str = "SAPISID=fresh; SID=session") -> BrowserCookieSnapshot:
    return BrowserCookieSnapshot(
        header=header,
        cookie_count=2,
        names=("SAPISID", "SID"),
        fingerprint="snapshot-a",
        fetched_at=time.time(),
    )


def test_browser_spec_normalizes_ytdlp_tuple_and_profile_layout() -> None:
    spec = BrowserCookieSpec(
        browser=" Chromium ",
        profile="/root/.config/chromium/Default",
        keyring="basictext",
        display=":10.0",
    )

    assert spec.ytdlp_tuple() == (
        "chromium",
        "/root/.config/chromium/Default",
        "BASICTEXT",
        None,
    )
    assert spec.chromium_profile_args() == (
        "/root/.config/chromium",
        "Default",
    )


def test_snapshot_filters_domains_expiry_and_duplicate_paths() -> None:
    future = int(time.time()) + 3600
    past = int(time.time()) - 3600
    jar = [
        _cookie("SAPISID", "narrow", domain="www.youtube.com", path="/watch"),
        _cookie("SAPISID", "broad", expires=future),
        _cookie("SID", "session"),
        _cookie("EXPIRED", "old", expires=past),
        _cookie("GOOGLE", "ignored", domain=".google.com"),
        _cookie("BAD", "line break\nignored"),
    ]
    with mock.patch(
        "yt_dlp.cookies.extract_cookies_from_browser",
        return_value=jar,
    ) as extract:
        snapshot = browser_runtime._extract_snapshot(
            BrowserCookieSpec(browser="chromium", profile="Default")
        )

    assert snapshot.authenticated is True
    assert snapshot.cookie_count == 2
    assert snapshot.header == "SAPISID=broad; SID=session"
    extract.assert_called_once()


def test_browser_source_throttles_repeated_profile_reads() -> None:
    source = BrowserCookieSource(
        BrowserCookieSpec(browser="chromium"),
        refresh_seconds=60,
    )

    async def run_twice():
        first = await source.refresh()
        second = await source.refresh()
        return first, second

    with mock.patch.object(
        browser_runtime,
        "_extract_snapshot",
        return_value=_snapshot(),
    ) as extract:
        first, second = asyncio.run(run_twice())

    assert first[1] is True
    assert second[1] is False
    assert first[0] is second[0]
    extract.assert_called_once()


def test_same_browser_snapshot_does_not_overwrite_response_rotation() -> None:
    runtime = YouTubeCookieRuntime("")
    assert runtime.replace_from_source(
        "SAPISID=browser; SIDCC=old",
        source_fingerprint="profile-a",
    )
    runtime.absorb(["SIDCC=fresh; Path=/"])
    assert "SIDCC=fresh" in runtime.header()

    assert not runtime.replace_from_source(
        "SAPISID=browser; SIDCC=old",
        source_fingerprint="profile-a",
    )
    assert "SIDCC=fresh" in runtime.header()

    assert runtime.replace_from_source(
        "SAPISID=browser-new; SIDCC=new",
        source_fingerprint="profile-b",
    )
    assert "SIDCC=new" in runtime.header()
    assert "SIDCC=fresh" not in runtime.header()


def test_browser_wakeup_preserves_an_existing_profile_process() -> None:
    source = BrowserCookieSource(
        BrowserCookieSpec(
            browser="chromium",
            profile="/root/.config/chromium/Default",
        ),
        wakeup_mode="有头（推荐）",
    )

    with (
        mock.patch.object(
            source, "_browser_executable", return_value="/usr/bin/chromium"
        ),
        mock.patch.object(source, "_profile_process_running", return_value=True),
        mock.patch("asyncio.create_subprocess_exec") as spawn,
    ):
        ok, detail = asyncio.run(source.wakeup())

    assert ok is True
    assert "保留现有进程" in detail
    spawn.assert_not_called()


def test_browser_display_normalizes_webui_numeric_value() -> None:
    numeric = BrowserCookieSource(
        BrowserCookieSpec(browser="chromium", display="10.0")
    )
    canonical = BrowserCookieSource(
        BrowserCookieSpec(browser="chromium", display=":10.0")
    )

    assert numeric._display() == ":10.0"
    assert canonical._display() == ":10.0"


def test_config_browser_mode_ignores_manual_cookie_and_wires_profile() -> None:
    config = ConfigManager(
        {
            "youtube": {
                "cookie_source": "浏览器 Profile",
                "cookie": "SAPISID=stale-manual-value",
                "browser_name": "chromium",
                "browser_profile": "/root/.config/chromium/Default",
                "browser_keyring": "BASICTEXT",
                "browser_wakeup_mode": "有头（推荐）",
                "browser_display": ":10.0",
            }
        }
    )
    parser = config.create_parsers()[0]

    assert config.youtube.cookie_source == "browser"
    assert config.youtube.cookie == ""
    assert parser.cookie_runtime.header() == ""
    assert parser.browser_cookie_source is not None
    assert parser.browser_cookie_source.spec.profile.endswith("/Default")
    assert parser.browser_cookie_source.wakeup_mode == "headed"
    assert parser.cookie_maintenance_enabled is True


def test_ytdlp_browser_cookies_are_dynamic_by_health_state() -> None:
    spec = ("chromium", "/root/.config/chromium/Default", "BASICTEXT", None)
    resolver = YtDlpStreamResolver(cookies_from_browser=spec)
    env = YtDlpEnvironment(available=True)
    with mock.patch.object(ytdlp_runtime, "probe_ytdlp_environment", return_value=env):
        authenticated = resolver.build_options(use_browser_cookies=True)
        anonymous = resolver.build_options(use_browser_cookies=False)

    assert authenticated["cookiesfrombrowser"] == spec
    assert "cookiefile" not in authenticated
    assert "cookiesfrombrowser" not in anonymous
    assert "cookiefile" not in anonymous


def test_parser_syncs_browser_snapshot_into_innertube_runtime() -> None:
    parser = YouTubeParser(
        browser_cookie_name="chromium",
        browser_cookie_profile="/root/.config/chromium/Default",
        browser_cookie_keyring="BASICTEXT",
    )
    source = parser.browser_cookie_source
    assert source is not None

    with mock.patch.object(
        source,
        "refresh",
        new=mock.AsyncMock(return_value=(_snapshot(), True)),
    ):
        authenticated, detail = asyncio.run(parser._sync_browser_cookie())

    assert authenticated is True
    assert "已鉴权" in detail
    assert parser.cookie_authenticated is True
    assert parser.cookie_runtime.header() == "SAPISID=fresh; SID=session"
    assert "tv" in parser.player_clients
    assert parser._login_label(False) == "browser(已鉴权)"


def test_browser_maintenance_wakes_syncs_and_verifies() -> None:
    parser = YouTubeParser(
        browser_cookie_name="chromium",
        browser_cookie_profile="/root/.config/chromium/Default",
        browser_cookie_wakeup_mode="headed",
    )
    source = parser.browser_cookie_source
    assert source is not None

    async def run_maintenance():
        with (
            mock.patch.object(
                source,
                "wakeup",
                new=mock.AsyncMock(return_value=(True, "已访问并关闭")),
            ) as wakeup,
            mock.patch.object(
                source,
                "refresh",
                new=mock.AsyncMock(return_value=(_snapshot(), True)),
            ) as refresh,
            mock.patch.object(
                parser.cookie_runtime,
                "keepalive",
                new=mock.AsyncMock(return_value=(True, "服务端确认已登录")),
            ) as keepalive,
        ):
            result = await parser.maintain_cookie(mock.Mock(), verify=True)
        return result, wakeup, refresh, keepalive

    (logged_in, detail), wakeup, refresh, keepalive = asyncio.run(run_maintenance())
    assert logged_in is True
    assert "浏览器唤醒" in detail
    assert "Profile 同步" in detail
    assert parser.cookie_runtime.alive is True
    wakeup.assert_awaited_once()
    refresh.assert_awaited_once_with(force=True)
    keepalive.assert_awaited_once()
