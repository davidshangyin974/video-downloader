from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from app import server
from app.core import storage


class DatabaseConnectionTests(unittest.TestCase):
    def test_existing_download_table_gets_parent_output_columns_and_unique_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "video-downloader.sqlite3"
            database = sqlite3.connect(database_path)
            database.execute(
                """
                CREATE TABLE downloads (
                    id TEXT PRIMARY KEY,
                    engine TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_type TEXT,
                    status TEXT NOT NULL,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    speed REAL,
                    eta INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            database.commit()
            database.close()

            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                with storage.connection() as migrated:
                    columns = {row["name"] for row in migrated.execute("PRAGMA table_info(downloads)")}
                    indexes = {row["name"] for row in migrated.execute("PRAGMA index_list(downloads)")}

            self.assertIn("parent_download_id", columns)
            self.assertIn("parent_output_index", columns)
            self.assertIn("restart_pending", columns)
            self.assertIn("priority", columns)
            self.assertIn("downloads_parent_output_idx", indexes)

    def test_server_connection_recreates_missing_database_with_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "removed-data" / "video-downloader.sqlite3"
            with patch.object(storage, "DATABASE_PATH", database_path):
                with server.connection() as database:
                    row = database.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'downloads'"
                    ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(database_path.is_file())
            with self.assertRaises(sqlite3.ProgrammingError):
                database.execute("SELECT 1")

    def test_storage_connection_recreates_missing_database_with_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "removed-data" / "video-downloader.sqlite3"
            with patch.object(storage, "DATABASE_PATH", database_path):
                with storage.connection() as database:
                    row = database.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'downloads'"
                    ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(database_path.is_file())
            with self.assertRaises(sqlite3.ProgrammingError):
                database.execute("SELECT 1")

    def test_startup_marks_active_tasks_interrupted_and_clears_transfer_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "video-downloader.sqlite3"
            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                with storage.connection() as database:
                    timestamp = storage.now()
                    database.execute(
                        """
                        INSERT INTO downloads (
                            id, engine, engine_version, source_url, status, speed, eta, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("running-task", "yt-dlp", "test", "https://example.com/video", "running", 1024, 30, timestamp, timestamp),
                    )
                    database.execute(
                        """
                        INSERT INTO downloads (
                            id, engine, engine_version, source_url, status, restart_pending, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("paused-task", "yt-dlp", "test", "https://example.com/paused", "paused", 0, timestamp, timestamp),
                    )
                storage.initialize_database()
                with storage.connection() as database:
                    row = database.execute(
                        "SELECT status, speed, eta, restart_pending FROM downloads WHERE id = ?", ("running-task",)
                    ).fetchone()
                    paused = database.execute(
                        "SELECT status, restart_pending FROM downloads WHERE id = ?", ("paused-task",)
                    ).fetchone()
            self.assertEqual(row["status"], "interrupted")
            self.assertIsNone(row["speed"])
            self.assertIsNone(row["eta"])
            self.assertEqual(row["restart_pending"], 1)
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["restart_pending"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
