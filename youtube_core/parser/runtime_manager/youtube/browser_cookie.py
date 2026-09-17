"""Read a live browser profile as the authoritative YouTube cookie source.

The browser owns the login session and follows Google's cookie rotations.  This
module only takes short-lived snapshots for requests; it never writes back to
the browser database and never logs cookie values.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import signal
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ....logger import logger

SUPPORTED_BROWSERS: tuple[str, ...] = (
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "vivaldi",
    "whale",
)
SUPPORTED_KEYRINGS: tuple[str, ...] = (
    "KWALLET",
    "KWALLET5",
    "KWALLET6",
    "GNOMEKEYRING",
    "BASICTEXT",
)

_COOKIE_UNSAFE = re.compile(r"[\x00-\x20\x7f;]")
_ERROR_LOG_COOLDOWN = 10 * 60


class BrowserCookieError(RuntimeError):
    """The configured browser profile could not produce a cookie snapshot."""


@dataclass(frozen=True)
class BrowserCookieSpec:
    """Normalized yt-dlp browser cookie specification."""

    browser: str = "chromium"
    profile: str = ""
    keyring: str = ""
    container: str = ""
    executable: str = ""
    display: str = ""

    def __post_init__(self) -> None:
        browser = str(self.browser or "").strip().lower()
        if browser not in SUPPORTED_BROWSERS:
            raise ValueError(f"不支持的浏览器类型: {browser or '<empty>'}")
        keyring = str(self.keyring or "").strip().upper()
        if keyring in {"", "AUTO", "自动"}:
            keyring = ""
        elif keyring not in SUPPORTED_KEYRINGS:
            raise ValueError(f"不支持的浏览器密钥环: {keyring}")
        object.__setattr__(self, "browser", browser)
        object.__setattr__(self, "profile", str(self.profile or "").strip())
        object.__setattr__(self, "keyring", keyring)
        object.__setattr__(self, "container", str(self.container or "").strip())
        object.__setattr__(self, "executable", str(self.executable or "").strip())
        object.__setattr__(self, "display", str(self.display or "").strip())

    def ytdlp_tuple(
        self,
    ) -> tuple[str, str | None, str | None, str | None]:
        """Return the tuple expected by yt-dlp's ``cookiesfrombrowser``."""
        return (
            self.browser,
            self.profile or None,
            self.keyring or None,
            self.container or None,
        )

    def label(self) -> str:
        profile = self.profile or "默认 Profile"
        separator = ":" if profile.startswith(("/", "\\")) else "/"
        return f"{self.browser}{separator}{profile}"

    def chromium_profile_args(self) -> tuple[str, str]:
        """Return ``(user_data_dir, profile_name)`` for Chromium startup."""
        if self.browser == "firefox":
            return "", ""
        expanded = os.path.expanduser(self.profile)
        if expanded.startswith("/"):
            profile_path = PurePosixPath(expanded)
            return str(profile_path.parent), profile_path.name
        if expanded and os.path.isabs(expanded):
            profile_path = Path(expanded).resolve()
            return str(profile_path.parent), profile_path.name
        return "", self.profile


@dataclass(frozen=True)
class BrowserCookieSnapshot:
    """A value-free diagnostic summary plus the request header held in memory."""

    header: str
    cookie_count: int
    names: tuple[str, ...]
    fingerprint: str
    fetched_at: float

    @property
    def authenticated(self) -> bool:
        return bool({"SAPISID", "__Secure-3PAPISID"}.intersection(self.names))


class _ExtractionLogger:
    """Keep yt-dlp's browser extraction chatter below the normal log level."""

    @staticmethod
    def debug(message: Any) -> None:
        logger.debug(f"[youtube][browser-cookie] {message}")

    info = debug
    warning = debug
    error = debug


def _youtube_cookie_score(cookie: Any) -> tuple[int, int, int, int]:
    """Prefer the broad, root-path and longer-lived copy of duplicate names."""
    domain = str(getattr(cookie, "domain", "") or "").lower().lstrip(".")
    path = str(getattr(cookie, "path", "") or "")
    try:
        expires = int(getattr(cookie, "expires", 0) or 0)
    except (TypeError, ValueError):
        expires = 0
    return (
        int(domain == "youtube.com"),
        int(path == "/"),
        int(bool(getattr(cookie, "secure", False))),
        expires,
    )


def _is_youtube_domain(domain: str) -> bool:
    normalized = str(domain or "").lower().lstrip(".")
    return normalized == "youtube.com" or normalized.endswith(".youtube.com")


def _extract_snapshot(spec: BrowserCookieSpec) -> BrowserCookieSnapshot:
    """Synchronously extract and flatten the current YouTube browser cookies."""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception as exc:  # pragma: no cover - environment-specific import
        raise BrowserCookieError("AstrBot 的 Python 环境未安装可用的 yt-dlp") from exc

    try:
        jar = extract_cookies_from_browser(
            *spec.ytdlp_tuple()[:2],
            logger=_ExtractionLogger(),
            keyring=spec.ytdlp_tuple()[2],
            container=spec.ytdlp_tuple()[3],
        )
    except Exception as exc:
        raise BrowserCookieError(f"{type(exc).__name__}: {exc}") from exc

    now = time.time()
    selected: dict[str, tuple[tuple[int, int, int, int], str]] = {}
    for cookie in jar:
        if not _is_youtube_domain(getattr(cookie, "domain", "")):
            continue
        try:
            if cookie.is_expired(now):
                continue
        except (AttributeError, TypeError, ValueError):
            pass
        name = str(getattr(cookie, "name", "") or "").strip()
        value = str(getattr(cookie, "value", "") or "").strip()
        if (
            not name
            or not value
            or _COOKIE_UNSAFE.search(name)
            or _COOKIE_UNSAFE.search(value)
        ):
            continue
        score = _youtube_cookie_score(cookie)
        previous = selected.get(name)
        if previous is None or score > previous[0]:
            selected[name] = (score, value)

    names = tuple(sorted(selected))
    header = "; ".join(f"{name}={selected[name][1]}" for name in names)
    fingerprint = hashlib.sha256(header.encode("utf-8")).hexdigest() if header else ""
    return BrowserCookieSnapshot(
        header=header,
        cookie_count=len(names),
        names=names,
        fingerprint=fingerprint,
        fetched_at=now,
    )


class BrowserCookieSource:
    """Concurrency-safe, throttled access to a live browser cookie profile."""

    def __init__(
        self,
        spec: BrowserCookieSpec,
        refresh_seconds: int = 60,
        wakeup_mode: str = "off",
        wakeup_timeout_seconds: int = 30,
    ) -> None:
        self.spec = spec
        self.refresh_seconds = max(15, min(int(refresh_seconds or 60), 3600))
        mode = str(wakeup_mode or "off").strip().lower()
        mode_aliases = {
            "关闭": "off",
            "off": "off",
            "有头": "headed",
            "有头（推荐）": "headed",
            "headed": "headed",
            "无头": "headless",
            "headless": "headless",
        }
        self.wakeup_mode = mode_aliases.get(mode, "off")
        self.auto_wakeup = self.wakeup_mode != "off"
        self.wakeup_timeout_seconds = max(
            10, min(int(wakeup_timeout_seconds or 30), 120)
        )
        self._lock = asyncio.Lock()
        self._snapshot: BrowserCookieSnapshot | None = None
        self._last_attempt = 0.0
        self._last_error = ""
        self._failure_streak = 0
        self._last_error_log_at = 0.0

    def _browser_executable(self) -> str:
        if self.spec.executable:
            expanded = os.path.expanduser(self.spec.executable)
            return expanded if os.path.isfile(expanded) else ""
        candidates = {
            "brave": ("brave-browser", "brave"),
            "chrome": ("google-chrome", "google-chrome-stable"),
            "chromium": ("chromium", "chromium-browser"),
            "edge": ("microsoft-edge", "microsoft-edge-stable"),
            "opera": ("opera",),
            "vivaldi": ("vivaldi", "vivaldi-stable"),
            "whale": ("naver-whale",),
        }.get(self.spec.browser, ())
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return ""

    def _display(self) -> str:
        configured = self.spec.display or str(os.environ.get("DISPLAY") or "")
        if configured:
            configured = configured.strip()
            # WebUI 中常被填成 10.0；X11 实际要求 :10.0。主机名形式
            # localhost:10.0 与已带冒号的写法保持原样。
            if re.fullmatch(r"\d+(?:\.\d+)?", configured):
                configured = f":{configured}"
            return configured
        sockets = sorted(Path("/tmp/.X11-unix").glob("X*"))
        if sockets:
            suffix = sockets[0].name[1:]
            if suffix.isdigit():
                return f":{suffix}.0"
        return ""

    @staticmethod
    def _profile_process_running(user_data_dir: str) -> bool:
        """Check Chromium's profile lock without touching an existing browser."""
        if not user_data_dir:
            return False
        lock_path = Path(user_data_dir) / "SingletonLock"
        try:
            target = os.readlink(lock_path)
            pid = int(target.rsplit("-", 1)[-1])
            os.kill(pid, 0)
            return True
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return False

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:  # pragma: no cover - Windows deployment path
                process.terminate()
            await asyncio.wait_for(process.wait(), timeout=3)
        except (ProcessLookupError, asyncio.TimeoutError):
            if process.returncode is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:  # pragma: no cover - Windows deployment path
                        process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    async def wakeup(self) -> tuple[bool, str]:
        """Visit YouTube in a short-lived browser process and close it safely."""
        if not self.auto_wakeup:
            return False, "自动唤醒未启用"
        if self.spec.browser == "firefox":
            return False, "自动唤醒暂不支持 Firefox"
        executable = self._browser_executable()
        if not executable:
            return False, f"找不到 {self.spec.browser} 可执行文件"

        user_data_dir, profile_name = self.spec.chromium_profile_args()
        if self._profile_process_running(user_data_dir):
            return True, "浏览器已在运行，保留现有进程"

        args = [executable, "--no-first-run", "--no-default-browser-check"]
        headless = self.wakeup_mode == "headless"
        if headless:
            args.extend(["--headless=new", "--disable-gpu"])
        if user_data_dir:
            args.append(f"--user-data-dir={user_data_dir}")
        if profile_name:
            args.append(f"--profile-directory={profile_name}")
        if getattr(os, "geteuid", lambda: 1)() == 0:
            args.append("--no-sandbox")
        if headless:
            args.append("--dump-dom")
        else:
            args.append("--new-window")
        args.append("https://www.youtube.com/")

        process_env = os.environ.copy()
        if not headless:
            display = self._display()
            if not display:
                return False, "找不到可用的 X11 DISPLAY"
            process_env["DISPLAY"] = display

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=(os.name == "posix"),
                env=process_env,
            )
            await asyncio.wait_for(process.wait(), timeout=self.wakeup_timeout_seconds)
            if process.returncode != 0:
                return False, f"浏览器退出码 {process.returncode}"
            return True, "已访问 YouTube 并自动关闭"
        except asyncio.CancelledError:
            if process is not None:
                await self._stop_process(process)
            raise
        except asyncio.TimeoutError:
            if process is not None:
                await self._stop_process(process)
            # A normal headed browser is expected to stay open. Reaching the
            # dwell limit means it had enough time to refresh, then we close
            # only the process group created above.
            mode_label = "有头" if not headless else "无头"
            return True, f"已用{mode_label}浏览器访问 YouTube 并自动关闭"
        except (OSError, ValueError) as exc:
            if process is not None:
                await self._stop_process(process)
            return False, f"启动失败: {type(exc).__name__}: {exc}"

    @property
    def snapshot(self) -> BrowserCookieSnapshot | None:
        return self._snapshot

    @property
    def failure_streak(self) -> int:
        return self._failure_streak

    @property
    def last_error(self) -> str:
        return self._last_error

    def ytdlp_tuple(
        self,
    ) -> tuple[str, str | None, str | None, str | None]:
        return self.spec.ytdlp_tuple()

    async def refresh(
        self,
        force: bool = False,
    ) -> tuple[BrowserCookieSnapshot, bool]:
        """Return ``(snapshot, freshly_read)`` without exposing any value in logs."""
        now = time.monotonic()
        if (
            not force
            and self._snapshot is not None
            and now - self._last_attempt < self.refresh_seconds
        ):
            return self._snapshot, False

        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._snapshot is not None
                and now - self._last_attempt < self.refresh_seconds
            ):
                return self._snapshot, False
            self._last_attempt = now
            try:
                snapshot = await asyncio.to_thread(_extract_snapshot, self.spec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failure_streak += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                if now - self._last_error_log_at >= _ERROR_LOG_COOLDOWN:
                    self._last_error_log_at = now
                    logger.warning(
                        "[youtube] 浏览器 Cookie 读取失败: "
                        f"profile={self.spec.label()}; {self._last_error}"
                    )
                raise BrowserCookieError(self._last_error) from exc

            self._snapshot = snapshot
            self._failure_streak = 0
            self._last_error = ""
            return snapshot, True


__all__ = [
    "SUPPORTED_BROWSERS",
    "SUPPORTED_KEYRINGS",
    "BrowserCookieError",
    "BrowserCookieSnapshot",
    "BrowserCookieSource",
    "BrowserCookieSpec",
]
