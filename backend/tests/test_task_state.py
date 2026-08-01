from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage


class FakeDownloader:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    def __enter__(self) -> FakeDownloader:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_info(self, _url: str, download: bool) -> dict[str, object]:
        if not download:
            raise AssertionError("test downloader must execute a download")
        return {"id": "video-id", "title": "Test video", "filepath": str(self.output_path)}

    def prepare_filename(self, _info: dict[str, object]) -> str:
        return str(self.output_path)


class TwoPartFakeDownloader(FakeDownloader):
    def __init__(self, output_path: Path, options: dict[str, object]) -> None:
        super().__init__(output_path)
        self.options = options

    def extract_info(self, _url: str, download: bool) -> dict[str, object]:
        if not download:
            raise AssertionError("test downloader must execute a download")
        progress_hook = self.options["progress_hooks"][-1]
        video_info = {
            "id": "video-id",
            "title": "Test video",
            "format_id": "video",
            "vcodec": "h264",
            "acodec": "none",
        }
        audio_info = {
            "id": "video-id",
            "title": "Test video",
            "format_id": "audio",
            "vcodec": "none",
            "acodec": "aac",
        }
        progress_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "filename": "video.part", "info_dict": video_info})
        progress_hook({"status": "finished", "downloaded_bytes": 100, "total_bytes": 100, "filename": "video.part", "info_dict": video_info})
        progress_hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 20, "filename": "audio.part", "info_dict": audio_info})
        progress_hook({"status": "finished", "downloaded_bytes": 20, "total_bytes": 20, "filename": "audio.part", "info_dict": audio_info})
        postprocessor_hook = self.options["postprocessor_hooks"][-1]
        postprocessor_hook({"status": "started", "info_dict": {"filepath": str(self.output_path)}})
        postprocessor_hook({"status": "finished", "info_dict": {"filepath": str(self.output_path)}})
        return {"id": "video-id", "title": "Test video", "filepath": str(self.output_path)}


class TaskStateTests(unittest.TestCase):
    def test_download_scheduler_runs_higher_priority_waiting_task_first(self) -> None:
        release = threading.Event()
        first_started = threading.Event()
        order: list[str] = []

        def task(label: str) -> None:
            order.append(label)
            if label == "first":
                first_started.set()
                release.wait(timeout=2)

        with (
            patch("app.server.configured_download_concurrency", return_value=1),
            patch("app.server.scheduled_download_has_capacity", return_value=True),
        ):
            first = server.submit_download_task(task, "first")
            self.assertTrue(first_started.wait(timeout=1))
            low = server.submit_download_task(task, "low", priority=-1)
            high = server.submit_download_task(task, "high", priority=1)
            release.set()
            first.result(timeout=2)
            high.result(timeout=2)
            low.result(timeout=2)

        self.assertEqual(order, ["first", "high", "low"])

    def test_disk_preflight_requires_expected_size_plus_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.server.shutil.disk_usage",
            return_value=Mock(free=2 * 1024 * 1024),
        ):
            with self.assertRaisesRegex(HTTPException, "剩余空间不足"):
                server.ensure_disk_capacity(
                    temp_dir,
                    expected_size=2 * 1024 * 1024,
                    minimum_free_space_mb=1,
                )

    def test_download_scheduler_honors_configured_concurrency(self) -> None:
        release = threading.Event()
        two_started = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def blocking_task() -> None:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    two_started.set()
            release.wait(timeout=2)
            with lock:
                active -= 1

        with (
            patch("app.server.configured_download_concurrency", return_value=2),
            patch("app.server.scheduled_download_has_capacity", return_value=True),
        ):
            futures = [server.submit_download_task(blocking_task) for _ in range(3)]
            self.assertTrue(two_started.wait(timeout=1))
            time.sleep(0.05)
            self.assertEqual(peak, 2)
            release.set()
            for future in futures:
                future.result(timeout=2)

        self.assertEqual(peak, 2)

    def test_download_executor_runs_at_most_five_tasks_at_once(self) -> None:
        release = threading.Event()
        five_started = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def blocking_task() -> None:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == server.MAX_CONCURRENT_DOWNLOADS:
                    five_started.set()
            release.wait(timeout=2)
            with lock:
                active -= 1

        with (
            patch(
                "app.server.configured_download_concurrency",
                return_value=server.MAX_CONCURRENT_DOWNLOADS,
            ),
            patch("app.server.scheduled_download_has_capacity", return_value=True),
        ):
            futures = [
                server.submit_download_task(blocking_task)
                for _ in range(server.MAX_CONCURRENT_DOWNLOADS + 1)
            ]
            self.assertTrue(five_started.wait(timeout=1))
            time.sleep(0.05)
            self.assertEqual(peak, 5)
            release.set()
            for future in futures:
                future.result(timeout=2)
        self.assertEqual(peak, 5)

    def tearDown(self) -> None:
        server.cancelled_downloads.clear()

    def test_terminal_control_codes_are_removed_from_messages(self) -> None:
        self.assertEqual(
            server.normalize_message("\x1b[0;31mERROR:\x1b[0m  download failed\r\n"),
            "ERROR: download failed",
        )

    def test_user_cancel_uses_cancelled_status(self) -> None:
        updates: list[dict[str, object]] = []
        with (
            patch("app.server.update_download", side_effect=lambda _id, **values: updates.append(values)),
            patch("app.server.append_log"),
            patch("app.server.publish_download"),
        ):
            server.cancel_download("cancel-test", "已由用户取消下载。")
        self.assertEqual(updates[-1]["status"], "cancelled")
        self.assertIsNone(updates[-1]["speed"])
        self.assertIsNone(updates[-1]["eta"])

    def test_completed_ytdlp_task_uses_final_file_size_and_clears_transfer_state(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "video.mp4"
            output_path.write_bytes(b"final-video")
            downloader = FakeDownloader(output_path)
            with (
                patch("app.server.build_ytdlp_download_options", return_value={}),
                patch("app.server.yt_dlp.YoutubeDL", return_value=downloader),
                patch("app.server.update_download", side_effect=lambda _id, **values: updates.append(values)),
                patch("app.server.append_log"),
                patch("app.server.publish_download"),
            ):
                server.run_download(
                    "complete-test",
                    "https://example.com/video",
                    None,
                    temp_dir,
                    True,
                    True,
                    "",
                    "",
                )
        completed = updates[-1]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["file_size"], len(b"final-video"))
        self.assertEqual(completed["downloaded_bytes"], len(b"final-video"))
        self.assertEqual(completed["total_bytes"], len(b"final-video"))
        self.assertIsNone(completed["speed"])
        self.assertIsNone(completed["eta"])

    def test_multi_part_ytdlp_progress_is_one_monotonic_total(self) -> None:
        updates: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "video.mp4"
            output_path.write_bytes(b"final-video")

            def downloader_factory(options: dict[str, object]) -> TwoPartFakeDownloader:
                return TwoPartFakeDownloader(output_path, options)

            with (
                patch("app.server.yt_dlp.YoutubeDL", side_effect=downloader_factory),
                patch("app.server.update_download", side_effect=lambda _id, **values: updates.append(values)),
                patch("app.server.append_log"),
                patch("app.server.publish_download"),
            ):
                server.run_download(
                    "multi-part-test",
                    "https://example.com/video",
                    None,
                    temp_dir,
                    True,
                    True,
                    "",
                    "",
                )

        progress_values = [float(update["progress"]) for update in updates if "progress" in update]
        self.assertEqual(progress_values, sorted(progress_values))
        self.assertEqual(progress_values[-1], 100)
        self.assertNotIn(100, progress_values[:-1])
        self.assertEqual(progress_values[:4], [25, 50, 75, 99])

    def test_retry_clears_previous_cancellation_before_scheduling(self) -> None:
        record = {
            "id": "retry-test",
            "status": "cancelled",
            "source_url": "https://example.com/video",
            "resolved_url": "https://example.com/video",
            "engine": "yt-dlp",
            "requested_format": None,
            "download_dir": "/tmp",
            "write_thumbnail": 0,
            "write_info_json": 0,
            "yt_dlp_config": "",
            "ffmpeg_config": "",
        }
        server.cancelled_downloads.add("retry-test")
        background_tasks = BackgroundTasks()
        with (
            patch("app.server.download_record", return_value=record),
            patch("app.server.active_download_ids", return_value=[]),
            patch("app.server.update_download"),
            patch("app.server.append_log"),
        ):
            server.retry_download("retry-test", background_tasks)
        self.assertNotIn("retry-test", server.cancelled_downloads)
        self.assertEqual(len(background_tasks.tasks), 1)

    def test_pause_persists_state_without_marking_task_cancelled(self) -> None:
        record = {"id": "pause-test", "status": "running"}
        updates: list[dict[str, object]] = []
        process = Mock()
        process.poll.return_value = None
        server.engine_processes["pause-test"] = process
        self.addCleanup(server.engine_processes.pop, "pause-test", None)
        with (
            patch("app.server.download_record", side_effect=[record, {**record, "status": "paused"}]),
            patch("app.server.update_download", side_effect=lambda _id, **values: updates.append(values)),
            patch("app.server.append_log"),
            patch("app.server.publish_download"),
        ):
            paused = server.pause_active_download("pause-test")

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(updates[-1]["status"], "paused")
        self.assertEqual(updates[-1]["restart_pending"], 0)
        self.assertNotIn("pause-test", server.cancelled_downloads)
        process.terminate.assert_called_once_with()

    def test_paused_ytdlp_task_does_not_start_downloader(self) -> None:
        with (
            patch("app.server.download_status", return_value="paused"),
            patch("app.server.yt_dlp.YoutubeDL") as downloader,
        ):
            server.run_download(
                "paused-before-start",
                "https://example.com/video",
                None,
                "/tmp",
                False,
                False,
                "",
                "",
            )
        downloader.assert_not_called()

    def test_restart_recovery_only_schedules_pending_active_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "recovery.sqlite3"
            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                timestamp = storage.now()
                with storage.connection() as database:
                    for download_id, status, restart_pending in [
                        ("recover-me", "interrupted", 1),
                        ("stay-paused", "paused", 0),
                        ("stay-cancelled", "cancelled", 0),
                    ]:
                        database.execute(
                            """
                            INSERT INTO downloads (
                                id, engine, engine_version, source_url, status, restart_pending,
                                download_dir, write_thumbnail, write_info_json, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                download_id,
                                "yt-dlp",
                                "test",
                                f"https://example.com/{download_id}",
                                status,
                                restart_pending,
                                temp_dir,
                                0,
                                0,
                                timestamp,
                                timestamp,
                            ),
                        )
                scheduled: list[tuple[object, ...]] = []
                with patch("app.server.submit_download_task", side_effect=lambda task, *args, **_kwargs: scheduled.append((task, *args))):
                    server.recover_restart_pending_downloads()

                with storage.connection() as database:
                    statuses = {
                        row["id"]: (row["status"], row["restart_pending"])
                        for row in database.execute(
                            "SELECT id, status, restart_pending FROM downloads ORDER BY id"
                        ).fetchall()
                    }

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][1], "recover-me")
        self.assertEqual(statuses["recover-me"], ("queued", 0))
        self.assertEqual(statuses["stay-paused"], ("paused", 0))
        self.assertEqual(statuses["stay-cancelled"], ("cancelled", 0))

    def test_shutdown_marks_only_active_tasks_for_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "shutdown.sqlite3"
            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                timestamp = storage.now()
                with storage.connection() as database:
                    for download_id, status in [
                        ("running-on-shutdown", "running"),
                        ("queued-on-shutdown", "queued"),
                        ("paused-on-shutdown", "paused"),
                    ]:
                        database.execute(
                            """
                            INSERT INTO downloads (
                                id, engine, engine_version, source_url, status, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (download_id, "yt-dlp", "test", f"https://example.com/{download_id}", status, timestamp, timestamp),
                        )
                with patch.dict(server.engine_processes, {}, clear=True):
                    server.shutdown()
                with storage.connection() as database:
                    states = {
                        row["id"]: (row["status"], row["restart_pending"])
                        for row in database.execute(
                            "SELECT id, status, restart_pending FROM downloads ORDER BY id"
                        ).fetchall()
                    }

        self.assertEqual(states["running-on-shutdown"], ("interrupted", 1))
        self.assertEqual(states["queued-on-shutdown"], ("interrupted", 1))
        self.assertEqual(states["paused-on-shutdown"], ("paused", 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
