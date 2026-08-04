from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage
from app.core.models import DownloadRequest, GeneralSettingsRequest


class DownloadDirectoryPatternTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()

    def test_pattern_builds_safe_nested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = server.formatted_download_dir(
                temporary_directory,
                "{platform}/{year}-{month}/{title}",
                platform="YouTube",
                uploader="测试作者",
                title="教程/第一集",
                created_at=datetime(2026, 7, 31),
            )

            expected = Path(temporary_directory).resolve() / "YouTube" / "2026-07" / "教程_第一集"
            self.assertEqual(Path(result), expected)
            self.assertTrue(expected.is_dir())

    def test_empty_pattern_keeps_default_download_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = server.formatted_download_dir(
                temporary_directory,
                "",
                platform="YouTube",
                uploader=None,
                title="测试视频",
            )

            self.assertEqual(Path(result), Path(temporary_directory).resolve())

    def test_unknown_variable_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            server.validate_directory_pattern("{platform}/{unknown}")

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("{unknown}", raised.exception.detail)

    def test_parent_directory_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            server.validate_directory_pattern("{platform}/../{title}")

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("..", raised.exception.detail)

    def test_general_setting_is_saved_and_used_by_new_ytdlp_task(self) -> None:
        base_directory = self.root / "downloads"
        saved = server.save_general_settings(
            GeneralSettingsRequest(
                download_dir=str(base_directory),
                directory_pattern="{platform}/{year}-{month}/{title}",
                write_thumbnail=True,
                write_info_json=True,
                max_concurrent_downloads=2,
                download_rate_limit_kbps=512,
                minimum_free_space_mb=0,
            )
        )
        self.assertEqual(saved["directory_pattern"], "{platform}/{year}-{month}/{title}")

        inspect_id = "directory-pattern-task"
        source_url = "https://www.youtube.com/watch?v=test"
        server.inspect_jobs[inspect_id] = {
            "status": "completed",
            "source": {
                "url": source_url,
                "route": {
                    "engine": "yt-dlp",
                    "source_type": "webpage",
                    "resolved_url": source_url,
                },
            },
            "media": {
                "kind": "video",
                "title": "测试/视频",
                "uploader": "测试作者",
            },
        }
        self.addCleanup(server.inspect_jobs.pop, inspect_id, None)

        background_tasks = BackgroundTasks()
        record = server.create_download(DownloadRequest(inspect_id=inspect_id, priority=1), background_tasks)

        relative_directory = Path(record["download_dir"]).relative_to(base_directory.resolve())
        self.assertEqual(relative_directory.parts[0], "YouTube")
        self.assertEqual(relative_directory.parts[1], datetime.now().strftime("%Y-%m"))
        self.assertEqual(relative_directory.parts[2], "测试_视频")
        self.assertEqual(background_tasks.tasks[0].args[4], record["download_dir"])
        self.assertEqual(record["priority"], 1)
        self.assertEqual(background_tasks.tasks[0].kwargs["priority"], 1)
        self.assertIn("--limit-rate 512K", record["yt_dlp_config"])

    def test_completed_video_is_not_created_again(self) -> None:
        source_url = "https://www.youtube.com/watch?v=test&tracking=second"
        canonical_url = "https://www.youtube.com/watch?v=test"
        media_file = self.root / "downloads" / "test.mp4"
        media_file.parent.mkdir(parents=True)
        media_file.write_bytes(b"video")
        timestamp = storage.now()
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_platform, status,
                    title, webpage_url, video_id, file_path, library_visible, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "completed-video", "yt-dlp", "test", canonical_url, "YouTube", "completed",
                    "测试视频", canonical_url, "test", str(media_file), 1, timestamp, timestamp,
                ),
            )

        inspect_id = "duplicate-completed-video"
        server.inspect_jobs[inspect_id] = {
            "status": "completed",
            "source": {
                "url": source_url,
                "route": {
                    "engine": "yt-dlp",
                    "source_type": "webpage",
                    "resolved_url": source_url,
                },
            },
            "media": {
                "kind": "video",
                "title": "测试视频",
                "webpage_url": canonical_url,
                "video_id": "test",
            },
        }
        self.addCleanup(server.inspect_jobs.pop, inspect_id, None)

        with self.assertRaises(HTTPException) as raised:
            server.create_download(
                DownloadRequest(inspect_id=inspect_id, replace_existing=True),
                BackgroundTasks(),
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "这个视频已下载，可在视频管理中查看。")
        with storage.connection() as database:
            count = database.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
