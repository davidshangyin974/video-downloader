from __future__ import annotations

import importlib.util
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


def load_launcher():
    launcher_path = Path(__file__).resolve().parents[1] / "run.py"
    spec = importlib.util.spec_from_file_location("video_downloader_launcher", launcher_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载启动器。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherTests(unittest.TestCase):
    def test_desktop_port_zero_reserves_an_ephemeral_loopback_socket(self) -> None:
        with patch.dict(os.environ, {"VIDEO_DOWNLOADER_DESKTOP": "1", "VIDEO_DOWNLOADER_PORT": "0"}, clear=True):
            launcher = load_launcher()
            listener = MagicMock()
            listener.getsockname.return_value = ("127.0.0.1", 43210)
            with (
                patch.object(launcher.socket, "socket", return_value=listener),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertIs(launcher.bind_desktop_socket(), listener)
        listener.bind.assert_called_once_with(("127.0.0.1", 0))
        listener.listen.assert_called_once()
        self.assertIn("http://127.0.0.1:43210", output.getvalue())

    def test_browser_opens_only_after_health_is_ready(self) -> None:
        launcher = load_launcher()
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        response.__exit__.return_value = None
        with (
            patch.object(launcher.urllib.request, "urlopen", return_value=response),
            patch.object(launcher.webbrowser, "open") as open_browser,
        ):
            launcher.open_browser_after_health()
        open_browser.assert_called_once_with("http://127.0.0.1:8000")

    def test_main_runs_one_uvicorn_process_on_loopback(self) -> None:
        launcher = load_launcher()
        thread = MagicMock()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(launcher, "check_dependencies"),
            patch.object(launcher.threading, "Thread", return_value=thread),
            patch.object(launcher.uvicorn, "run") as run_server,
        ):
            launcher.main()
        thread.start.assert_called_once_with()
        run_server.assert_called_once_with(launcher.app, host="127.0.0.1", port=8000, log_level="info")

    def test_desktop_mode_does_not_open_a_second_browser(self) -> None:
        launcher = load_launcher()
        with (
            patch.dict(os.environ, {"VIDEO_DOWNLOADER_DESKTOP": "1"}),
            patch.object(launcher, "check_dependencies"),
            patch.object(launcher.threading, "Thread") as thread,
            patch.object(launcher.uvicorn, "run") as run_server,
        ):
            launcher.main()
        thread.assert_not_called()
        run_server.assert_called_once_with(launcher.app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    unittest.main(verbosity=2)
