from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import server
from app.core import storage
from app.core.models import DownloadProgressRequest, LocalVideoRequest


class FileManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()
        self.media_probe_patch = patch.object(
            server,
            "probe_local_media_file",
            return_value={
                "media_type": "video",
                "duration": 10.0,
                "resolution": "1280x720",
                "codec": "h264",
                "bit_rate": 1_000_000,
                "format_name": "mov,mp4",
            },
        )
        self.media_probe_patch.start()
        self.addCleanup(self.media_probe_patch.stop)

    def insert_download(self, download_id: str, **values: object) -> None:
        record = {
            "engine": "aria2",
            "engine_version": "test",
            "source_url": "https://example.com/file",
            "status": "failed",
            "title": "file.mp4",
            "download_dir": str(self.root / "downloads"),
            "source_type": "direct",
            "file_path": None,
            "output_files_json": None,
            "library_visible": 0,
            "created_at": storage.now(),
            "updated_at": storage.now(),
        }
        record.update(values)
        columns = ["id", *record.keys()]
        with storage.connection() as database:
            database.execute(
                f"INSERT INTO downloads ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [download_id, *record.values()],
            )

    def test_queued_download_position_orders_priority_before_creation_time(self) -> None:
        self.insert_download("low-priority", status="queued", priority=-1, created_at="2026-01-01T00:00:00+00:00")
        self.insert_download("high-priority", status="queued", priority=1, created_at="2026-01-02T00:00:00+00:00")
        self.assertEqual(server.download_record("high-priority")["queue_position"], 1)
        self.assertEqual(server.download_record("low-priority")["queue_position"], 2)

    def test_output_path_cannot_escape_recorded_download_directory(self) -> None:
        download_root = self.root / "downloads"
        download_root.mkdir()
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"outside")
        self.insert_download(
            "unsafe-output",
            output_files_json=json.dumps([
                {"relative_path": "../outside.mp4", "size": 7, "file_type": "video/mp4", "playable": True}
            ]),
        )

        with self.assertRaises(HTTPException) as raised:
            server.resolve_download_output("unsafe-output", 0)
        self.assertEqual(raised.exception.status_code, 409)

    def test_bt_output_cannot_reference_another_task_directory(self) -> None:
        download_root = self.root / "downloads"
        other_task_file = download_root / "BT" / "other-task" / "outside.mp4"
        other_task_file.parent.mkdir(parents=True)
        other_task_file.write_bytes(b"outside")
        self.insert_download(
            "bt-output",
            source_type="magnet",
            output_files_json=json.dumps([
                {
                    "relative_path": "BT/other-task/outside.mp4",
                    "size": 7,
                    "file_type": "video/mp4",
                    "playable": True,
                }
            ]),
        )

        with self.assertRaises(HTTPException) as raised:
            server.resolve_download_output("bt-output", 0)
        self.assertEqual(raised.exception.status_code, 409)

    def test_bt_incomplete_files_are_limited_to_the_task_directory(self) -> None:
        download_root = self.root / "downloads"
        task_directory = download_root / "BT" / "bt-task"
        other_directory = download_root / "BT" / "other-task"
        task_directory.mkdir(parents=True)
        other_directory.mkdir()
        (task_directory / "selected.mp4.aria2").write_bytes(b"partial")
        (other_directory / "other.mp4").write_bytes(b"other")
        record = {
            "id": "bt-task",
            "status": "cancelled",
            "download_dir": str(download_root),
            "source_type": "magnet",
            "engine": "aria2",
            "title": "bundle",
            "file_path": None,
        }

        self.assertEqual(server.incomplete_task_files(record), [task_directory.resolve()])

    def test_direct_filename_collision_gets_a_task_specific_name(self) -> None:
        download_root = self.root / "downloads"
        download_root.mkdir()
        (download_root / "clip.mp4").write_bytes(b"existing")

        name = server.direct_download_file_name(str(download_root), "clip.mp4", "12345678-abcd")

        self.assertEqual(name, "clip [12345678].mp4")

    def test_partial_task_cleanup_failure_keeps_task_and_logs(self) -> None:
        download_root = self.root / "downloads"
        download_root.mkdir()
        partial_file = download_root / "file.mp4"
        partial_file.write_bytes(b"partial")
        self.insert_download("cleanup-failure", file_path=str(partial_file), status="cancelled")
        with storage.connection() as database:
            database.execute(
                "INSERT INTO download_logs (download_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                ("cleanup-failure", "info", "partial", storage.now()),
            )

        with patch("app.server.move_to_trash", side_effect=RuntimeError("trash unavailable")):
            response = server.delete_download_task("cleanup-failure", True)

        self.assertEqual(response.status_code, 409)
        with storage.connection() as database:
            row = database.execute(
                "SELECT task_deleted FROM downloads WHERE id = ?", ("cleanup-failure",)
            ).fetchone()
            log_count = database.execute(
                "SELECT COUNT(*) FROM download_logs WHERE download_id = ?", ("cleanup-failure",)
            ).fetchone()[0]
        self.assertEqual(row["task_deleted"], 0)
        self.assertEqual(log_count, 1)

    def test_completed_task_deletion_never_moves_video_file(self) -> None:
        download_root = self.root / "downloads"
        download_root.mkdir()
        video = download_root / "complete.mp4"
        video.write_bytes(b"complete")
        self.insert_download(
            "complete-task",
            status="completed",
            title="complete.mp4",
            file_path=str(video),
            library_visible=1,
        )

        with patch("app.server.move_to_trash") as move_to_trash:
            result = server.delete_download_task("complete-task", True)

        move_to_trash.assert_not_called()
        self.assertEqual(result["trashed_files"], [])
        self.assertTrue(video.is_file())

    def test_video_delete_only_uses_exact_related_files(self) -> None:
        download_root = self.root / "downloads"
        download_root.mkdir()
        video = download_root / "movie.mp4"
        cover = download_root / "movie.webp"
        info = download_root / "movie.info.json"
        subtitle = download_root / "movie.zh.srt"
        unrelated = download_root / "movie-other.srt"
        for path in [video, cover, info, subtitle, unrelated]:
            path.write_bytes(path.name.encode())
        self.insert_download(
            "video-delete",
            status="completed",
            title="movie",
            file_path=str(video),
            library_visible=1,
        )

        preview = server.preview_video_delete("video-delete")
        self.assertEqual(
            {(item["kind"], item["path"]) for item in preview},
            {
                ("主视频", str(video.resolve())),
                ("本地封面", str(cover.resolve())),
                ("info JSON", str(info.resolve())),
                ("字幕", str(subtitle.resolve())),
            },
        )

        moved: list[Path] = []
        with patch("app.server.move_to_trash", side_effect=lambda path: moved.append(path) or path):
            result = server.delete_video("video-delete")

        self.assertEqual(set(moved), {video.resolve(), cover.resolve(), info.resolve(), subtitle.resolve()})
        self.assertNotIn(unrelated.resolve(), moved)
        self.assertEqual(result["failed_files"], [])
        with self.assertRaises(HTTPException):
            server.download_record("video-delete")

    def test_local_video_uses_source_path_and_is_hidden_from_download_tasks(self) -> None:
        video = self.root / "source videos" / "local movie.mp4"
        video.parent.mkdir()
        video.write_bytes(b"local")

        result = server.create_local_video(LocalVideoRequest(file_path=str(video)))

        self.assertEqual(result["file_path"], str(video.resolve()))
        self.assertEqual(result["file_origin"], "local")
        self.assertEqual(result["source_type"], "local_file")
        self.assertEqual(result["engine"], "local")
        self.assertTrue(result["file_exists"])
        with storage.connection() as database:
            row = database.execute(
                "SELECT task_deleted, file_origin FROM downloads WHERE id = ?",
                (result["id"],),
            ).fetchone()
        self.assertEqual(row["task_deleted"], 1)
        self.assertEqual(row["file_origin"], "local")
        self.assertNotIn(result["id"], {item["id"] for item in server.list_downloads(status=None, limit=50)})

    def test_multi_output_task_registers_each_media_file_once(self) -> None:
        download_root = self.root / "downloads"
        task_directory = download_root / "BT" / "parent-task"
        task_directory.mkdir(parents=True)
        first = task_directory / "first.mp4"
        second = task_directory / "second.mkv"
        readme = task_directory / "readme.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        readme.write_bytes(b"readme")
        self.insert_download(
            "parent-task",
            status="completed",
            title="bundle",
            source_type="magnet",
            output_files_json=json.dumps([
                {"relative_path": "BT/parent-task/first.mp4", "size": 5, "file_type": "video/mp4", "playable": True},
                {"relative_path": "BT/parent-task/second.mkv", "size": 6, "file_type": "video/x-matroska", "playable": True},
                {"relative_path": "BT/parent-task/readme.txt", "size": 6, "file_type": "text/plain", "playable": False},
            ]),
        )

        first_result = server.register_download_media_outputs("parent-task")
        second_result = server.register_download_media_outputs("parent-task")

        self.assertEqual([item["title"] for item in first_result], ["first", "second"])
        self.assertEqual([item["id"] for item in second_result], [
            "parent-task-output-0",
            "parent-task-output-1",
        ])
        with storage.connection() as database:
            children = database.execute(
                """
                SELECT id, parent_output_index, library_visible, task_deleted, file_path
                FROM downloads
                WHERE parent_download_id = ?
                ORDER BY parent_output_index
                """,
                ("parent-task",),
            ).fetchall()
        self.assertEqual(len(children), 2)
        self.assertEqual([row["parent_output_index"] for row in children], [0, 1])
        self.assertTrue(all(row["library_visible"] for row in children))
        self.assertTrue(all(row["task_deleted"] for row in children))
        self.assertEqual([row["file_path"] for row in children], [str(first.resolve()), str(second.resolve())])

        library = server.list_library_items(
            q=None,
            platform=[],
            file_format=[],
            resolution=None,
            media_type=None,
            page=1,
            page_size=10,
            sort_by="created_at",
            sort_order="desc",
            favorite_only=False,
            limit=None,
        )
        self.assertEqual(
            {item["id"] for item in library["items"] if item["kind"] == "video"},
            {"parent-task-output-0", "parent-task-output-1"},
        )

    def test_single_audio_output_gets_actual_media_metadata(self) -> None:
        download_root = self.root / "downloads"
        download_root.mkdir(parents=True)
        audio = download_root / "track.mp3"
        audio.write_bytes(b"audio")
        self.insert_download(
            "audio-task",
            status="completed",
            title="track.mp3",
            file_path=str(audio),
            library_visible=1,
            output_files_json=json.dumps([
                {"relative_path": "track.mp3", "size": 5, "file_type": "audio/mpeg", "playable": True},
            ]),
        )

        with patch.object(server, "probe_local_media_file", return_value={
            "media_type": "audio",
            "duration": 30.0,
            "resolution": None,
            "codec": "mp3",
            "bit_rate": 128_000,
            "format_name": "mp3",
        }):
            [registered] = server.register_download_media_outputs("audio-task")

        self.assertEqual(registered["metadata"]["media_type"], "audio")
        self.assertEqual(registered["metadata"]["bit_rate"], 128_000)
        self.assertEqual(registered["duration"], 30.0)
        self.assertIsNone(registered["resolution"])

    def test_output_child_keeps_independent_favorite_and_progress(self) -> None:
        download_root = self.root / "downloads"
        task_directory = download_root / "BT" / "parent-state"
        task_directory.mkdir(parents=True)
        video = task_directory / "episode.mp4"
        companion = task_directory / "bonus.mp4"
        video.write_bytes(b"episode")
        companion.write_bytes(b"bonus")
        self.insert_download(
            "parent-state",
            status="completed",
            title="bundle",
            source_type="magnet",
            output_files_json=json.dumps([
                {"relative_path": "BT/parent-state/episode.mp4", "size": 7, "file_type": "video/mp4", "playable": True},
                {"relative_path": "BT/parent-state/bonus.mp4", "size": 5, "file_type": "video/mp4", "playable": True},
            ]),
        )
        server.register_download_media_outputs("parent-state")

        favorite = server.update_video_favorite(
            "parent-state-output-0",
            server.VideoFavoriteRequest(favorite=True),
        )
        progress = server.update_video_progress(
            "parent-state-output-0",
            server.PlaybackProgressRequest(position=6, duration=10),
        )

        self.assertEqual(favorite["favorite"], 1)
        self.assertEqual(progress["watch_position"], 6)
        sibling = server.download_record("parent-state-output-1")
        self.assertEqual(sibling["favorite"], 0)
        self.assertEqual(sibling["watch_position"], 0)

    def test_deleting_completed_parent_task_keeps_indexed_media(self) -> None:
        download_root = self.root / "downloads"
        task_directory = download_root / "BT" / "parent-history"
        task_directory.mkdir(parents=True)
        first = task_directory / "first.mp4"
        second = task_directory / "second.mp4"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.insert_download(
            "parent-history",
            status="completed",
            title="bundle",
            source_type="magnet",
            output_files_json=json.dumps([
                {"relative_path": "BT/parent-history/first.mp4", "size": 5, "file_type": "video/mp4", "playable": True},
                {"relative_path": "BT/parent-history/second.mp4", "size": 6, "file_type": "video/mp4", "playable": True},
            ]),
        )
        server.register_download_media_outputs("parent-history")

        result = server.delete_download_task("parent-history", remove_files=True)

        self.assertEqual(result["trashed_files"], [])
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        with storage.connection() as database:
            parent = database.execute(
                "SELECT task_deleted FROM downloads WHERE id = ?",
                ("parent-history",),
            ).fetchone()
            children = database.execute(
                "SELECT library_visible FROM downloads WHERE parent_download_id = ?",
                ("parent-history",),
            ).fetchall()
        self.assertEqual(parent["task_deleted"], 1)
        self.assertEqual(len(children), 2)
        self.assertTrue(all(row["library_visible"] for row in children))

    def test_download_tasks_exclude_completed_items_from_results_and_counts(self) -> None:
        for index in range(12):
            self.insert_download(f"active-{index}", status="running")
        for index in range(3):
            self.insert_download(f"failed-{index}", status="failed")
        for index in range(4):
            self.insert_download(f"completed-{index}", status="completed")
        self.insert_download("paused-task", status="paused")

        first_page = server.list_downloads(
            status=None,
            limit=50,
            page=1,
            page_size=10,
            task_filter="all",
        )
        second_page = server.list_downloads(
            status=None,
            limit=50,
            page=2,
            page_size=10,
            task_filter="all",
        )

        self.assertEqual(len(first_page["items"]), 10)
        self.assertEqual(len(second_page["items"]), 6)
        self.assertEqual(first_page["total"], 16)
        self.assertEqual(first_page["total_pages"], 2)
        self.assertEqual(first_page["counts"], {"all": 16, "active": 12, "attention": 4})
        self.assertTrue(all(item["status"] != "completed" for item in first_page["items"]))
        self.assertTrue(all(item["status"] != "completed" for item in second_page["items"]))

        completed_legacy = server.list_downloads(
            status=None,
            limit=50,
        )
        self.assertTrue(all(item["status"] != "completed" for item in completed_legacy))

    def test_download_progress_is_returned_for_multiple_tasks_in_one_request(self) -> None:
        self.insert_download(
            "running-task",
            status="running",
            progress=35,
            downloaded_bytes=350,
            total_bytes=1000,
        )
        self.insert_download("failed-task", status="failed", progress=0, error="失败")

        response = server.get_download_progress_batch(
            DownloadProgressRequest(ids=["failed-task", "running-task", "missing-task"])
        )

        self.assertEqual(
            [item["id"] for item in response["items"]],
            ["failed-task", "running-task"],
        )
        self.assertEqual(response["items"][1]["progress"], 35)
        self.assertEqual(response["items"][1]["downloaded_bytes"], 350)
        self.assertEqual(response["counts"], {"all": 2, "active": 1, "attention": 1})

    def test_local_video_must_be_an_existing_absolute_video_path(self) -> None:
        with self.assertRaises(HTTPException) as relative_error:
            server.create_local_video(LocalVideoRequest(file_path="movie.mp4"))
        self.assertEqual(relative_error.exception.status_code, 422)

        text_file = self.root / "notes.txt"
        text_file.write_text("not video")
        with self.assertRaises(HTTPException) as type_error:
            server.create_local_video(LocalVideoRequest(file_path=str(text_file)))
        self.assertEqual(type_error.exception.status_code, 422)

    def test_local_video_can_be_removed_without_moving_source_file(self) -> None:
        video = self.root / "local.mp4"
        video.write_bytes(b"local")
        created = server.create_local_video(LocalVideoRequest(file_path=str(video)))

        with patch("app.server.move_to_trash") as move_to_trash:
            result = server.delete_video(created["id"], remove_file=False)

        move_to_trash.assert_not_called()
        self.assertEqual(result["trashed_files"], [])
        self.assertTrue(video.is_file())
        with self.assertRaises(HTTPException):
            server.download_record(created["id"])

    def test_local_video_can_move_source_file_to_trash(self) -> None:
        video = self.root / "local.mp4"
        video.write_bytes(b"local")
        created = server.create_local_video(LocalVideoRequest(file_path=str(video)))
        moved: list[Path] = []

        with patch("app.server.move_to_trash", side_effect=lambda path: moved.append(path) or path):
            result = server.delete_video(created["id"], remove_file=True)

        self.assertEqual(moved, [video.resolve()])
        self.assertEqual(result["trashed_files"], [str(video.resolve())])

    def test_denoise_api_routes_remain_registered(self) -> None:
        paths = {route.path for route in server.app.routes}
        self.assertIn("/api/v1/videos/{download_id}/denoise", paths)
        self.assertIn("/api/v1/videos/{download_id}/denoise/{job_id}/file", paths)

    def test_frontend_build_is_served_from_root(self) -> None:
        paths = {route.path for route in server.app.routes}
        self.assertIn("/", paths)
        self.assertIn("/assets", paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
