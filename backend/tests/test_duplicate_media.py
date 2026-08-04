from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import server
from app.core import storage
from app.core.models import DuplicateCleanupRequest


class DuplicateMediaTests(unittest.TestCase):
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

    def insert_video(self, video_id: str, content: bytes) -> Path:
        directory = self.root / video_id
        directory.mkdir()
        path = directory / f"{video_id}.mp4"
        path.write_bytes(content)
        record = {
            "id": video_id,
            "engine": "local",
            "engine_version": "filesystem",
            "source_url": path.as_uri(),
            "source_platform": "本地文件",
            "source_type": "local_file",
            "status": "completed",
            "title": video_id,
            "file_path": str(path),
            "file_size": path.stat().st_size,
            "download_dir": str(directory),
            "library_visible": 1,
            "task_deleted": 1,
            "file_origin": "local",
            "metadata_json": json.dumps({"media_type": "video"}),
            "created_at": f"2026-08-01T01:00:0{len(video_id)}+00:00",
            "updated_at": "2026-08-01T01:00:00+00:00",
        }
        columns = list(record)
        with storage.connection() as database:
            database.execute(
                f"INSERT INTO downloads ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                list(record.values()),
            )
        return path

    def test_reports_only_exact_content_duplicates(self) -> None:
        self.insert_video("first", b"same!!")
        self.insert_video("second", b"same!!")
        self.insert_video("same-size-different", b"other?")
        self.insert_video("unique", b"unique-size")

        preview = server.duplicate_media_preview()

        self.assertEqual(preview["scanned_files"], 4)
        self.assertEqual(preview["total_groups"], 1)
        self.assertEqual(preview["total_files"], 2)
        self.assertEqual(preview["potential_reclaim_bytes"], 6)
        self.assertEqual(
            {item["id"] for item in preview["groups"][0]["items"]},
            {"first", "second"},
        )

    def test_cleanup_requires_confirmation_and_a_keeper(self) -> None:
        self.insert_video("first", b"same!!")
        self.insert_video("second", b"same!!")

        with self.assertRaises(HTTPException) as confirmation_error:
            server.cleanup_duplicate_media(
                DuplicateCleanupRequest(download_ids=["first"], confirm=False)
            )
        with self.assertRaises(HTTPException) as keeper_error:
            server.cleanup_duplicate_media(
                DuplicateCleanupRequest(download_ids=["first", "second"], confirm=True)
            )

        self.assertEqual(confirmation_error.exception.status_code, 422)
        self.assertEqual(keeper_error.exception.status_code, 422)

    def test_cleanup_trashes_selected_copy_and_keeps_other_record(self) -> None:
        first_path = self.insert_video("first", b"same!!")
        self.insert_video("second", b"same!!")

        with patch.object(server, "move_to_trash") as move_to_trash:
            result = server.cleanup_duplicate_media(
                DuplicateCleanupRequest(download_ids=["first"], confirm=True)
            )

        move_to_trash.assert_called_once_with(first_path.resolve())
        self.assertEqual(result["trashed_download_ids"], ["first"])
        self.assertEqual(result["freed_bytes"], 6)
        with storage.connection() as database:
            ids = {row["id"] for row in database.execute("SELECT id FROM downloads")}
        self.assertEqual(ids, {"second"})

    def test_cleanup_rejects_selection_when_content_changed(self) -> None:
        first_path = self.insert_video("first", b"same!!")
        self.insert_video("second", b"same!!")
        self.assertEqual(server.duplicate_media_preview()["total_groups"], 1)
        first_path.write_bytes(b"differ")

        with self.assertRaises(HTTPException) as raised:
            server.cleanup_duplicate_media(
                DuplicateCleanupRequest(download_ids=["first"], confirm=True)
            )

        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
