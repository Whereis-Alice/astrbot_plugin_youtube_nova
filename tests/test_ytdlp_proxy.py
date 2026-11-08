from unittest.mock import patch

import pytest
import yt_dlp

from youtube_core.parser.runtime_manager.youtube import ytdlp as runtime


@pytest.mark.parametrize("configured", [None, "", "http://127.0.0.1:7890"])
def test_extraction_uses_only_explicit_plugin_proxy(configured):
    environment = runtime.YtDlpEnvironment(available=True)
    with patch.object(runtime, "probe_ytdlp_environment", return_value=environment), patch(
        "urllib.request.getproxies", return_value={"https": "http://system-proxy.example:9000"}
    ):
        options = runtime.YtDlpStreamResolver(proxy=configured).build_options()
        assert options["proxy"] == (configured or "")
        with yt_dlp.YoutubeDL(options) as ydl:
            assert ydl.proxies == {"all": configured or "__noproxy__"}
