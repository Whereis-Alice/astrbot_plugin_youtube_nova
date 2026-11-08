#!/usr/bin/env python3
"""Install the optional, matched bgutil components explicitly (never on bot startup)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


VERSION = "2.0.2"
SOURCE_COMMIT = "26475e9d3665b972c4aeb83b4dbaafcf64b88438"
ARCHIVE_SHA256 = "1f2ae9ec39405b8e80397dfc2551f58deed09d8dff4b2b8fbc314df8e091c0a1"
ARCHIVE_URL = (
    "https://codeload.github.com/Brainicism/bgutil-ytdlp-pot-provider/"
    f"tar.gz/refs/tags/{VERSION}"
)
ARCHIVE_ROOT = f"bgutil-ytdlp-pot-provider-{VERSION}"
DIRECTORY_NAME = "pot_provider_2_0_2"
MARKER_NAME = ".youtube-nova-setup.json"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
MARKER = {"version": VERSION, "source_commit": SOURCE_COMMIT, "archive_sha256": ARCHIVE_SHA256}


class SetupError(RuntimeError):
    """An actionable setup failure without dumping process credentials."""


def _tool_command(executable: str) -> list[str]:
    path = shutil.which(executable)
    if not path:
        raise SetupError(f"{executable} is not on PATH.")
    if os.name == "nt" and Path(path).suffix.lower() in {".cmd", ".bat"}:
        # Run npm's JS entry directly; avoid cmd.exe parsing user-supplied data paths.
        entry = Path(path).parent / "node_modules/npm/bin/npm-cli.js"
        node = shutil.which("node")
        if executable != "npm" or not node or not entry.is_file():
            raise SetupError("Use a standard Node.js/npm installation with npm-cli.js available.")
        return [node, str(entry)]
    return [path]


def _version_of(executable: str, minimum: int) -> str | None:
    try:
        command = _tool_command(executable)
        result = subprocess.run(
            [*command, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
    except (SetupError, OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    match = re.fullmatch(r"v?(\d+)\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?", value)
    return value if result.returncode == 0 and match and int(match[1]) >= minimum else None


def _python_provider_version() -> str | None:
    try:
        return importlib.metadata.version("bgutil-ytdlp-pot-provider")
    except importlib.metadata.PackageNotFoundError:
        return None


def _script_ready(destination: Path) -> bool:
    try:
        package = json.loads((destination / "server/package.json").read_text(encoding="utf-8"))
        return (
            package.get("version") == VERSION
            and (destination / "server/build/generate_once.js").is_file()
            and (destination / "server/node_modules").is_dir()
        )
    except (OSError, ValueError, AttributeError):
        return False


def _managed_ready(destination: Path) -> bool:
    try:
        marker = json.loads((destination / MARKER_NAME).read_text(encoding="utf-8"))
        return marker == MARKER and _script_ready(destination)
    except (OSError, ValueError):
        return False


def _child_environment(data_dir: Path) -> dict[str, str]:
    cache = data_dir / ".youtube-nova-setup-cache"
    temporary = cache / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
        "PIP_CACHE_DIR": str(cache / "pip"), "UV_CACHE_DIR": str(cache / "uv"),
        "npm_config_cache": str(cache / "npm"), "XDG_CACHE_HOME": str(cache / "xdg"),
    })
    return env


def _run(command: list[str], *, cwd: Path, env: dict[str, str], log: Path, label: str, timeout: int = 300) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"{label} could not finish ({type(exc).__name__}).") from None
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{label}]\n{result.stdout}\n")
    if result.returncode:
        raise SetupError(f"{label} failed (exit {result.returncode}); inspect {log} locally.")
    return result.stdout.strip()


def _download_archive(target: Path) -> None:
    digest, size = hashlib.sha256(), 0
    request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "YouTube-Nova-Setup/1"})
    with urllib.request.urlopen(request, timeout=45) as response, target.open("xb") as output:
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise SetupError("Upstream archive exceeds the download size limit.")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise SetupError("Upstream archive SHA-256 mismatch; no code was built or installed.")


def _extract_archive(archive: Path, output: Path) -> Path:
    """Extract regular files/directories only, after validating the entire member list."""
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        size = 0
        for member in members:
            name = PurePosixPath(member.name)
            if (
                name.is_absolute() or not name.parts or name.parts[0] != ARCHIVE_ROOT
                or ".." in name.parts or "\\" in member.name or ":" in member.name
                or not (member.isfile() or member.isdir())
            ):
                raise SetupError("Unsafe or unsupported path in the upstream archive.")
            size += member.size
            if size > MAX_EXTRACTED_BYTES:
                raise SetupError("Upstream source exceeds the extraction size limit.")
        output.mkdir()
        # All paths/types were checked; no archive-controlled links or special files exist.
        for member in members:
            target = output.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.extractfile(member)
            if content is None:
                raise SetupError("Could not read an upstream source file.")
            with content, target.open("xb") as stream:
                shutil.copyfileobj(content, stream)
    return output / ARCHIVE_ROOT


def _install_python_provider(data_dir: Path, env: dict[str, str], log: Path) -> None:
    if _python_provider_version() == VERSION:
        return
    specification = f"bgutil-ytdlp-pot-provider=={VERSION}"
    if importlib.util.find_spec("pip") is not None:
        command = [sys.executable, "-m", "pip", "install", specification]
    elif uv := shutil.which("uv"):
        command = [uv, "pip", "install", "--python", sys.executable, specification]
    else:
        raise SetupError("This Python has no pip, and uv is not on PATH. Install the optional requirements in AstrBot's Python environment first.")
    _run(command, cwd=data_dir, env=env, log=log, label="Python PO Token provider")
    # Distribution discovery may have been cached before pip/uv ran.
    importlib.invalidate_caches()
    if _python_provider_version() != VERSION:
        raise SetupError("The expected Python provider is not visible to this interpreter after installation.")


def install(data_dir: Path) -> Path:
    """Build pinned upstream code separately, then install its matched Python provider."""
    destination = data_dir / DIRECTORY_NAME
    if destination.exists() and not _managed_ready(destination):
        raise SetupError("Destination already exists without a valid setup manifest; use --check or a different --data-dir. Existing files were not changed.")
    if not _version_of("node", 22) or not _version_of("npm", 9):
        raise SetupError("Install Node.js 22+ and npm 9+ in the AstrBot execution environment first.")
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = data_dir / ".youtube-nova-pot-setup.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise SetupError("Another setup owns the lock directory. Wait for it; remove a stale lock only after confirming no setup process is running.") from None
    try:
        env = _child_environment(data_dir)
        log = data_dir / "pot-provider-setup.log"
        log.touch(mode=0o600, exist_ok=True)
        if not destination.exists():
            with tempfile.TemporaryDirectory(prefix=".youtube-nova-pot-build-", dir=data_dir) as temporary:
                stage = Path(temporary)
                archive = stage / "source.tar.gz"
                _download_archive(archive)
                source = _extract_archive(archive, stage / "source")
                server = source / "server"
                package = json.loads((server / "package.json").read_text(encoding="utf-8"))
                if package.get("version") != VERSION:
                    raise SetupError("Upstream package version does not match the pinned version.")
                _run([*_tool_command("npm"), "ci", "--include=dev", "--no-audit", "--no-fund"], cwd=server, env=env, log=log, label="npm locked dependencies")
                _run([*_tool_command("node"), str(server / "node_modules/typescript/bin/tsc"), "--project", "tsconfig.json"], cwd=server, env=env, log=log, label="TypeScript build", timeout=120)
                version = _run([*_tool_command("node"), str(server / "build/generate_once.js"), "--version"], cwd=server, env=env, log=log, label="Generated script version", timeout=30)
                if version != VERSION:
                    raise SetupError("Generated script version does not match the pinned version.")
                _install_python_provider(data_dir, env, log)
                (source / MARKER_NAME).write_text(json.dumps(MARKER, indent=2) + "\n", encoding="utf-8")
                if destination.exists():
                    raise SetupError("Destination appeared during setup; existing files were not overwritten.")
                source.rename(destination)
        else:
            _install_python_provider(data_dir, env, log)
        return destination / "server"
    finally:
        lock.rmdir()


def check(data_dir: Path) -> bool:
    """Offline checks only; do not download, install, generate tokens, or change config."""
    checks = {
        "Node.js 22+": bool(_version_of("node", 22)),
        "npm 9+": bool(_version_of("npm", 9)),
        f"Python provider {VERSION}": _python_provider_version() == VERSION,
        f"Built script {VERSION}": _script_ready(data_dir / DIRECTORY_NAME),
    }
    for name, ready in checks.items():
        print(f"{'OK' if ready else 'MISSING'}: {name}")
    return all(checks.values())


def configuration(server: Path) -> None:
    print("\nSet these values in the AstrBot plugin configuration, then restart AstrBot after installing a new Python provider:")
    print(json.dumps({"youtube.ytdlp_pot_provider": str(server), "youtube.ytdlp_fetch_pot": "总是"}, ensure_ascii=False, indent=2))
    print("Paths must exist INSIDE the AstrBot runtime/container. No AstrBot config was changed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Explicitly install matching bgutil {VERSION} Python/script components using THIS Python interpreter.",
        epilog="Prerequisites: Python used by AstrBot, Node.js 22+, npm 9+, network access, pip or uv. Use a persistent data directory mounted inside AstrBot's container. This tool never installs system software, edits AstrBot config, reads Cookie, or runs on bot startup.",
    )
    parser.add_argument("--data-dir", required=True, type=Path, help="Absolute persistent plugin data directory; installation/caches/temp files stay here.")
    parser.add_argument("--check", action="store_true", help="Offline checks only; no installation or token requests.")
    args = parser.parse_args(argv)
    if not args.data_dir.is_absolute():
        parser.error("--data-dir must be an absolute path")
    data_dir = args.data_dir.resolve()
    try:
        if args.check:
            ready = check(data_dir)
            if ready:
                configuration(data_dir / DIRECTORY_NAME / "server")
            return 0 if ready else 1
        server = install(data_dir)
        configuration(server)
        return 0
    except (SetupError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
