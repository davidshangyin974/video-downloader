from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import server
from app.core import storage
from app.core.models import SubtitlePreferenceRequest


class SubtitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(
            storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3"
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()

        self.media_path = self.root / "movie.mp4"
        self.media_path.write_bytes(b"video")
        record = {
            "id": "video-1",
            "engine": "local",
            "engine_version": "filesystem",
            "source_url": self.media_path.as_uri(),
            "source_platform": "本地文件",
            "source_type": "local_file",
            "status": "completed",
            "title": "movie",
            "file_path": str(self.media_path),
            "file_size": self.media_path.stat().st_size,
            "download_dir": str(self.root),
            "library_visible": 1,
            "task_deleted": 1,
            "file_origin": "local",
            "metadata_json": json.dumps({"media_type": "video"}),
            "created_at": "2026-08-01T01:00:00+00:00",
            "updated_at": "2026-08-01T01:00:00+00:00",
        }
        columns = list(record)
        with storage.connection() as database:
            database.execute(
                f"INSERT INTO downloads ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                list(record.values()),
            )

    def test_lists_only_same_stem_vtt_and_srt_tracks(self) -> None:
        (self.root / "movie.zh-Hans.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (self.root / "movie.en.forced.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
        (self.root / "movie.ass").write_text("unsupported")
        (self.root / "other.zh.vtt").write_text("WEBVTT\n")

        response = server.list_video_subtitles("video-1")

        self.assertEqual(
            [track["filename"] for track in response["tracks"]],
            ["movie.en.forced.srt", "movie.zh-Hans.vtt"],
        )
        self.assertEqual(response["tracks"][0]["label"], "en.forced")
        self.assertEqual(response["tracks"][0]["language"], "en")
        self.assertEqual(response["tracks"][1]["language"], "zh-Hans")
        self.assertIsNone(response["preferred_filename"])

    def test_srt_is_converted_to_utf8_webvtt(self) -> None:
        subtitle = self.root / "movie.zh.srt"
        subtitle.write_bytes("1\r\n00:00:01,250 --> 00:00:03,000\r\n中文字幕\r\n".encode("gb18030"))

        converted = server.srt_to_webvtt(subtitle).decode("utf-8")

        self.assertTrue(converted.startswith("WEBVTT\n\n"))
        self.assertIn("00:00:01.250 --> 00:00:03.000", converted)
        self.assertIn("中文字幕", converted)

    def test_preference_is_persisted_and_can_be_cleared(self) -> None:
        subtitle = self.root / "movie.zh.vtt"
        subtitle.write_text("WEBVTT\n", encoding="utf-8")

        selected = server.update_subtitle_preference(
            "video-1", SubtitlePreferenceRequest(filename=subtitle.name)
        )
        reopened = server.list_video_subtitles("video-1")
        cleared = server.update_subtitle_preference(
            "video-1", SubtitlePreferenceRequest(filename=None)
        )

        self.assertEqual(selected["preferred_filename"], subtitle.name)
        self.assertEqual(reopened["preferred_filename"], subtitle.name)
        self.assertIsNone(cleared["preferred_filename"])

    def test_rejects_unknown_track_and_out_of_range_index(self) -> None:
        with self.assertRaises(HTTPException) as preference_error:
            server.update_subtitle_preference(
                "video-1", SubtitlePreferenceRequest(filename="movie.ass")
            )
        with self.assertRaises(HTTPException) as index_error:
            server.get_video_subtitle_file("video-1", 0)

        self.assertEqual(preference_error.exception.status_code, 422)
        self.assertEqual(index_error.exception.status_code, 404)

    def test_ignores_same_stem_symbolic_link(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.vtt"
        outside.write_text("WEBVTT\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        (self.root / "movie.zh.vtt").symlink_to(outside)

        self.assertEqual(server.list_video_subtitles("video-1")["tracks"], [])


if __name__ == "__main__":
    unittest.main()
