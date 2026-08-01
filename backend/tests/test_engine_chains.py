from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core.models import DownloadRequest
from app.engines.inputs import InputError, decode_thunder_url, inspect_non_ytdlp_input, route_input
from app.services.engine_tasks import run_aria2_task


class FileHandler(BaseHTTPRequestHandler):
    content = b"video-downloader-test-file\n" * 1024
    torrent_content: bytes | None = None

    def response_content(self) -> bytes:
        if self.path.endswith(".torrent") and self.__class__.torrent_content is not None:
            return self.__class__.torrent_content
        return self.__class__.content

    def do_HEAD(self) -> None:  # noqa: N802
        content = self.response_content()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Type", "application/x-bittorrent" if self.path.endswith(".torrent") else "video/mp4")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.do_HEAD()
        self.wfile.write(self.response_content())

    def log_message(self, _format: str, *_args: object) -> None:
        return


class EngineChainTests(unittest.TestCase):
    def start_server(self, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def test_thunder_to_aria2_download_chain(self) -> None:
        server = self.start_server(FileHandler)
        target_url = f"http://127.0.0.1:{server.server_port}/clip.mp4"
        thunder_url = "thunder://" + base64.b64encode(f"AA{target_url}ZZ".encode()).decode()
        resolved_url = decode_thunder_url(thunder_url)
        route = route_input(thunder_url)
        self.assertEqual(resolved_url, target_url)
        self.assertEqual(route["engine"], "aria2")
        self.assertEqual(route["source_type"], "thunder")

        updates: list[dict[str, object]] = []
        logs: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            run_aria2_task(
                "aria2-test",
                resolved_url,
                temp_dir,
                "thunder",
                "clip.mp4",
                len(FileHandler.content),
                None,
                None,
                lambda _id, **values: updates.append(values),
                lambda _id, _level, message: logs.append(message),
                lambda _id: None,
                lambda _id: False,
                lambda _id, _process: None,
            )
            output = Path(temp_dir) / "clip.mp4"
            self.assertEqual(output.read_bytes(), FileHandler.content)
        self.assertEqual(updates[-1]["status"], "completed")
        self.assertTrue(updates[-1]["library_visible"])
        self.assertEqual(json.loads(str(updates[-1]["output_files_json"]))[0]["relative_path"], "clip.mp4")
        self.assertTrue(any("直链下载完成" in line for line in logs))

    def test_direct_non_media_file_is_rejected_before_download(self) -> None:
        route = {"engine": "aria2", "source_type": "direct", "resolved_url": "https://example.com/report.pdf"}
        with (
            patch(
                "app.engines.inputs.direct_file_probe",
                return_value={"file_name": "report.pdf", "file_size": 1024, "content_type": "application/pdf"},
            ),
            self.assertRaisesRegex(InputError, "只支持下载视频和音频文件"),
        ):
            inspect_non_ytdlp_input(route, route["resolved_url"])

    def test_direct_audio_file_is_accepted(self) -> None:
        route = {"engine": "aria2", "source_type": "direct", "resolved_url": "https://example.com/track.mp3"}
        with patch(
            "app.engines.inputs.direct_file_probe",
            return_value={"file_name": "track.mp3", "file_size": 1024, "content_type": "audio/mpeg"},
        ):
            inspected = inspect_non_ytdlp_input(route, route["resolved_url"])
        self.assertEqual(inspected["kind"], "file")
        self.assertEqual(inspected["file_name"], "track.mp3")

    def test_ytdlp_non_media_result_is_rejected(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "title": "document",
                "formats": [{"vcodec": "none", "acodec": "none"}],
            }
            with self.assertRaisesRegex(InputError, "只支持下载视频和音频内容"):
                server.inspect_url("https://example.com/document")

    def test_ytdlp_audio_result_is_accepted(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "title": "track",
                "formats": [{"format_id": "audio", "vcodec": "none", "acodec": "mp4a"}],
            }
            inspected = server.inspect_url("https://example.com/track")
        self.assertEqual(inspected["kind"], "video")
        self.assertEqual(inspected["title"], "track")

    def test_ytdlp_regular_playlist_uses_flat_extraction(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "_type": "playlist",
                "title": "Regular playlist",
                "entries": [
                    {
                        "title": "First video",
                        "url": "https://example.com/video/1",
                    }
                ],
            }
            inspected = server.inspect_url("https://example.com/playlist/1")

        options = downloader.call_args.args[0]
        self.assertEqual(options["extract_flat"], "in_playlist")
        self.assertEqual(inspected["entries"][0]["title"], "First video")

    def test_ytdlp_bilibili_bangumi_expands_episode_metadata(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "_type": "playlist",
                "title": "中国通史·精编版",
                "entries": [
                    {
                        "title": "1 秦始皇嬴政：大一统的创始者",
                        "webpage_url": "https://www.bilibili.com/bangumi/play/ep4926728",
                        "thumbnail": "https://i0.hdslb.com/episode.png",
                        "duration": 981.648,
                    }
                ],
            }
            inspected = server.inspect_url(
                "https://www.bilibili.com/bangumi/media/md430450774"
            )

        options = downloader.call_args.args[0]
        self.assertNotIn("extract_flat", options)
        self.assertEqual(
            inspected["entries"][0]["title"],
            "1 秦始皇嬴政：大一统的创始者",
        )
        self.assertEqual(
            inspected["entries"][0]["thumbnail"],
            "https://i0.hdslb.com/episode.png",
        )

    def test_ytdlp_bilibili_space_collection_expands_video_metadata(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "_type": "playlist",
                "title": "合集·旅行VLOG",
                "thumbnail": "https://archive.biliimg.com/collection.jpg",
                "entries": [
                    {
                        "title": "一个人去青岛过秋天",
                        "webpage_url": "https://www.bilibili.com/video/BV1jgxPzpES9",
                        "thumbnail": "http://i0.hdslb.com/video.jpg",
                        "duration": 1707,
                    }
                ],
            }
            inspected = server.inspect_url(
                "https://space.bilibili.com/1373737489/lists/5565958?type=season"
            )

        options = downloader.call_args.args[0]
        self.assertNotIn("extract_flat", options)
        self.assertEqual(inspected["entries"][0]["title"], "一个人去青岛过秋天")
        self.assertEqual(
            inspected["entries"][0]["thumbnail"],
            "https://i0.hdslb.com/video.jpg",
        )

    def test_ytdlp_bilibili_legacy_collection_expands_video_metadata(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "_type": "playlist",
                "title": "旧版合集",
                "entries": [
                    {
                        "title": "第一集",
                        "webpage_url": "https://www.bilibili.com/video/BV1legacy",
                    }
                ],
            }
            server.inspect_url(
                "https://space.bilibili.com/1373737489/channel/collectiondetail?sid=5565958"
            )

        options = downloader.call_args.args[0]
        self.assertNotIn("extract_flat", options)

    def test_bt_non_media_selection_is_rejected_before_download(self) -> None:
        inspect_id = "non-media-selection"
        server.inspect_jobs[inspect_id] = {
            "status": "completed",
            "source": {
                "url": None,
                "route": {"engine": "aria2", "source_type": "torrent_file", "resolved_url": ""},
                "torrent_data": b"torrent",
                "torrent_name": "mixed.torrent",
            },
            "media": {
                "kind": "torrent",
                "files": [
                    {"index": 0, "path": "movie.mp4", "size": 100},
                    {"index": 1, "path": "readme.pdf", "size": 10},
                ],
            },
        }
        self.addCleanup(server.inspect_jobs.pop, inspect_id, None)
        with self.assertRaises(HTTPException) as raised:
            server.create_download(
                DownloadRequest(inspect_id=inspect_id, selected_file_indexes=[1]),
                BackgroundTasks(),
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("只能选择视频或音频文件", raised.exception.detail)

    def test_torrent_url_to_aria2_web_seed_chain(self) -> None:
        server = self.start_server(FileHandler)
        web_seed_url = f"http://127.0.0.1:{server.server_port}/sample.mp4"
        piece_length = 16 * 1024
        pieces = b"".join(
            hashlib.sha1(FileHandler.content[offset : offset + piece_length]).digest()
            for offset in range(0, len(FileHandler.content), piece_length)
        )
        info = (
            b"d6:lengthi" + str(len(FileHandler.content)).encode() + b"e"
            + b"4:name10:sample.mp4"
            + b"12:piece lengthi" + str(piece_length).encode() + b"e"
            + b"6:pieces" + str(len(pieces)).encode() + b":" + pieces + b"e"
        )
        torrent_data = b"d4:info" + info + b"8:url-list" + str(len(web_seed_url)).encode() + b":" + web_seed_url.encode() + b"e"
        FileHandler.torrent_content = torrent_data
        self.addCleanup(setattr, FileHandler, "torrent_content", None)
        updates: list[dict[str, object]] = []
        logs: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            run_aria2_task(
                "torrent-test",
                f"http://127.0.0.1:{server.server_port}/sample.torrent",
                temp_dir,
                "torrent_url",
                "sample.mp4",
                len(FileHandler.content),
                None,
                None,
                lambda _id, **values: updates.append(values),
                lambda _id, _level, message: logs.append(message),
                lambda _id: None,
                lambda _id: False,
                lambda _id, _process: None,
            )
            output = Path(temp_dir) / "BT" / "torrent-test" / "sample.mp4"
            self.assertEqual(output.read_bytes(), FileHandler.content)
        self.assertEqual(updates[-1]["status"], "completed")
        self.assertEqual(updates[-1]["progress"], 100)
        self.assertTrue(updates[-1]["library_visible"])
        self.assertEqual(
            json.loads(str(updates[-1]["output_files_json"]))[0]["relative_path"],
            "BT/torrent-test/sample.mp4",
        )
        self.assertTrue(any("BT下载完成" in line for line in logs))

    def test_thunder_magnet_routes_to_aria2_bt(self) -> None:
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=sample.mp4"
        thunder_url = "thunder://" + base64.b64encode(f"AA{magnet}ZZ".encode()).decode()
        route = route_input(thunder_url)
        self.assertEqual(route["engine"], "aria2")
        self.assertEqual(route["source_type"], "thunder_bt")
        self.assertEqual(route["resolved_url"], magnet)
        inspected = inspect_non_ytdlp_input(route, thunder_url)
        self.assertEqual(inspected["kind"], "torrent")
        self.assertEqual(inspected["engine"], "aria2")

    def test_bt_task_passes_selected_file_indexes_to_aria2(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "selected.mp4"
            output.write_bytes(FileHandler.content)
            with patch("app.services.engine_tasks.aria2.download_bt", return_value=[output]) as download_bt:
                run_aria2_task(
                    "selection-test",
                    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                    temp_dir,
                    "magnet",
                    "selected.mp4",
                    len(FileHandler.content),
                    None,
                    [0],
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, _message: None,
                    lambda _id: None,
                    lambda: False,
                    lambda _id, _process: None,
                )
        self.assertEqual(download_bt.call_args.args[3], [0])
        self.assertEqual(updates[-1]["status"], "completed")

    def test_direct_task_passes_saved_aria2_settings(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "configured.mp4"
            output.write_bytes(FileHandler.content)
            with patch("app.services.engine_tasks.aria2.download", return_value=output) as download:
                run_aria2_task(
                    "configured-direct",
                    "https://example.com/configured.mp4",
                    temp_dir,
                    "direct",
                    "configured.mp4",
                    len(FileHandler.content),
                    None,
                    None,
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, _message: None,
                    lambda _id: None,
                    lambda _id: False,
                    lambda _id, _process: None,
                    {"split": 8, "max_tries": 10, "retry_wait": 5, "bt_stall_timeout": 180, "download_rate_limit_kbps": 512},
                )
        self.assertEqual(download.call_args.args[7:10], (8, 10, 5))
        self.assertEqual(download.call_args.args[10], 512)
        self.assertEqual(updates[-1]["status"], "completed")

    def test_bt_task_passes_saved_stall_timeout(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "configured-bt.mp4"
            output.write_bytes(FileHandler.content)
            with patch("app.services.engine_tasks.aria2.download_bt", return_value=[output]) as download_bt:
                run_aria2_task(
                    "configured-bt",
                    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                    temp_dir,
                    "magnet",
                    "configured-bt.mp4",
                    len(FileHandler.content),
                    None,
                    None,
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, _message: None,
                    lambda _id: None,
                    lambda _id: False,
                    lambda _id, _process: None,
                    {"split": 5, "max_tries": 3, "retry_wait": 2, "bt_stall_timeout": 180},
                )
        self.assertEqual(download_bt.call_args.args[7], 180)
        self.assertEqual(updates[-1]["status"], "completed")

    def test_magnet_metadata_is_exposed_as_a_selectable_file_list(self) -> None:
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        torrent_data = (
            b"d4:infod5:filesld6:lengthi4e4:pathl9:first.mp4eed6:lengthi5e4:pathl10:second.mp4eee"
            b"4:name6:bundle12:piece lengthi16384e6:pieces20:00000000000000000000ee"
        )
        source = {"url": magnet, "route": route_input(magnet), "torrent_data": None, "torrent_name": None}
        with patch("app.server.aria2.fetch_magnet_metadata", return_value=torrent_data):
            inspected = server.inspect_source(source)
        self.assertEqual(inspected["kind"], "torrent")
        self.assertEqual(inspected["source_type"], "magnet")
        self.assertEqual(inspected["file_count"], 2)
        self.assertEqual([item["path"] for item in inspected["files"]], ["first.mp4", "second.mp4"])
        self.assertEqual(source["torrent_data"], torrent_data)

    def test_multi_video_bt_outputs_are_registered_as_library_items(self) -> None:
        updates: list[dict[str, object]] = []
        registered: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir) / "BT" / "multi-video"
            task_dir.mkdir(parents=True)
            first = task_dir / "first.mp4"
            second = task_dir / "second.mkv"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            with patch("app.services.engine_tasks.aria2.download_bt", return_value=[first, second]):
                run_aria2_task(
                    "multi-video",
                    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                    temp_dir,
                    "magnet",
                    "bundle",
                    11,
                    None,
                    [0, 1],
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, _message: None,
                    lambda _id: None,
                    lambda _id: False,
                    lambda _id, _process: None,
                    None,
                    registered.append,
                )
        outputs = json.loads(str(updates[-1]["output_files_json"]))
        self.assertEqual([item["relative_path"] for item in outputs], [
            "BT/multi-video/first.mp4",
            "BT/multi-video/second.mkv",
        ])
        self.assertFalse(updates[-1]["library_visible"])
        self.assertIsNone(updates[-1]["file_path"])
        self.assertEqual(registered, ["multi-video"])

    def test_single_audio_aria2_output_is_visible_in_library(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "track.mp3"
            audio.write_bytes(b"audio")
            with patch("app.services.engine_tasks.aria2.download", return_value=audio):
                run_aria2_task(
                    "audio-output",
                    "https://example.com/track.mp3",
                    temp_dir,
                    "direct",
                    "track.mp3",
                    len(b"audio"),
                    None,
                    None,
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, _message: None,
                    lambda _id: None,
                    lambda _id: False,
                    lambda _id, _process: None,
                )
        self.assertTrue(updates[-1]["library_visible"])
        self.assertEqual(updates[-1]["file_path"], str(audio))

    def test_unexpected_aria2_failure_reaches_a_terminal_state(self) -> None:
        updates: list[dict[str, object]] = []
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("app.services.engine_tasks.aria2.download", side_effect=OSError("disk unavailable")),
        ):
            run_aria2_task(
                "unexpected-failure",
                "https://example.com/file.mp4",
                temp_dir,
                "direct",
                "file.mp4",
                None,
                None,
                None,
                lambda _id, **values: updates.append(values),
                lambda _id, _level, _message: None,
                lambda _id: None,
                lambda _id: False,
                lambda _id, _process: None,
            )
        self.assertEqual(updates[-1]["status"], "failed")
        self.assertIsNone(updates[-1]["speed"])
        self.assertIsNone(updates[-1]["eta"])

    def test_aria2_pause_reaches_persisted_paused_state(self) -> None:
        updates: list[dict[str, object]] = []
        pause_checks = iter([False, True])
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("app.services.engine_tasks.aria2.download", side_effect=server.aria2.Aria2Error("下载已取消")),
        ):
            run_aria2_task(
                "paused-aria2",
                "https://example.com/file.mp4",
                temp_dir,
                "direct",
                "file.mp4",
                None,
                None,
                None,
                lambda _id, **values: updates.append(values),
                lambda _id, _level, _message: None,
                lambda _id: None,
                lambda _id: False,
                lambda _id, _process: None,
                None,
                None,
                lambda _id: next(pause_checks),
                lambda _id: False,
            )
        self.assertEqual(updates[-1]["status"], "paused")
        self.assertEqual(updates[-1]["restart_pending"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
