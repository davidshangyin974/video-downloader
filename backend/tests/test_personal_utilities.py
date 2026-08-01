from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import server
from app.core import storage
from app.core.models import (
    IncompleteCleanupRequest,
    MaintenanceCleanupRequest,
    MaintenanceSettings,
    VideoRenameRequest,
)


class PersonalUtilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()

    def insert_download(
        self,
        download_id: str,
        status: str,
        updated_at: str,
        *,
        file_path: Path | None = None,
        download_dir: Path | None = None,
        title: str = "测试视频",
        task_deleted: int = 0,
    ) -> None:
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_platform, source_type,
                    status, title, file_path, file_size, progress, downloaded_bytes,
                    total_bytes, metadata_json, download_dir, library_visible,
                    task_deleted, file_origin, created_at, updated_at
                ) VALUES (?, 'local', 'filesystem', ?, '本地文件', 'local_file', ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, 'local', ?, ?)
                """,
                (
                    download_id,
                    file_path.as_uri() if file_path else f"https://example.com/{download_id}",
                    status,
                    title,
                    str(file_path) if file_path else None,
                    file_path.stat().st_size if file_path and file_path.exists() else None,
                    100 if status == "completed" else 0,
                    file_path.stat().st_size if file_path and file_path.exists() else 0,
                    file_path.stat().st_size if file_path and file_path.exists() else None,
                    str(download_dir or self.root),
                    1 if status == "completed" else 0,
                    task_deleted,
                    updated_at,
                    updated_at,
                ),
            )

    def test_maintenance_settings_are_saved_and_returned(self) -> None:
        saved = server.save_maintenance_settings(
            MaintenanceSettings(
                auto_cleanup_enabled=True,
                retention_days=45,
                clean_download_logs=True,
                clean_download_tasks=False,
            )
        )
        self.assertEqual(saved["maintenance"]["retention_days"], 45)
        self.assertTrue(saved["maintenance"]["auto_cleanup_enabled"])
        self.assertFalse(saved["maintenance"]["clean_download_tasks"])

    def test_history_cleanup_excludes_completed_paused_and_recent_tasks(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        recent = datetime.now(UTC).isoformat()
        self.insert_download("old-failed", "failed", old)
        self.insert_download("old-paused", "paused", old)
        self.insert_download("old-completed", "completed", old)
        self.insert_download("recent-failed", "failed", recent)
        with storage.connection() as database:
            for download_id, created_at in (
                ("old-failed", old),
                ("old-paused", old),
                ("old-completed", old),
                ("recent-failed", recent),
            ):
                database.execute(
                    "INSERT INTO download_logs (download_id, level, message, created_at) VALUES (?, 'info', 'log', ?)",
                    (download_id, created_at),
                )

        preview = server.maintenance_cleanup_preview(30)
        self.assertEqual(preview["task_count"], 1)
        self.assertEqual({task["id"] for task in preview["tasks"]}, {"old-failed"})
        self.assertEqual(preview["log_count"], 2)

        result = server.run_maintenance_cleanup(
            MaintenanceCleanupRequest(retention_days=30, clean_download_logs=True, clean_download_tasks=True)
        )
        self.assertEqual(result["cleaned_tasks"], 1)
        with storage.connection() as database:
            states = {
                row["id"]: row["task_deleted"]
                for row in database.execute(
                    "SELECT id, task_deleted FROM downloads WHERE id LIKE '%failed' OR id = 'old-paused' OR id = 'old-completed'"
                ).fetchall()
            }
            remaining_logs = {
                row["download_id"]
                for row in database.execute("SELECT download_id FROM download_logs").fetchall()
            }
        self.assertEqual(states["old-failed"], 1)
        self.assertEqual(states["old-paused"], 0)
        self.assertEqual(states["old-completed"], 0)
        self.assertIn("old-paused", remaining_logs)
        self.assertIn("recent-failed", remaining_logs)
        self.assertNotIn("old-completed", remaining_logs)

    def test_incomplete_residue_preview_and_cleanup_use_fresh_allowlist(self) -> None:
        task_dir = self.root / "downloads" / "task"
        task_dir.mkdir(parents=True)
        part_file = task_dir / "clip.mp4.part"
        part_file.write_bytes(b"partial")
        old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        self.insert_download("failed-task", "failed", old, download_dir=task_dir)

        preview = server.incomplete_residue_preview()
        paths = {item["path"] for item in preview["items"]}
        self.assertIn(str(part_file.resolve()), paths)
        with patch("app.server.move_to_trash", return_value=self.root / "Trash" / part_file.name) as move:
            result = server.cleanup_incomplete_residues(IncompleteCleanupRequest(paths=[str(part_file.resolve())]))
        move.assert_called_once_with(part_file.resolve())
        self.assertEqual(result["freed_bytes"], len(b"partial"))

    def test_video_title_can_rename_media_and_related_sidecars(self) -> None:
        media_dir = self.root / "media"
        media_dir.mkdir()
        media_path = media_dir / "old.mp4"
        subtitle_path = media_dir / "old.zh.vtt"
        media_path.write_bytes(b"video")
        subtitle_path.write_text("WEBVTT", encoding="utf-8")
        timestamp = datetime.now(UTC).isoformat()
        self.insert_download(
            "rename-video",
            "completed",
            timestamp,
            file_path=media_path,
            download_dir=media_dir,
            title="旧标题",
        )

        renamed = server.rename_video(
            "rename-video",
            VideoRenameRequest(title="新标题", rename_file=True),
        )
        self.assertEqual(renamed["title"], "新标题")
        self.assertEqual(renamed["file_path"], str((media_dir / "新标题.mp4").resolve()))
        self.assertTrue((media_dir / "新标题.mp4").is_file())
        self.assertTrue((media_dir / "新标题.zh.vtt").is_file())
        self.assertFalse(media_path.exists())

    def test_ytdlp_update_check_reports_newer_version_without_installing(self) -> None:
        response = Mock()
        response.read.return_value = json.dumps({"info": {"version": "9999.1.1"}}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch("app.server.build_opener", return_value=opener):
            result = server.check_ytdlp_update(force=True)
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "9999.1.1")

    def test_system_notification_respects_setting_and_uses_argv(self) -> None:
        completed = Mock(returncode=0)
        with (
            patch("app.server.sys.platform", "darwin"),
            patch("app.server.get_download_settings", return_value={"system_notifications": True}),
            patch("app.server.subprocess.run", return_value=completed) as run,
        ):
            self.assertTrue(server.send_system_notification("Video Downloader", "完成"))
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["Video Downloader", "完成"])


if __name__ == "__main__":
    unittest.main()
