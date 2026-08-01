from __future__ import annotations

import base64
import unittest
from email.message import Message
from urllib.error import URLError
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st

from app.engines.inputs import (
    InputError,
    _decode_torrent_value,
    _parse_bencode,
    decode_thunder_url,
    direct_file_name,
    direct_file_probe,
    inspect_non_ytdlp_input,
    inspect_torrent,
    is_direct_file_url,
    is_media_file,
    is_torrent_url,
    route_input,
)


FUZZ_SETTINGS = settings(
    max_examples=500,
    deadline=None,
    derandomize=True,
    database=None,
    print_blob=True,
)


def bencode(value: object) -> bytes:
    if isinstance(value, int):
        return f"i{value}e".encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(key) + bencode(item) for key, item in sorted(value.items())) + b"e"
    raise TypeError(type(value).__name__)


class InputEdgeCaseTests(unittest.TestCase):
    def test_thunder_decode_rejects_empty_invalid_and_unsupported_payloads(self) -> None:
        with self.assertRaisesRegex(InputError, "内容为空"):
            decode_thunder_url("thunder://")
        with self.assertRaisesRegex(InputError, "无法解码"):
            decode_thunder_url("thunder://%%%\ud800")
        unsupported = base64.b64encode(b"AAfile:///tmp/movie.mp4ZZ").decode()
        with self.assertRaisesRegex(InputError, "资源类型暂不支持"):
            decode_thunder_url(f"thunder://{unsupported}")
        malformed = base64.b64encode(b"AAhttp://[invalidZZ").decode()
        with self.assertRaisesRegex(InputError, "资源地址无效"):
            decode_thunder_url(f"thunder://{malformed}")

    def test_thunder_decode_accepts_wrapped_and_unwrapped_supported_urls(self) -> None:
        for target in (
            "https://example.com/movie.mp4",
            "ftp://example.com/movie.mp4",
            "magnet:?xt=urn:btih:abcdef",
            "ed2k://|file|movie.mp4|1|hash|/",
        ):
            with self.subTest(target=target):
                wrapped = base64.b64encode(f"AA{target}ZZ".encode()).decode().rstrip("=")
                self.assertEqual(decode_thunder_url(f" thunder://{wrapped} "), target)
        plain = base64.b64encode(b"https://example.com/plain.mp4").decode()
        self.assertEqual(decode_thunder_url(f"thunder://{plain}"), "https://example.com/plain.mp4")

    def test_route_input_covers_engines_and_rejections(self) -> None:
        self.assertEqual(route_input("", "auto", has_torrent_file=True)["source_type"], "torrent_file")
        self.assertEqual(route_input("", "qbittorrent", has_torrent_file=True)["engine"], "qbittorrent")
        with self.assertRaisesRegex(InputError, "种子文件只能"):
            route_input("", "yt-dlp", has_torrent_file=True)
        with self.assertRaisesRegex(InputError, "请选择"):
            route_input("https://example.com", "invalid")
        with self.assertRaisesRegex(InputError, "请输入网页链接"):
            route_input("movie.mp4")
        with self.assertRaisesRegex(InputError, "请输入有效"):
            route_input("http://[broken")
        with self.assertRaisesRegex(InputError, "eD2K"):
            route_input("ed2k://|file|movie.mp4|1|hash|/")
        with self.assertRaisesRegex(InputError, "有效的网页链接"):
            route_input("file:///tmp/movie.mp4")
        with self.assertRaisesRegex(InputError, "磁力链接需要"):
            route_input("magnet:?xt=urn:btih:abcdef", "yt-dlp")
        with self.assertRaisesRegex(InputError, "种子地址只能"):
            route_input("https://example.com/file.TORRENT?download=1", "yt-dlp")
        with self.assertRaisesRegex(InputError, "qBittorrent 只用于"):
            route_input("https://example.com/page", "qbittorrent")

        self.assertEqual(route_input("magnet:?xt=urn:btih:abcdef")["engine"], "aria2")
        self.assertEqual(route_input("magnet:?xt=urn:btih:abcdef", "qbittorrent")["engine"], "qbittorrent")
        self.assertEqual(route_input("https://example.com/file.torrent")["source_type"], "torrent_url")
        self.assertEqual(route_input("https://example.com/movie.mp4")["engine"], "aria2")
        self.assertEqual(route_input("https://example.com/watch?v=1")["engine"], "yt-dlp")
        self.assertEqual(route_input("ftp://example.com/movie.bin", "aria2")["source_type"], "direct")
        self.assertEqual(route_input("https://example.com/page", "yt-dlp")["source_type"], "webpage")

        target = "magnet:?xt=urn:btih:abcdef"
        thunder = "thunder://" + base64.b64encode(f"AA{target}ZZ".encode()).decode()
        routed = route_input(thunder)
        self.assertEqual(routed["source_type"], "thunder_bt")
        self.assertEqual(routed["resolved_url"], target)

    def test_file_helpers_cover_names_extensions_and_content_types(self) -> None:
        self.assertTrue(is_torrent_url("https://example.com/a.TORRENT?download=1"))
        self.assertFalse(is_torrent_url("https://example.com/a.torrent.zip"))
        self.assertTrue(is_direct_file_url("https://example.com/%E7%94%B5%E5%BD%B1.MP4"))
        self.assertFalse(is_direct_file_url("https://example.com/watch?v=1"))
        self.assertEqual(direct_file_name("https://example.com/a%20b.mp4?x=1"), "a b.mp4")
        self.assertEqual(direct_file_name("https://example.com/"), "未命名文件")
        self.assertTrue(is_media_file("movie.mkv"))
        self.assertTrue(is_media_file("unknown.bin", " Audio/MPEG; charset=binary"))
        self.assertFalse(is_media_file("readme.txt", "application/octet-stream"))

    def test_direct_file_probe_handles_headers_and_network_failure(self) -> None:
        headers = Message()
        headers["Content-Length"] = "123"
        headers["Content-Type"] = "video/mp4"
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''my%20movie.mp4"
        response = MagicMock()
        response.headers = headers
        response.__enter__.return_value = response
        with patch("app.engines.inputs.urlopen", return_value=response):
            result = direct_file_probe("https://example.com/download")
        self.assertEqual(result, {"file_name": "my movie.mp4", "file_size": 123, "content_type": "video/mp4"})

        headers.replace_header("Content-Length", "unknown")
        del headers["Content-Disposition"]
        with patch("app.engines.inputs.urlopen", return_value=response):
            result = direct_file_probe("https://example.com/fallback.mp4")
        self.assertEqual(result["file_name"], "fallback.mp4")
        self.assertIsNone(result["file_size"])

        with patch("app.engines.inputs.urlopen", side_effect=URLError("offline")):
            result = direct_file_probe("https://example.com/offline.mp4")
        self.assertEqual(result, {"file_name": "offline.mp4", "file_size": None, "content_type": None})

    def test_bencode_parser_and_torrent_inspection_cover_valid_shapes(self) -> None:
        self.assertEqual(_parse_bencode(b"i42e"), (42, 4))
        self.assertEqual(_parse_bencode(b"l1:ai2ee"), ([b"a", 2], 8))
        self.assertEqual(_parse_bencode(b"d1:ai2ee"), ({b"a": 2}, 8))
        self.assertEqual(_parse_bencode(b"3:abc"), (b"abc", 5))
        self.assertEqual(_decode_torrent_value(b"\xffname"), "�name")
        self.assertEqual(_decode_torrent_value(123), "")

        single = bencode({b"info": {b"length": 123, b"name": b"movie.mp4"}}) + b"\r\n"
        inspected = inspect_torrent(single, "fallback.torrent")
        self.assertEqual(inspected["title"], "movie.mp4")
        self.assertEqual(inspected["file_size"], 123)
        self.assertEqual(inspected["files"], [{"index": 0, "path": "movie.mp4", "size": 123}])

        multi = bencode(
            {
                b"info": {
                    b"name": b"collection",
                    b"files": [
                        {b"length": 10, b"path": [b"video", b"one.mp4"]},
                        {b"length": b"unknown", b"path": [b"readme.txt"]},
                        b"ignored",
                        {b"length": 5, b"path": b"ignored"},
                    ],
                }
            }
        )
        inspected = inspect_torrent(multi, None)
        self.assertEqual(inspected["file_count"], 2)
        self.assertEqual(inspected["file_size"], 10)
        self.assertEqual(inspected["files"][0]["path"], "video/one.mp4")

    def test_bencode_parser_rejects_each_invalid_shape(self) -> None:
        invalid_payloads = (
            b"",
            b"i12",
            b"ixxe",
            b"dlee",
            b"3abc",
            b"1x:abc",
            b"x:abc",
            b"5:abc",
            b"not-bencode",
            bencode({b"name": b"missing-info"}),
            bencode({b"info": b"not-a-dictionary"}),
            bencode({b"info": {b"name": b"ok"}}) + b"trailing",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(InputError):
                inspect_torrent(payload, None)

        deeply_nested = b"l" * 1200 + b"e" * 1200
        with self.assertRaisesRegex(InputError, "层级过深"):
            inspect_torrent(deeply_nested, None)

    def test_non_ytdlp_inspection_covers_torrent_direct_and_magnet(self) -> None:
        route = {"engine": "qbittorrent", "source_type": "torrent_file", "resolved_url": ""}
        with self.assertRaisesRegex(InputError, "重新选择"):
            inspect_non_ytdlp_input(route, "")

        torrent = bencode({b"info": {b"length": 1, b"name": b"movie.mp4"}})
        inspected = inspect_non_ytdlp_input(route, "", torrent, "movie.torrent")
        self.assertEqual(inspected["engine"], "qbittorrent")
        self.assertIn("文件列表", inspected["message"])

        direct_route = {"engine": "aria2", "source_type": "direct", "resolved_url": "https://example.com/movie.mp4"}
        with patch(
            "app.engines.inputs.direct_file_probe",
            return_value={"file_name": "movie.mp4", "file_size": 5, "content_type": "video/mp4"},
        ):
            inspected = inspect_non_ytdlp_input(direct_route, direct_route["resolved_url"])
        self.assertEqual(inspected["resolved_host"], "example.com")
        with (
            patch(
                "app.engines.inputs.direct_file_probe",
                return_value={"file_name": "manual.pdf", "file_size": 5, "content_type": "application/pdf"},
            ),
            self.assertRaisesRegex(InputError, "只支持下载视频和音频文件"),
        ):
            inspect_non_ytdlp_input(direct_route, direct_route["resolved_url"])

        magnet_route = {
            "engine": "aria2",
            "source_type": "magnet",
            "resolved_url": "magnet:?xt=urn:btih:abcdef&dn=My%20Movie",
        }
        inspected = inspect_non_ytdlp_input(magnet_route, magnet_route["resolved_url"])
        self.assertEqual(inspected["title"], "My Movie")


class InputFuzzTests(unittest.TestCase):
    @FUZZ_SETTINGS
    @given(st.text(max_size=300), st.sampled_from(["auto", "yt-dlp", "aria2", "qbittorrent", "invalid"]))
    def test_fuzz_route_input_has_only_documented_results(self, value: str, hint: str) -> None:
        try:
            routed = route_input(value, hint)
        except InputError:
            return
        self.assertIn(routed["engine"], {"yt-dlp", "aria2", "qbittorrent"})
        self.assertIn(
            routed["source_type"],
            {"webpage", "direct", "magnet", "torrent_url", "thunder", "thunder_bt"},
        )
        self.assertIsInstance(routed["resolved_url"], str)

    @FUZZ_SETTINGS
    @given(st.text(max_size=300))
    def test_fuzz_thunder_decoder_never_leaks_parser_exceptions(self, payload: str) -> None:
        try:
            decoded = decode_thunder_url(f"thunder://{payload}")
        except InputError:
            return
        self.assertIn(urlparse(decoded).scheme, {"http", "https", "ftp", "magnet", "ed2k"})

    @FUZZ_SETTINGS
    @given(st.binary(max_size=512), st.one_of(st.none(), st.text(max_size=60)))
    def test_fuzz_torrent_parser_never_leaks_internal_exceptions(self, payload: bytes, name: str | None) -> None:
        try:
            inspected = inspect_torrent(payload, name)
        except InputError:
            return
        self.assertEqual(inspected["kind"], "torrent")
        self.assertEqual(inspected["file_count"], len(inspected["files"]))
        self.assertLessEqual(len(inspected["files"]), 100)

    @FUZZ_SETTINGS
    @given(st.binary(max_size=40), st.integers(min_value=0, max_value=10_000_000))
    def test_fuzz_valid_single_file_torrent_preserves_size(self, name: bytes, size: int) -> None:
        torrent = bencode({b"info": {b"length": size, b"name": name}})
        inspected = inspect_torrent(torrent, "fallback.torrent")
        self.assertEqual(inspected["files"][0]["size"], size)
        self.assertEqual(inspected["file_size"], size or None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
