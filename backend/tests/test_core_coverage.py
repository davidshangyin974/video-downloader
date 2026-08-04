from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import storage
from app.core.events import EventBus


class CoreCoverageTests(unittest.TestCase):
    def test_event_bus_publishes_to_current_subscribers_only(self) -> None:
        events = EventBus()
        first = events.subscribe()
        second = events.subscribe()
        payload = {"type": "download_progress", "progress": 50}
        events.publish(payload)
        self.assertEqual(first.get_nowait(), payload)
        self.assertEqual(second.get_nowait(), payload)

        events.unsubscribe(first)
        events.unsubscribe(first)
        events.publish({"type": "download_completed"})
        self.assertTrue(first.empty())
        self.assertEqual(second.get_nowait()["type"], "download_completed")

    def test_initialize_database_migrates_legacy_denoise_filter_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.sqlite3"
            database = sqlite3.connect(database_path)
            database.execute(
                """
                CREATE TABLE video_denoise_jobs (
                    id TEXT PRIMARY KEY,
                    download_id TEXT NOT NULL,
                    preset TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    file_path TEXT,
                    file_size INTEGER,
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
                    columns = {row["name"] for row in migrated.execute("PRAGMA table_info(video_denoise_jobs)")}
            self.assertIn("filter_config", columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
