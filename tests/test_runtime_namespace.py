from youtube_core.message_adapter.archive_builder import (
    _ARCHIVE_FILE_NAME,
    _ARCHIVE_ROOT_NAME,
    _WORKSPACE_PREFIX,
)
from youtube_core.storage.cache_marker import EXPIRY_FILE_NAME, MARKER_FILE_NAME


def test_runtime_files_use_youtube_plugin_namespace() -> None:
    names = {
        _ARCHIVE_FILE_NAME,
        _ARCHIVE_ROOT_NAME,
        _WORKSPACE_PREFIX,
        MARKER_FILE_NAME,
        EXPIRY_FILE_NAME,
    }

    assert all("youtube_nova" in name for name in names)
    assert all("media_parser_nova" not in name for name in names)
