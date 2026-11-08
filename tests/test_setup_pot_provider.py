import importlib.util
import io
import json
from pathlib import Path
import tarfile
from unittest import mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    "nova_pot_setup", Path(__file__).resolve().parents[1] / "tools/setup_pot_provider.py",
)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def archive(tmp_path, members):
    target = tmp_path / "upstream.tar.gz"
    with tarfile.open(target, "w:gz") as output:
        for name, kind, value in members:
            member = tarfile.TarInfo(name)
            if kind == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = value
                output.addfile(member)
            else:
                content = value.encode()
                member.size = len(content)
                output.addfile(member, io.BytesIO(content))
    return target


@pytest.mark.parametrize("name,kind,value", [
    ("../outside.txt", "file", "bad"),
    (setup.ARCHIVE_ROOT + "/../../outside.txt", "file", "bad"),
    ("/absolute.txt", "file", "bad"),
    (setup.ARCHIVE_ROOT + "/server/link", "link", "../../outside"),
    (setup.ARCHIVE_ROOT + "/server/C:drive", "file", "bad"),
    (setup.ARCHIVE_ROOT + "/server/..\\outside", "file", "bad"),
])
def test_unsafe_archive_is_rejected_before_any_extraction(tmp_path, name, kind, value):
    source = archive(tmp_path, [
        (setup.ARCHIVE_ROOT + "/server/good.txt", "file", "good"),
        (name, kind, value),
    ])
    output = tmp_path / "extracted"
    with pytest.raises(setup.SetupError, match="Unsafe"):
        setup._extract_archive(source, output)
    assert not output.exists()
    assert not (tmp_path / "outside.txt").exists()


def test_archive_size_limit_is_checked_before_extraction(tmp_path, monkeypatch):
    source = archive(tmp_path, [(setup.ARCHIVE_ROOT + "/large", "file", "large")])
    monkeypatch.setattr(setup, "MAX_EXTRACTED_BYTES", 2)
    with pytest.raises(setup.SetupError, match="size limit"):
        setup._extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_unexpected_archive_hash_does_not_run_or_extract_code(tmp_path, monkeypatch):
    response = mock.MagicMock()
    response.__enter__.return_value.read.side_effect = [b"tampered", b""]
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *args, **kwargs: response)
    with pytest.raises(setup.SetupError, match="SHA-256 mismatch"):
        setup._download_archive(tmp_path / "download.tar.gz")


def test_existing_unknown_installation_is_preserved(tmp_path, monkeypatch):
    destination = tmp_path / setup.DIRECTORY_NAME
    destination.mkdir()
    sentinel = destination / "private-data.txt"
    sentinel.write_text("keep", encoding="utf-8")
    downloader = mock.Mock()
    monkeypatch.setattr(setup, "_download_archive", downloader)
    with pytest.raises(setup.SetupError, match="Destination already exists"):
        setup.install(tmp_path)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    downloader.assert_not_called()


def test_build_failure_cleans_temporary_work_and_releases_lock(tmp_path, monkeypatch):
    source_archive = archive(tmp_path, [(setup.ARCHIVE_ROOT + "/server/package.json", "file", json.dumps({"version": setup.VERSION}))])
    monkeypatch.setattr(setup, "_version_of", lambda *args: "22.0.0")
    monkeypatch.setattr(setup, "_tool_command", lambda name: [name])

    def download(target):
        target.write_bytes(source_archive.read_bytes())

    monkeypatch.setattr(setup, "_download_archive", download)
    monkeypatch.setattr(setup, "_run", mock.Mock(side_effect=setup.SetupError("npm failed")))
    install_python = mock.Mock()
    monkeypatch.setattr(setup, "_install_python_provider", install_python)
    with pytest.raises(setup.SetupError, match="npm failed"):
        setup.install(tmp_path)
    assert not (tmp_path / setup.DIRECTORY_NAME).exists()
    assert not (tmp_path / ".youtube-nova-pot-setup.lock").exists()
    assert not list(tmp_path.glob(".youtube-nova-pot-build-*"))
    install_python.assert_not_called()


def test_check_is_offline_and_does_not_create_data_directory(tmp_path, monkeypatch):
    destination = tmp_path / "uncreated"
    monkeypatch.setattr(setup, "_version_of", lambda *args: None)
    monkeypatch.setattr(setup, "_python_provider_version", lambda: None)
    monkeypatch.setattr(setup.urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("network")))
    assert setup.main(["--data-dir", str(destination), "--check"]) == 1
    assert not destination.exists()


def test_python_install_uses_current_interpreter_via_uv_when_pip_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "_python_provider_version", mock.Mock(side_effect=[None, setup.VERSION]))
    monkeypatch.setattr(setup.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/tools/uv" if name == "uv" else None)
    run = mock.Mock()
    monkeypatch.setattr(setup, "_run", run)
    setup._install_python_provider(tmp_path, {}, tmp_path / "log")
    assert run.call_args.args[0] == ["/tools/uv", "pip", "install", "--python", setup.sys.executable, "bgutil-ytdlp-pot-provider==" + setup.VERSION]
