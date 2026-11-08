import asyncio
import json
import threading
from types import SimpleNamespace

from youtube_core import diagnostics
from youtube_core.parser.runtime_manager.youtube import YtDlpEnvironment


def make_config(**youtube):
    return SimpleNamespace(
        youtube=SimpleNamespace(
            **dict(dict(ytdlp_js_runtime="auto", cookie_source="manual", cookie=""), **youtube)
        ),
        download=SimpleNamespace(
            cache_dir_available=True,
            cache_dir="/private/cache/secret-path",
            send_video_max_mb=48,
            max_video_size_mb=1000,
        ),
        proxy=SimpleNamespace(youtube_use_proxy=False, address=""),
    )


def patch_ready(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "probe_ytdlp_environment",
        lambda preference: YtDlpEnvironment(
            available=True,
            version="2026.10.22",
            needs_js_runtime=True,
            ejs_available=True,
            runtime_name="node",
            runtime_version="22.20.0",
        ),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/secret/bin/" + name)


def test_ready_report_displays_limits_and_optional_provider(monkeypatch):
    patch_ready(monkeypatch)
    report = asyncio.run(diagnostics.build_diagnostic_report(make_config(), active_media_flows=2))
    assert "取流依赖：就绪" in report
    assert "2026.10.22" in report
    assert "node 22.20.0" in report
    assert "FFmpeg：已找到" in report
    assert "ffprobe：已找到" in report
    assert "48 MiB" in report and "1000 MiB" in report
    assert "当前媒体处理：2" in report
    assert "PO Token 提供方：未检测到（可选）" in report
    assert "建议：" not in report
    assert "/secret" not in report


def test_missing_dependencies_get_actionable_advice(monkeypatch):
    monkeypatch.setattr(diagnostics, "probe_ytdlp_environment", lambda preference: YtDlpEnvironment())
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    cfg = make_config()
    cfg.download.cache_dir_available = False
    cfg.download.max_video_size_mb = 0
    report = asyncio.run(diagnostics.build_diagnostic_report(cfg))
    assert "yt-dlp：未安装" in report
    assert "FFmpeg：缺失" in report and "ffprobe：缺失" in report
    assert "媒体缓存：不可用" in report
    assert "下载硬上限：不限" in report
    assert "AstrBot 所用 Python 环境" in report
    assert "PATH" in report
    assert "缓存目录配置和写入权限" in report


def test_new_ytdlp_without_js_dependencies_reports_both(monkeypatch):
    monkeypatch.setattr(
        diagnostics, "probe_ytdlp_environment",
        lambda preference: YtDlpEnvironment(
            available=True, needs_js_runtime=True,
            problems=("missing ejs", "missing runtime"),
        ),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/bin/" + name)
    report = asyncio.run(diagnostics.build_diagnostic_report(make_config()))
    assert "yt-dlp-ejs：缺失" in report
    assert "JS 运行时：缺失" in report
    assert "Node.js 22+" in report


def test_probe_errors_are_isolated_and_do_not_disclose_original_exception(monkeypatch):
    def fail(*args):
        raise RuntimeError("https://admin:password@example.test?api_key=SECRET")

    monkeypatch.setattr(diagnostics, "probe_ytdlp_environment", fail)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/bin/ffmpeg" if name == "ffmpeg" else fail())
    report = asyncio.run(diagnostics.build_diagnostic_report(make_config()))
    assert "取流依赖：检查未完成" in report
    assert "FFmpeg：已找到" in report
    assert "ffprobe：检查未完成" in report
    assert "password" not in report and "SECRET" not in report


def test_report_excludes_config_secrets_and_browser_paths(monkeypatch):
    patch_ready(monkeypatch)
    cfg = make_config(cookie_source="browser", cookie="SID=SECRET_COOKIE")
    cfg.youtube.browser_profile = "/private/SECRET_PROFILE"
    cfg.youtube.ytdlp_pot_provider = "https://SECRET_PROVIDER"
    cfg.proxy.youtube_use_proxy = True
    cfg.proxy.address = "http://user:SECRET_PROXY@127.0.0.1:1234"
    cfg.relay = SimpleNamespace(callback_api_base="https://SECRET_CALLBACK")
    cfg.translation = SimpleNamespace(api_key="SECRET_API_KEY")
    report = asyncio.run(diagnostics.build_diagnostic_report(cfg))
    assert "浏览器 Profile（未验证登录态）" in report
    assert "YouTube 代理：已启用" in report
    assert "SECRET" not in report
    assert "/private" not in report
    assert "127.0.0.1" not in report


def test_local_probes_run_outside_the_event_loop_thread(monkeypatch):
    event_thread = threading.get_ident()
    threads = []

    def probe(preference):
        threads.append(threading.get_ident())
        assert preference == "node"
        return YtDlpEnvironment(available=True)

    def which(name):
        threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(diagnostics, "probe_ytdlp_environment", probe)
    monkeypatch.setattr(diagnostics.shutil, "which", which)
    asyncio.run(diagnostics.build_diagnostic_report(make_config(ytdlp_js_runtime="node")))
    assert len(threads) == 3
    assert all(thread != event_thread for thread in threads)


def test_manual_cookie_is_only_reported_as_configured(monkeypatch):
    patch_ready(monkeypatch)
    report = asyncio.run(diagnostics.build_diagnostic_report(make_config(cookie="SID=SECRET")))
    assert "手动 Cookie（已配置，未验证登录态）" in report
    assert "SECRET" not in report


def test_pot_major_mismatch_is_reported_without_exposing_directory(monkeypatch, tmp_path):
    patch_ready(monkeypatch)
    monkeypatch.setattr(diagnostics.importlib.metadata, "version", lambda name: "2.0.2")
    server = tmp_path / "SECRET_SERVER"
    (server / "build").mkdir(parents=True)
    (server / "package.json").write_text(json.dumps({"version": "1.3.2"}), encoding="utf-8")
    (server / "build" / "generate_once.js").write_text("", encoding="utf-8")
    report = asyncio.run(diagnostics.build_diagnostic_report(make_config(ytdlp_pot_provider=str(server))))
    assert "bgutil Python 包：2.0.2" in report
    assert "PO Token 脚本版本：1.3.2" in report
    assert "PO Token 主版本：不匹配" in report
    assert "仅升级 Python 包不能修复" in report
    assert "SECRET" not in report and str(tmp_path) not in report


def test_pot_matching_versions_still_distinguish_missing_build(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics.importlib.metadata, "version", lambda name: "2.0.2")
    (tmp_path / "package.json").write_text('{"version":"2.0.2"}', encoding="utf-8")
    lines, advice = diagnostics._pot_configuration_details(str(tmp_path))
    assert "PO Token 编译产物：缺失" in lines
    assert "PO Token 主版本：匹配（未验证令牌生成）" in lines
    assert any("编译" in item for item in advice)


def test_pot_missing_package_and_invalid_metadata_have_install_advice(monkeypatch, tmp_path):
    def missing(name):
        raise diagnostics.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(diagnostics.importlib.metadata, "version", missing)
    (tmp_path / "package.json").write_text("invalid", encoding="utf-8")
    lines, advice = diagnostics._pot_configuration_details(str(tmp_path))
    assert "bgutil Python 包：未安装" in lines
    assert "PO Token 脚本：元数据无法读取" in lines
    assert any("setup_pot_provider.py" in item for item in advice)


def test_pot_http_mode_never_reads_files_or_returns_url(monkeypatch):
    monkeypatch.setattr(diagnostics.importlib.metadata, "version", lambda name: "2.0.2")
    def unexpected(*args):
        raise AssertionError("HTTP mode must not inspect local files")
    monkeypatch.setattr(diagnostics, "Path", unexpected)
    lines, advice = diagnostics._pot_configuration_details("https://user:SECRET@example.test/private?token=SECRET")
    assert "PO Token 配置：HTTP 服务（未连接验证）" in lines
    assert "SECRET" not in " ".join(lines + advice)
