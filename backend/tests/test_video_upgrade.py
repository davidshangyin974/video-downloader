from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage
from app.core.models import DownloadRequest, VideoUpgradeFinalizeRequest, VideoUpgradeRequest


class VideoUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        database_patch.start()
        self.addCleanup(database_patch.stop)
        storage.initialize_database()
        with storage.connection() as database:
            database.execute(
                "UPDATE app_settings SET value = ? WHERE key = 'download_dir'",
                (f'"{self.root}"',),
            )

    def insert_video(self, video_id: str, *, resolution: str = "720p", upgrade_from_id: str | None = None) -> Path:
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"video")
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_platform, source_type,
                    status, title, resolution, file_path, file_size, download_dir,
                    library_visible, file_origin, upgrade_from_id, created_at, updated_at
                ) VALUES (?, 'yt-dlp', 'test', 'https://example.com/watch/1', 'example.com',
                          'webpage', 'completed', ?, ?, ?, ?, ?, 1, 'downloaded', ?, 'now', 'now')
                """,
                (video_id, video_id, resolution, str(path), path.stat().st_size, str(self.root), upgrade_from_id),
            )
        return path

    @staticmethod
    def inspected() -> dict[str, object]:
        return {
            "kind": "video",
            "title": "source",
            "webpage_url": "https://example.com/watch/1",
            "video_id": "one",
            "formats": [
                {"format_id": "720", "label": "720p · MP4", "resolution": "720p", "file_size": 10},
                {"format_id": "1080+bestaudio/best", "label": "1080p · MP4", "resolution": "1080p", "file_size": 20},
                {"format_id": "2160+bestaudio/best", "label": "2160p · WEBM", "resolution": "2160p", "file_size": 40},
            ],
        }

    def test_options_only_return_formats_above_current_resolution(self) -> None:
        self.insert_video("old")
        with patch.object(server, "inspect_url", return_value=self.inspected()):
            result = server.video_upgrade_options("old")
        self.assertEqual([item["height"] for item in result["candidates"]], [1080, 2160])

    def test_preferred_format_is_last_for_upgrade_default(self) -> None:
        self.insert_video("old")
        media = {
            **self.inspected(),
            "formats": [
                {"format_id": "preferred", "label": "1080p · MP4", "resolution": "1080p", "file_size": 10},
                {"format_id": "fallback", "label": "1080p · WEBM", "resolution": "1080p", "file_size": 20},
            ],
        }
        with patch.object(server, "inspect_url", return_value=media):
            result = server.video_upgrade_options("old")

        self.assertEqual(
            [item["format_id"] for item in result["candidates"]],
            ["fallback", "preferred"],
        )

    def test_upgrade_route_creates_linked_download_and_rejects_stale_format(self) -> None:
        self.insert_video("old")
        options = {
            "source_url": "https://example.com/watch/1",
            "candidates": [{"format_id": "1080+bestaudio/best"}],
        }
        source = {
            "url": "https://example.com/watch/1",
            "route": {"engine": "yt-dlp", "source_type": "webpage", "resolved_url": "https://example.com/watch/1"},
        }
        media = {**self.inspected(), "resolution": "1080p"}
        with (
            patch.object(server, "video_upgrade_options", return_value=options),
            patch.object(server, "source_from_download_request", return_value=(source, media)),
            patch.object(server, "ensure_disk_capacity"),
        ):
            result = server.start_video_upgrade(
                "old", VideoUpgradeRequest(format_id="1080+bestaudio/best"), BackgroundTasks()
            )
        self.assertEqual(result["upgrade_from_id"], "old")
        self.assertEqual(result["requested_format"], "1080+bestaudio/best")

        with patch.object(server, "video_upgrade_options", return_value=options):
            with self.assertRaises(HTTPException) as context:
                server.start_video_upgrade("old", VideoUpgradeRequest(format_id="stale"), BackgroundTasks())
        self.assertEqual(context.exception.status_code, 409)

    def test_generic_upgrade_bypass_still_requires_same_existing_source(self) -> None:
        self.insert_video("old")
        source = {
            "url": "https://other.example/watch/2",
            "route": {"engine": "yt-dlp", "source_type": "webpage", "resolved_url": "https://other.example/watch/2"},
        }
        with patch.object(server, "source_from_download_request", return_value=(source, self.inspected())):
            with self.assertRaises(HTTPException) as context:
                server.create_download(
                    DownloadRequest(url=source["url"], upgrade_from_id="old"), BackgroundTasks()
                )
        self.assertEqual(context.exception.status_code, 409)

    def test_completed_upgrade_must_be_finalized_before_creating_another(self) -> None:
        self.insert_video("old")
        self.insert_video("pending", resolution="1080p", upgrade_from_id="old")

        with self.assertRaises(HTTPException) as context:
            server.video_upgrade_options("old")

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("等待确认", str(context.exception.detail))

    def test_finalize_keeps_both_or_deletes_old_only_after_confirmation(self) -> None:
        old_path = self.insert_video("old")
        self.insert_video("new", resolution="1080p", upgrade_from_id="old")
        kept = server.finalize_video_upgrade(
            "new", VideoUpgradeFinalizeRequest(confirm=True, remove_original_file=False)
        )
        self.assertTrue(kept["original"]["kept"])
        self.assertIsNone(server.download_record("new")["upgrade_from_id"])
        self.assertTrue(old_path.exists())

        with storage.connection() as database:
            database.execute(
                """
                UPDATE downloads
                SET title = '我的标题', uploader = '我的作者', favorite = 1,
                    watched = 1, watch_position = 42, last_watched_at = '2026-08-02T00:00:00+00:00'
                WHERE id = 'old'
                """
            )
            database.execute("UPDATE downloads SET upgrade_from_id = 'old' WHERE id = 'new'")
        with patch.object(server, "move_to_trash", side_effect=lambda path: path.unlink()):
            removed = server.finalize_video_upgrade(
                "new", VideoUpgradeFinalizeRequest(confirm=True, remove_original_file=True)
            )
        self.assertEqual(removed["original"]["id"], "old")
        self.assertFalse(old_path.exists())
        upgraded = server.download_record("new")
        self.assertEqual(upgraded["title"], "我的标题")
        self.assertEqual(upgraded["uploader"], "我的作者")
        self.assertEqual(upgraded["favorite"], 1)
        self.assertEqual(upgraded["watched"], 1)
        self.assertEqual(upgraded["watch_position"], 42)
        with self.assertRaises(HTTPException):
            server.download_record("old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
