from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage
from app.core.models import DownloadRequest, InspectRequest
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


class SlowRangeHandler(BaseHTTPRequestHandler):
    content = bytes(range(256)) * (64 * 1024)
    ranges: list[str | None] = []
    bytes_sent = 0
    lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        with cls.lock:
            cls.ranges = []
            cls.bytes_sent = 0

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.content)))
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        range_header = self.headers.get("Range")
        start = 0
        end = len(self.content) - 1
        if range_header and range_header.startswith("bytes="):
            bounds = range_header[6:].split("-", 1)
            start = int(bounds[0] or 0)
            if bounds[1]:
                end = min(int(bounds[1]), end)
        payload = self.content[start : end + 1]
        with self.lock:
            self.ranges.append(range_header)
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.content)}")
        self.end_headers()
        try:
            for offset in range(0, len(payload), 64 * 1024):
                chunk = payload[offset : offset + 64 * 1024]
                self.wfile.write(chunk)
                self.wfile.flush()
                with self.lock:
                    self.__class__.bytes_sent += len(chunk)
                time.sleep(0.006)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        return


class EngineChainTests(unittest.TestCase):
    def test_download_formats_list_best_quality_first(self) -> None:
        formats = server.format_options({
            "formats": [
                {"format_id": "360", "vcodec": "h264", "acodec": "aac", "height": 360, "ext": "mp4"},
                {"format_id": "1080", "vcodec": "h264", "acodec": "none", "height": 1080, "ext": "mp4"},
            ]
        })

        self.assertEqual([item["resolution"] for item in formats], ["1080p", "360p"])
        self.assertEqual(formats[0]["format_id"], "1080+bestaudio/best")

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
                "formats": [{
                    "vcodec": "none",
                    "acodec": "none",
                    "protocol": "m3u8_native",
                    "ext": "txt",
                }],
            }
            with self.assertRaisesRegex(InputError, "只支持下载视频和音频内容"):
                server.inspect_url("https://example.com/document")

    def test_ytdlp_hls_result_without_codec_metadata_is_accepted(self) -> None:
        with patch("app.server.yt_dlp.YoutubeDL") as downloader:
            downloader.return_value.__enter__.return_value.extract_info.return_value = {
                "title": "HLS video",
                "formats": [{
                    "format_id": "0",
                    "vcodec": None,
                    "acodec": None,
                    "protocol": "m3u8_native",
                    "ext": "mp4",
                }],
            }
            inspected = server.inspect_url("https://example.com/chunklist.m3u8")

        self.assertEqual(inspected["kind"], "video")
        self.assertEqual(inspected["title"], "HLS video")

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

    def test_remote_torrent_url_is_parsed_before_task_creation(self) -> None:
        torrent_data = (
            b"d4:infod6:lengthi8e4:name9:movie.mp412:piece lengthi16384e"
            b"6:pieces20:00000000000000000000ee"
        )
        source = {
            "url": "https://example.com/movie.torrent",
            "route": route_input("https://example.com/movie.torrent"),
            "torrent_data": None,
            "torrent_name": None,
        }
        with patch("app.server.fetch_torrent_url", return_value=(torrent_data, "movie.torrent")):
            inspected = server.inspect_source(source)
        self.assertEqual(inspected["engine"], "aria2")
        self.assertEqual(inspected["source_type"], "torrent_url")
        self.assertEqual(inspected["files"], [{"index": 0, "path": "movie.mp4", "size": 8}])
        self.assertEqual(source["torrent_data"], torrent_data)
        self.assertEqual(source["bt_info_hash"], inspected["info_hash"])

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
        torrent_data = (
            b"d4:infod5:filesld6:lengthi4e4:pathl9:first.mp4eed6:lengthi5e4:pathl10:second.mp4eee"
            b"4:name6:bundle12:piece lengthi16384e6:pieces20:00000000000000000000ee"
        )
        info_hash = server.inspect_torrent(torrent_data, None)["info_hash"]
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
        source = {"url": magnet, "route": route_input(magnet), "torrent_data": None, "torrent_name": None}
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            patch.object(server, "MAGNET_METADATA_CACHE_DIR", Path(cache_dir)),
            patch("app.server.fetch_public_magnet_metadata", return_value=None),
            patch("app.server.aria2.fetch_magnet_metadata", return_value=torrent_data) as fetch_metadata,
        ):
            inspected = server.inspect_source(source)
            cached_source = {"url": magnet, "route": route_input(magnet), "torrent_data": None, "torrent_name": None}
            cached_inspected = server.inspect_source(cached_source)
        self.assertEqual(inspected["kind"], "torrent")
        self.assertEqual(inspected["source_type"], "magnet")
        self.assertEqual(inspected["file_count"], 2)
        self.assertEqual([item["path"] for item in inspected["files"]], ["first.mp4", "second.mp4"])
        self.assertEqual(source["torrent_data"], torrent_data)
        self.assertEqual(cached_inspected["info_hash"], info_hash)
        self.assertEqual(cached_source["torrent_data"], torrent_data)
        fetch_metadata.assert_called_once()

    def test_magnet_metadata_must_match_requested_info_hash(self) -> None:
        torrent_data = (
            b"d4:infod6:lengthi8e4:name9:movie.mp412:piece lengthi16384e"
            b"6:pieces20:00000000000000000000ee"
        )
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        source = {"url": magnet, "route": route_input(magnet), "torrent_data": None, "torrent_name": None}
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            patch.object(server, "MAGNET_METADATA_CACHE_DIR", Path(cache_dir)),
            patch("app.server.fetch_public_magnet_metadata", return_value=None),
            patch("app.server.aria2.fetch_magnet_metadata", return_value=torrent_data),
            self.assertRaisesRegex(InputError, "Info Hash 不一致"),
        ):
            server.inspect_source(source)

    def test_public_magnet_metadata_cache_is_hash_checked_before_dht(self) -> None:
        torrent_data = (
            b"d4:infod6:lengthi8e4:name9:movie.mp412:piece lengthi16384e"
            b"6:pieces20:00000000000000000000ee"
        )
        info_hash = server.inspect_torrent(torrent_data, None)["info_hash"]
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
        source = {"url": magnet, "route": route_input(magnet), "torrent_data": None, "torrent_name": None}
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            patch.object(server, "MAGNET_METADATA_CACHE_DIR", Path(cache_dir)),
            patch("app.server.fetch_torrent_url", return_value=(torrent_data, "cached.torrent")) as fetch_remote,
            patch("app.server.aria2.fetch_magnet_metadata") as fetch_dht,
        ):
            inspected = server.inspect_source(source)
        self.assertEqual(inspected["info_hash"], info_hash)
        self.assertIn(info_hash.upper(), fetch_remote.call_args.args[0])
        fetch_dht.assert_not_called()

    def test_public_magnet_metadata_cache_rejects_hash_mismatch(self) -> None:
        torrent_data = (
            b"d4:infod6:lengthi8e4:name9:movie.mp412:piece lengthi16384e"
            b"6:pieces20:00000000000000000000ee"
        )
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        with patch("app.server.fetch_torrent_url", return_value=(torrent_data, "wrong.torrent")):
            self.assertIsNone(server.fetch_public_magnet_metadata(magnet))

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


class TorrentProductFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()

    def start_server(self, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http_server.shutdown)
        self.addCleanup(http_server.server_close)
        return http_server

    def test_remote_torrent_completes_inspect_select_create_and_download(self) -> None:
        http_server = self.start_server(FileHandler)
        web_seed_url = f"http://127.0.0.1:{http_server.server_port}/sample.mp4"
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

        torrent_url = f"http://127.0.0.1:{http_server.server_port}/sample.torrent"
        source = server.prepare_inspect_source(InspectRequest(url=torrent_url))
        media = server.inspect_source(source)
        inspect_id = "remote-torrent-product-flow"
        server.inspect_jobs[inspect_id] = {"status": "completed", "source": source, "media": media}
        self.addCleanup(server.inspect_jobs.pop, inspect_id, None)
        created = server.create_download(
            DownloadRequest(
                inspect_id=inspect_id,
                selected_file_indexes=[0],
                download_dir=str(self.root / "downloads"),
            ),
            BackgroundTasks(),
        )
        task, args = server.existing_download_task(server.download_record(created["id"]))
        self.assertIs(task, run_aria2_task)
        task(*args)

        completed = server.download_record(created["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(Path(str(completed["file_path"])).read_bytes(), FileHandler.content)

    def test_bt_download_resumes_after_a_real_process_interruption(self) -> None:
        SlowRangeHandler.reset()
        http_server = self.start_server(SlowRangeHandler)
        web_seed_url = f"http://127.0.0.1:{http_server.server_port}/resume.mp4"
        piece_length = 256 * 1024
        pieces = b"".join(
            hashlib.sha1(SlowRangeHandler.content[offset : offset + piece_length]).digest()
            for offset in range(0, len(SlowRangeHandler.content), piece_length)
        )
        info = (
            b"d6:lengthi" + str(len(SlowRangeHandler.content)).encode() + b"e"
            + b"4:name10:resume.mp4"
            + b"12:piece lengthi" + str(piece_length).encode() + b"e"
            + b"6:pieces" + str(len(pieces)).encode() + b":" + pieces + b"e"
        )
        torrent_data = b"d4:info" + info + b"8:url-list" + str(len(web_seed_url)).encode() + b":" + web_seed_url.encode() + b"e"
        updates: list[dict[str, object]] = []
        logs: list[str] = []
        interrupted = threading.Event()

        def should_interrupt(_download_id: str) -> bool:
            if SlowRangeHandler.bytes_sent >= 512 * 1024:
                interrupted.set()
            return interrupted.is_set()

        run_aria2_task(
            "resume-bt",
            "torrent-file:resume.torrent",
            str(self.root / "downloads"),
            "torrent_file",
            "resume.mp4",
            len(SlowRangeHandler.content),
            torrent_data,
            [0],
            lambda _id, **values: updates.append(values),
            lambda _id, _level, message: logs.append(message),
            lambda _id: None,
            lambda _id: False,
            lambda _id, _process: None,
            {"bt_stall_timeout": 30, "dht_state_path": str(self.root / "dht.dat")},
            None,
            lambda _id: False,
            should_interrupt,
        )

        task_dir = self.root / "downloads" / "BT" / "resume-bt"
        self.assertEqual(updates[-1]["status"], "interrupted")
        self.assertEqual(updates[-1]["restart_pending"], 1)
        self.assertTrue(any(task_dir.rglob("*.aria2")))
        first_request_count = len(SlowRangeHandler.ranges)

        interrupted.clear()
        updates.clear()
        run_aria2_task(
            "resume-bt",
            "torrent-file:resume.torrent",
            str(self.root / "downloads"),
            "torrent_file",
            "resume.mp4",
            len(SlowRangeHandler.content),
            torrent_data,
            [0],
            lambda _id, **values: updates.append(values),
            lambda _id, _level, message: logs.append(message),
            lambda _id: None,
            lambda _id: False,
            lambda _id, _process: None,
            {"bt_stall_timeout": 30, "dht_state_path": str(self.root / "dht.dat")},
            None,
            lambda _id: False,
            lambda _id: False,
        )

        output = task_dir / "resume.mp4"
        resumed_ranges = SlowRangeHandler.ranges[first_request_count:]
        self.assertEqual(updates[-1]["status"], "completed")
        self.assertEqual(output.read_bytes(), SlowRangeHandler.content)
        self.assertFalse(any(task_dir.rglob("*.aria2")))
        self.assertTrue(any(value and value.startswith("bytes=") for value in resumed_ranges))
        self.assertTrue(any("校验断点并继续下载" in message for message in logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
