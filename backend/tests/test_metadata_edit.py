from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import server
from app.core import storage
from app.core.models import VideoMetadataRequest


class MetadataEditTests(unittest.TestCase):
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
        self.insert_playlist("playlist-a", "A 合集")
        self.insert_playlist("playlist-b", "B 合集")
        self.insert_video("target", playlist_id="playlist-a", playlist_index=2)
        self.insert_video("a-keeper", playlist_id="playlist-a", playlist_index=1)
        self.insert_video("b-keeper", playlist_id="playlist-b", playlist_index=4)

    def insert_playlist(self, playlist_id: str, title: str) -> None:
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO playlists (
                    id, source_url, source_platform, title, total_count,
                    source_total_count, created_at, updated_at
                ) VALUES (?, ?, '本地整理', ?, 0, 0, ?, ?)
                """,
                (playlist_id, f"local://{playlist_id}", title, server.now(), server.now()),
            )

    def insert_video(
        self, video_id: str, playlist_id: str | None, playlist_index: int | None
    ) -> None:
        directory = self.root / video_id
        directory.mkdir()
        path = directory / f"{video_id}.mp4"
        path.write_bytes(video_id.encode())
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, status, title,
                    file_path, file_size, download_dir, library_visible,
                    task_deleted, file_origin, playlist_id, playlist_index,
                    created_at, updated_at
                ) VALUES (?, 'local', 'filesystem', ?, 'completed', ?, ?, ?, ?, 1, 1,
                          'local', ?, ?, ?, ?)
                """,
                (
                    video_id,
                    path.as_uri(),
                    video_id,
                    str(path),
                    path.stat().st_size,
                    str(directory),
                    playlist_id,
                    playlist_index,
                    server.now(),
                    server.now(),
                ),
            )

    def test_updates_fields_and_moves_to_end_of_existing_collection(self) -> None:
        updated = server.update_video_metadata(
            "target",
            VideoMetadataRequest(
                uploader="  新作者  ",
                upload_date="2026-08-01",
                thumbnail="https://example.com/cover.jpg",
                playlist_id="playlist-b",
            ),
        )

        self.assertEqual(updated["uploader"], "新作者")
        self.assertEqual(updated["upload_date"], "20260801")
        self.assertEqual(updated["thumbnail"], "https://example.com/cover.jpg")
        self.assertEqual(updated["playlist_id"], "playlist-b")
        self.assertEqual(updated["playlist_index"], 5)
        with storage.connection() as database:
            counts = {
                row["id"]: row["total_count"]
                for row in database.execute("SELECT id, total_count FROM playlists")
            }
        self.assertEqual(counts, {"playlist-a": 1, "playlist-b": 2})

    def test_can_clear_fields_and_collection_membership(self) -> None:
        updated = server.update_video_metadata(
            "target",
            VideoMetadataRequest(
                uploader=None,
                upload_date=None,
                thumbnail=None,
                playlist_id=None,
            ),
        )

        self.assertIsNone(updated["uploader"])
        self.assertIsNone(updated["upload_date"])
        self.assertIsNone(updated["thumbnail"])
        self.assertIsNone(updated["playlist_id"])
        self.assertIsNone(updated["playlist_index"])

    def test_whitespace_fields_clear_without_reordering_same_collection(self) -> None:
        updated = server.update_video_metadata(
            "target",
            VideoMetadataRequest(
                uploader="   ",
                upload_date=None,
                thumbnail="   ",
                playlist_id=" playlist-a ",
            ),
        )

        self.assertIsNone(updated["uploader"])
        self.assertIsNone(updated["thumbnail"])
        self.assertEqual(updated["playlist_id"], "playlist-a")
        self.assertEqual(updated["playlist_index"], 2)

    def test_rejects_invalid_cover_date_and_collection(self) -> None:
        requests = [
            VideoMetadataRequest(thumbnail="file:///tmp/cover.jpg"),
            VideoMetadataRequest(upload_date="2026-02-30"),
            VideoMetadataRequest(playlist_id="missing"),
        ]

        for request in requests:
            with self.subTest(request=request), self.assertRaises(HTTPException) as raised:
                server.update_video_metadata("target", request)
            self.assertEqual(raised.exception.status_code, 422)

    def test_lists_all_collections_for_editor(self) -> None:
        self.insert_playlist("empty", "空合集")

        options = server.list_playlists()

        self.assertEqual([option["title"] for option in options], ["A 合集", "B 合集", "空合集"])
        self.assertEqual({option["id"]: option["item_count"] for option in options}, {
            "playlist-a": 2,
            "playlist-b": 1,
            "empty": 0,
        })


if __name__ == "__main__":
    unittest.main()
