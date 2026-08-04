from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app import server
from app.core.models import ResourceSearchRequest


class FakeSearchDownloader:
    def __init__(self, options: dict[str, object]) -> None:
        self.options = options
        self.search_url = ""

    def __enter__(self) -> FakeSearchDownloader:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_info(self, url: str, download: bool) -> dict[str, object]:
        self.search_url = url
        if download:
            raise AssertionError("resource search must not start a download")
        return {
            "_type": "playlist",
            "entries": [
                {
                    "id": "first",
                    "title": "First result",
                    "channel": "Creator",
                    "url": "https://example.com/first",
                    "duration": 90,
                    "thumbnails": [{"url": "https://example.com/first.jpg"}],
                },
                {
                    "id": "duplicate",
                    "title": "Duplicate",
                    "url": "https://example.com/first",
                },
                {
                    "id": "missing-url",
                    "title": "Missing URL",
                },
            ],
        }


class ResourceSearchTests(unittest.TestCase):
    def test_catalog_contains_current_core_keyword_search_providers(self) -> None:
        providers = {provider["key"] for provider in server.resource_search_providers()}

        self.assertTrue({"ytsearch", "bilisearch", "scsearch"}.issubset(providers))

    def test_search_uses_selected_provider_and_returns_downloadable_urls(self) -> None:
        fake_downloader: FakeSearchDownloader | None = None

        def build_downloader(options: dict[str, object]) -> FakeSearchDownloader:
            nonlocal fake_downloader
            fake_downloader = FakeSearchDownloader(options)
            return fake_downloader

        with patch("app.server.yt_dlp.YoutubeDL", side_effect=build_downloader):
            result = server.resource_search(
                ResourceSearchRequest(provider="ytsearch", query="test query", limit=12)
            )

        self.assertIsNotNone(fake_downloader)
        self.assertEqual(fake_downloader.search_url, "ytsearch12:test query")
        self.assertEqual(fake_downloader.options["playlistend"], 12)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["webpage_url"], "https://example.com/first")
        self.assertEqual(result["results"][0]["thumbnail"], "https://example.com/first.jpg")

    def test_search_rejects_unknown_provider(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            server.resource_search(
                ResourceSearchRequest(provider="unknown", query="test", limit=10)
            )

        self.assertEqual(raised.exception.status_code, 422)

    def test_bilibili_search_returns_metadata_from_search_response(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "code": 0,
                "data": {
                    "result": [
                        {
                            "aid": 123,
                            "bvid": "BV123",
                            "author": "Creator",
                            "arcurl": "https://www.bilibili.com/video/BV123",
                            "title": "游览<em class=\"keyword\">甘南</em>",
                            "pic": "//i0.hdslb.com/cover.jpg",
                            "duration": "1:42",
                            "pubdate": 1_700_000_000,
                            "play": 456,
                        }
                    ]
                }
            }
        ).encode()
        opener = MagicMock()
        opener.open.return_value = response

        with patch("app.server.build_opener", return_value=opener):
            result = server.resource_search(
                ResourceSearchRequest(provider="bilisearch", query="甘南", limit=20)
            )

        api_request = opener.open.call_args.args[0]
        self.assertEqual(api_request.get_header("Referer"), "https://search.bilibili.com/")
        self.assertRegex(api_request.get_header("Cookie"), r"^buvid3=[0-9a-f-]+infoc$")
        self.assertEqual(result["results"][0]["title"], "游览甘南")
        self.assertEqual(result["results"][0]["thumbnail"], "https://i0.hdslb.com/cover.jpg")
        self.assertEqual(result["results"][0]["duration"], 102)


if __name__ == "__main__":
    unittest.main(verbosity=2)
