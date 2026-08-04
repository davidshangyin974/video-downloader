from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage
from app.core.models import (
    SourceFollowDownloadRequest,
    SourceFollowRequest,
    SourceFollowUpdateRequest,
)


class SourceFollowTests(unittest.TestCase):
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

    @staticmethod
    def playlist(entries: list[dict[str, object]]) -> dict[str, object]:
        return {
            "kind": "playlist",
            "external_id": "collection-1",
            "title": "关注合集",
            "uploader": "作者",
            "thumbnail": "https://example.com/cover.jpg",
            "source_url": "https://example.com/collection",
            "source_platform": "example.com",
            "entries": entries,
        }

    def insert_completed(self, url: str) -> None:
        media = self.root / "completed.mp4"
        media.write_bytes(b"video")
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_platform,
                    webpage_url, status, file_path, download_dir,
                    library_visible, created_at, updated_at
                ) VALUES ('completed', 'yt-dlp', 'test', ?, 'example.com', ?,
                          'completed', ?, ?, 1, 'now', 'now')
                """,
                (url, url, str(media), str(self.root)),
            )

    def test_create_and_check_follow_only_keep_new_entries(self) -> None:
        old_url = "https://example.com/watch/old"
        new_url = "https://example.com/watch/new"
        newest_url = "https://example.com/watch/newest"
        self.insert_completed(old_url)
        initial = self.playlist([
            {"title": "旧视频", "webpage_url": old_url, "playlist_index": 1},
            {"title": "新视频", "webpage_url": new_url, "playlist_index": 2},
        ])
        refreshed = self.playlist([
            *initial["entries"],
            {"title": "最新视频", "webpage_url": newest_url, "playlist_index": 3},
        ])

        with patch.object(server, "inspect_follow_source", return_value=initial):
            follow = server.create_source_follow(
                SourceFollowRequest(url="https://example.com/collection")
            )

        self.assertEqual(follow["new_count"], 1)
        self.assertEqual(follow["entries"][0]["webpage_url"], new_url)
        self.assertTrue(follow["check_on_startup"])

        with patch.object(server, "inspect_follow_source", return_value=refreshed):
            checked = server.check_source_follow(follow["id"])

        self.assertEqual(
            [entry["webpage_url"] for entry in checked["entries"]],
            [new_url, newest_url],
        )
        self.assertIsNone(checked["last_error"])

    def test_follow_setting_and_failure_state_are_persisted(self) -> None:
        with patch.object(server, "inspect_follow_source", return_value=self.playlist([])):
            follow = server.create_source_follow(
                SourceFollowRequest(url="https://example.com/collection")
            )
        updated = server.update_source_follow(
            follow["id"], SourceFollowUpdateRequest(check_on_startup=False)
        )
        self.assertFalse(updated["check_on_startup"])

        with patch.object(
            server,
            "inspect_follow_source",
            side_effect=HTTPException(status_code=422, detail="源不可用"),
        ):
            with self.assertRaises(HTTPException) as context:
                server.check_source_follow(follow["id"])
        self.assertEqual(context.exception.status_code, 502)
        self.assertEqual(server.source_follow_record(follow["id"])["last_error"], "源不可用")

    def test_selected_new_entries_create_tasks_and_leave_failures_pending(self) -> None:
        first_url = "https://example.com/watch/first"
        second_url = "https://example.com/watch/second"
        with patch.object(
            server,
            "inspect_follow_source",
            return_value=self.playlist([
                {"title": "第一条", "webpage_url": first_url, "playlist_index": 1},
                {"title": "第二条", "webpage_url": second_url, "playlist_index": 2},
            ]),
        ):
            follow = server.create_source_follow(
                SourceFollowRequest(url="https://example.com/collection")
            )

        batch_result = {
            "created": 1,
            "failed": 1,
            "successes": [{"index": 0, "url": first_url, "download": {"id": "task-1"}}],
            "failures": [{"index": 1, "url": second_url, "error": "失败"}],
        }
        with patch.object(server, "create_download_batch", return_value=batch_result):
            result = server.download_source_follow_entries(
                follow["id"],
                SourceFollowDownloadRequest(entry_urls=[first_url, second_url]),
                BackgroundTasks(),
            )

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["follow"]["new_count"], 1)
        self.assertEqual(result["follow"]["entries"][0]["webpage_url"], second_url)

    def test_download_rejects_stale_entry_selection(self) -> None:
        with patch.object(
            server,
            "inspect_follow_source",
            return_value=self.playlist([]),
        ):
            follow = server.create_source_follow(
                SourceFollowRequest(url="https://example.com/collection")
            )
        with self.assertRaises(HTTPException) as context:
            server.download_source_follow_entries(
                follow["id"],
                SourceFollowDownloadRequest(entry_urls=["https://example.com/stale"]),
                BackgroundTasks(),
            )
        self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
