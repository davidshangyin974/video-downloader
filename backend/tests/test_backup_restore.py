from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import server
from app.core import storage


class BackupRestoreTests(unittest.TestCase):
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

    def insert_download(self, download_id: str, status: str = "completed") -> None:
        timestamp = "2026-08-01T00:00:00+00:00"
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, status, title,
                    file_path, library_visible, created_at, updated_at
                ) VALUES (?, 'local', 'filesystem', ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    download_id,
                    f"file:///{download_id}.mp4",
                    status,
                    download_id,
                    str(self.root / f"{download_id}.mp4"),
                    timestamp,
                    timestamp,
                ),
            )

    def test_export_contains_version_settings_and_no_media_files(self) -> None:
        self.insert_download("backup-video")

        payload = server.export_backup_payload()

        self.assertEqual(payload["format"], server.BACKUP_FORMAT)
        self.assertEqual(payload["version"], server.BACKUP_FORMAT_VERSION)
        self.assertFalse(payload["includes_media_files"])
        self.assertEqual(payload["tables"]["downloads"][0]["id"], "backup-video")
        setting_keys = {row["key"] for row in payload["tables"]["app_settings"]}
        self.assertIn("download_dir", setting_keys)

    def test_preview_reports_counts_and_missing_files(self) -> None:
        self.insert_download("missing-video")
        payload = server.export_backup_payload()

        normalized, summary = server.validate_backup_payload(payload)

        self.assertEqual(summary["counts"]["downloads"], 1)
        self.assertEqual(summary["missing_media_files"], 1)
        self.assertFalse(summary["includes_media_files"])
        self.assertEqual(normalized["tables"]["downloads"][0]["id"], "missing-video")

    def test_restore_replaces_data_and_preserves_default_settings(self) -> None:
        self.insert_download("from-backup")
        payload = server.export_backup_payload()
        with storage.connection() as database:
            database.execute("DELETE FROM downloads")
            database.execute("DELETE FROM app_settings")
        self.insert_download("current-data")

        result = server.restore_backup_payload(payload)

        self.assertEqual(result["counts"]["downloads"], 1)
        with storage.connection() as database:
            ids = [row["id"] for row in database.execute("SELECT id FROM downloads")]
            setting_count = database.execute("SELECT COUNT(*) AS count FROM app_settings").fetchone()["count"]
        self.assertEqual(ids, ["from-backup"])
        self.assertGreater(setting_count, 0)

    def test_failed_restore_rolls_back_without_overwriting_current_data(self) -> None:
        self.insert_download("backup-row")
        payload = server.export_backup_payload()
        broken_payload = copy.deepcopy(payload)
        broken_payload["tables"]["downloads"].append(
            copy.deepcopy(broken_payload["tables"]["downloads"][0])
        )
        with storage.connection() as database:
            database.execute("DELETE FROM downloads")
        self.insert_download("current-row")

        with self.assertRaises(Exception):
            server.restore_backup_payload(broken_payload)

        with storage.connection() as database:
            ids = [row["id"] for row in database.execute("SELECT id FROM downloads")]
        self.assertEqual(ids, ["current-row"])

    def test_restore_rejects_active_downloads_and_unknown_fields(self) -> None:
        self.insert_download("active-row", status="running")
        payload = server.export_backup_payload()
        with self.assertRaisesRegex(HTTPException, "进行中的下载任务"):
            server.restore_backup_payload(payload)

        invalid = copy.deepcopy(payload)
        invalid["tables"]["downloads"][0]["unknown"] = json.dumps(True)
        with self.assertRaisesRegex(HTTPException, "未知字段"):
            server.validate_backup_payload(invalid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
