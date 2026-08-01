from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from app.engines import aria2, qbittorrent


class FakeProcess:
    def __init__(self, polls: list[int | None], returncode: int = 0, wait_error: Exception | None = None) -> None:
        self.polls = iter(polls)
        self.returncode = returncode
        self.wait_error = wait_error
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return next(self.polls, self.returncode)

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        if self.wait_error:
            raise self.wait_error
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class Aria2AdapterEdgeTests(unittest.TestCase):
    def test_executable_and_version_cover_missing_errors_and_output(self) -> None:
        with patch("app.engines.aria2.shutil.which", return_value=None):
            self.assertIsNone(aria2.executable())
            self.assertIsNone(aria2.version())
        with (
            patch("app.engines.aria2.executable", return_value="/usr/bin/aria2c"),
            patch("app.engines.aria2.subprocess.run", side_effect=OSError("broken")),
        ):
            self.assertIsNone(aria2.version())
        with (
            patch("app.engines.aria2.executable", return_value="/usr/bin/aria2c"),
            patch("app.engines.aria2.subprocess.run", return_value=MagicMock(stdout="")),
        ):
            self.assertIsNone(aria2.version())
        with (
            patch("app.engines.aria2.executable", return_value="/usr/bin/aria2c"),
            patch("app.engines.aria2.subprocess.run", return_value=MagicMock(stdout="aria2 version 1.37.0\nCopyright")),
        ):
            self.assertEqual(aria2.version(), "1.37.0")

    def test_direct_download_rejects_missing_binary_unsafe_name_and_launch_error(self) -> None:
        callback = lambda *_args: None
        with patch("app.engines.aria2.executable", return_value=None):
            with self.assertRaisesRegex(aria2.Aria2Error, "未找到"):
                aria2.download("https://example.com/a", "/tmp", "a.mp4", None, lambda: False, callback, callback)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("app.engines.aria2.executable", return_value="aria2c"):
                with self.assertRaisesRegex(aria2.Aria2Error, "不安全"):
                    aria2.download("https://example.com/a", temporary_directory, "../a.mp4", None, lambda: False, callback, callback)
            with (
                patch("app.engines.aria2.executable", return_value="aria2c"),
                patch("app.engines.aria2.subprocess.Popen", side_effect=OSError("cannot start")),
            ):
                with self.assertRaisesRegex(aria2.Aria2Error, "无法启动"):
                    aria2.download("https://example.com/a", temporary_directory, "a.mp4", None, lambda: False, callback, callback)

    def test_direct_download_cancellation_kills_stuck_process(self) -> None:
        process = FakeProcess([None], wait_error=subprocess.TimeoutExpired("aria2c", 4))
        processes: list[object | None] = []
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch("app.engines.aria2.executable", return_value="aria2c"),
                patch("app.engines.aria2.subprocess.Popen", return_value=process),
            ):
                with self.assertRaisesRegex(aria2.Aria2Error, "已取消"):
                    aria2.download(
                        "https://example.com/a",
                        temporary_directory,
                        "a.mp4",
                        None,
                        lambda: True,
                        processes.append,
                        lambda *_args: None,
                    )
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertIsNone(processes[-1])

    def test_direct_download_reports_process_and_missing_output_errors(self) -> None:
        callback = lambda *_args: None
        with tempfile.TemporaryDirectory() as temporary_directory:
            for process, message in (
                (FakeProcess([1], returncode=1), "未能下载"),
                (FakeProcess([0], returncode=0), "未找到输出文件"),
            ):
                with (
                    self.subTest(message=message),
                    patch("app.engines.aria2.executable", return_value="aria2c"),
                    patch("app.engines.aria2.subprocess.Popen", return_value=process),
                ):
                    with self.assertRaisesRegex(aria2.Aria2Error, message):
                        aria2.download(
                            "https://example.com/a",
                            temporary_directory,
                            "a.mp4",
                            None,
                            lambda: False,
                            callback,
                            callback,
                            download_rate_limit_kbps=256,
                        )

    def test_managed_files_exclude_control_and_private_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "movie.mp4").write_bytes(b"video")
            (root / "movie.mp4.aria2").write_bytes(b"control")
            private = root / ".video-downloader"
            private.mkdir()
            (private / "source.torrent").write_bytes(b"torrent")
            self.assertEqual(aria2._managed_files(root), [root / "movie.mp4"])

    def test_bt_download_errors_and_cancellation(self) -> None:
        callback = lambda *_args: None
        with patch("app.engines.aria2.executable", return_value=None):
            with self.assertRaisesRegex(aria2.Aria2Error, "未找到"):
                aria2.download_bt("magnet:?x", "/tmp", None, None, lambda: False, callback, callback)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch("app.engines.aria2.executable", return_value="aria2c"),
                patch("app.engines.aria2.subprocess.Popen", side_effect=OSError("cannot start")),
            ):
                with self.assertRaisesRegex(aria2.Aria2Error, "无法启动"):
                    aria2.download_bt("magnet:?x", temporary_directory, None, [0], lambda: False, callback, callback)

            process = FakeProcess([None], wait_error=subprocess.TimeoutExpired("aria2c", 4))
            with (
                patch("app.engines.aria2.executable", return_value="aria2c"),
                patch("app.engines.aria2.subprocess.Popen", return_value=process),
            ):
                with self.assertRaisesRegex(aria2.Aria2Error, "已取消"):
                    aria2.download_bt(
                        "magnet:?x",
                        temporary_directory,
                        None,
                        [0],
                        lambda: True,
                        callback,
                        callback,
                        download_rate_limit_kbps=128,
                    )
            self.assertTrue(process.killed)

            for process, message in (
                (FakeProcess([1], returncode=1), "没有获得数据"),
                (FakeProcess([0], returncode=0), "未找到 BT"),
            ):
                with (
                    self.subTest(message=message),
                    patch("app.engines.aria2.executable", return_value="aria2c"),
                    patch("app.engines.aria2.subprocess.Popen", return_value=process),
                ):
                    with self.assertRaisesRegex(aria2.Aria2Error, message):
                        aria2.download_bt("magnet:?x", temporary_directory, None, None, lambda: False, callback, callback)

    def test_fetch_magnet_metadata_covers_all_terminal_results(self) -> None:
        with patch("app.engines.aria2.executable", return_value=None):
            with self.assertRaisesRegex(aria2.Aria2Error, "未找到"):
                aria2.fetch_magnet_metadata("magnet:?x")
        with (
            patch("app.engines.aria2.executable", return_value="aria2c"),
            patch("app.engines.aria2.subprocess.run", side_effect=subprocess.TimeoutExpired("aria2c", 10)),
        ):
            with self.assertRaisesRegex(aria2.Aria2Error, "没有返回文件列表"):
                aria2.fetch_magnet_metadata("magnet:?x", 1)

        def write_metadata(command: list[str], **_kwargs: object) -> MagicMock:
            target = Path(command[command.index("--dir") + 1]) / "metadata.torrent"
            target.write_bytes(b"torrent")
            return MagicMock(returncode=0)

        with (
            patch("app.engines.aria2.executable", return_value="aria2c"),
            patch("app.engines.aria2.subprocess.run", side_effect=write_metadata),
        ):
            self.assertEqual(aria2.fetch_magnet_metadata("magnet:?x"), b"torrent")

        for returncode, message in ((1, "没有返回文件列表"), (0, "没有得到有效")):
            with (
                self.subTest(returncode=returncode),
                patch("app.engines.aria2.executable", return_value="aria2c"),
                patch("app.engines.aria2.subprocess.run", return_value=MagicMock(returncode=returncode)),
            ):
                with self.assertRaisesRegex(aria2.Aria2Error, message):
                    aria2.fetch_magnet_metadata("magnet:?x")


class QbittorrentAdapterEdgeTests(unittest.TestCase):
    def test_client_rejects_invalid_urls_and_normalizes_valid_base(self) -> None:
        for value in ("", "localhost:8080", "ftp://localhost"):
            with self.subTest(value=value), self.assertRaises(qbittorrent.QbittorrentError):
                qbittorrent.Client(value, "user", "password")
        client = qbittorrent.Client(" https://example.com/qbt/ ", "user", "password")
        self.assertEqual(client.base_url, "https://example.com/qbt")

    def test_request_covers_success_auth_http_and_connection_errors(self) -> None:
        client = qbittorrent.Client("http://localhost:8080", "user", "password")
        response = MagicMock()
        response.read.return_value = b"ok"
        response.__enter__.return_value = response
        client.opener = MagicMock()
        client.opener.open.return_value = response
        self.assertEqual(client._request("/api", b"x", "text/plain"), b"ok")
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Content-type"], "text/plain")

        for error, message in (
            (HTTPError("http://x", 401, "unauthorized", {}, io.BytesIO()), "拒绝了连接"),
            (HTTPError("http://x", 500, "server", {}, io.BytesIO()), "HTTP 500"),
            (URLError("offline"), "无法连接"),
        ):
            client.opener.open.side_effect = error
            with self.subTest(error=error), self.assertRaisesRegex(qbittorrent.QbittorrentError, message):
                client._request("/api")
            if isinstance(error, HTTPError):
                error.close()

    def test_login_versions_forms_and_invalid_login(self) -> None:
        client = qbittorrent.Client("http://localhost:8080", "user", "password")
        with patch.object(client, "_request", return_value=b"Denied"):
            with self.assertRaisesRegex(qbittorrent.QbittorrentError, "登录失败"):
                client.login()
        with patch.object(client, "_request", side_effect=[b"Ok.", b"v5.2.0", b"2.11", b"form"]):
            client.login()
            client.login()
            self.assertEqual(client.app_version(), "v5.2.0")
            self.assertEqual(client.webapi_version(), "2.11")
            self.assertEqual(client._form("/form", {"x": "1"}), b"form")

    def test_add_covers_missing_source_multipart_and_rejected_responses(self) -> None:
        client = qbittorrent.Client("http://localhost:8080", "user", "password")
        with self.assertRaisesRegex(qbittorrent.QbittorrentError, "缺少"):
            client.add(None, None, "/tmp", "tag")

        with (
            patch.object(client, "login"),
            patch.object(client, "_request", return_value=b"Ok.") as request,
            patch("app.engines.qbittorrent.secrets.token_hex", return_value="fixed"),
        ):
            self.assertEqual(client.add(None, b"torrent", "/tmp", "tag", 64), [])
        self.assertIn(b"source.torrent", request.call_args.args[1])
        self.assertIn(b"65536", request.call_args.args[1])

        rejected = (
            b"not-json",
            json.dumps({"success_count": 0}).encode(),
            json.dumps({"success_count": 1, "failure_count": 1}).encode(),
        )
        for response in rejected:
            with self.subTest(response=response), patch.object(client, "_form", return_value=response):
                with self.assertRaisesRegex(qbittorrent.QbittorrentError, "没有接受"):
                    client.add("magnet:?x", None, "/tmp", "tag")
        with patch.object(client, "_form", return_value=json.dumps({"success_count": 1}).encode()):
            self.assertEqual(client.add("magnet:?x", None, "/tmp", "tag"), [])

    def test_info_files_versions_and_mutations_cover_invalid_shapes(self) -> None:
        client = qbittorrent.Client("http://localhost:8080", "user", "password")
        with patch.object(client, "login"), patch.object(client, "_request", return_value=b"not-json"):
            with self.assertRaisesRegex(qbittorrent.QbittorrentError, "任务信息"):
                client.info(tag="tag", hashes="hash")
            with self.assertRaisesRegex(qbittorrent.QbittorrentError, "文件列表"):
                client.files("hash")
        with patch.object(client, "login"), patch.object(client, "_request", return_value=b"{}"):
            self.assertEqual(client.info(), [])
            self.assertEqual(client.files("hash"), [])
        with patch.object(client, "login"), patch.object(client, "_request", return_value=b"[{}]"):
            self.assertEqual(client.info(), [{}])
            self.assertEqual(client.files("hash"), [{}])

        with patch.object(client, "app_version", return_value="development"):
            self.assertFalse(client._uses_v5_api())
        with patch.object(client, "_form") as form:
            client.delete("hash", delete_files=True)
            client.file_priority("hash", [], 0)
            client.file_priority("hash", [1, 3], 7)
        self.assertEqual(form.call_args_list[0].args[1]["deleteFiles"], "true")
        self.assertEqual(form.call_args_list[1].args[1]["id"], "1|3")

    def test_media_file_path_requires_exactly_one_valid_media_path(self) -> None:
        extensions = {".mp4", ".mkv"}
        task = {"save_path": "/downloads"}
        self.assertIsNone(qbittorrent.media_file_path(task, [], extensions))
        self.assertIsNone(
            qbittorrent.media_file_path(task, [{"name": "a.mp4"}, {"name": "b.mkv"}], extensions)
        )
        self.assertIsNone(qbittorrent.media_file_path({}, [{"name": "a.mp4"}], extensions))
        self.assertIsNone(qbittorrent.media_file_path(task, [{"name": 123}], extensions))
        self.assertEqual(
            qbittorrent.media_file_path(task, [{"name": "folder/a.mp4"}], extensions),
            "/downloads/folder/a.mp4",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
