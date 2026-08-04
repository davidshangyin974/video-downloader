from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import server
from app.core import storage
from app.core.models import (
    LocalDirectoryScanRequest,
    LocalMediaBatchRequest,
    PlaybackProgressRequest,
    VideoFavoriteRequest,
    VideoRelinkRequest,
    VideoWatchedRequest,
)


class PersonalLibraryTests(unittest.TestCase):
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

    def insert_video(self, video_id: str, **values: object) -> None:
        video_path = self.root / f"{video_id}.mp4"
        video_path.write_bytes(video_id.encode())
        record = {
            "engine": "local",
            "engine_version": "filesystem",
            "source_url": video_path.as_uri(),
            "source_platform": "本地文件",
            "source_type": "local_file",
            "status": "completed",
            "title": video_id,
            "duration": 100,
            "file_path": str(video_path),
            "file_size": video_path.stat().st_size,
            "download_dir": str(self.root),
            "library_visible": 1,
            "task_deleted": 1,
            "file_origin": "local",
            "created_at": "2026-07-31T01:00:00+00:00",
            "updated_at": "2026-07-31T01:00:00+00:00",
        }
        record.update(values)
        columns = ["id", *record.keys()]
        with storage.connection() as database:
            database.execute(
                f"INSERT INTO downloads ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [video_id, *record.values()],
            )

    def library(self, **values: object) -> dict[str, object]:
        return server.list_library_items(
            q=None,
            platform=[],
            file_format=[],
            resolution=None,
            media_type=values.get("media_type"),
            page=int(values.get("page", 1)),
            page_size=int(values.get("page_size", 10)),
            sort_by=str(values.get("sort_by", "created_at")),
            sort_order=str(values.get("sort_order", "desc")),
            favorite_only=bool(values.get("favorite_only", False)),
            file_status=str(values.get("file_status", "all")),
            limit=None,
        )

    def test_progress_favorite_and_watched_state_are_persisted(self) -> None:
        self.insert_video("long-video")

        favorite = server.update_video_favorite(
            "long-video", VideoFavoriteRequest(favorite=True)
        )
        progress = server.update_video_progress(
            "long-video", PlaybackProgressRequest(position=42, duration=100)
        )

        self.assertEqual(favorite["favorite"], 1)
        self.assertEqual(progress["watch_position"], 42)
        self.assertEqual(progress["watched"], 0)
        self.assertIsNotNone(progress["last_watched_at"])

        watched = server.update_video_progress(
            "long-video", PlaybackProgressRequest(position=95, duration=100)
        )
        self.assertEqual(watched["watched"], 1)

        reset = server.update_video_watched(
            "long-video", VideoWatchedRequest(watched=False)
        )
        self.assertEqual(reset["watched"], 0)
        self.assertEqual(reset["watch_position"], 0)

    def test_playback_progress_does_not_clear_explicit_watched_state(self) -> None:
        self.insert_video("watched-video", watched=1, watch_position=100)

        progress = server.update_video_progress(
            "watched-video", PlaybackProgressRequest(position=5, duration=100)
        )

        self.assertEqual(progress["watch_position"], 5)
        self.assertEqual(progress["watched"], 1)

    def test_continue_watching_and_next_episode_are_derived_from_watch_state(self) -> None:
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO playlists (
                    id, source_url, source_platform, title, total_count,
                    source_total_count, created_at, updated_at
                ) VALUES ('course', 'https://example.com/course', 'Example',
                          '个人课程', 3, 3, '2026-07-31T00:00:00+00:00',
                          '2026-07-31T00:00:00+00:00')
                """
            )
        self.insert_video(
            "standalone-progress",
            watch_position=35,
            watched=0,
            last_watched_at="2026-08-02T02:00:00+00:00",
        )
        self.insert_video(
            "course-1",
            playlist_id="course",
            playlist_index=1,
            watch_position=100,
            watched=1,
            last_watched_at="2026-08-02T01:00:00+00:00",
        )
        self.insert_video(
            "course-2",
            playlist_id="course",
            playlist_index=2,
            watch_position=0,
            watched=0,
        )
        self.insert_video(
            "course-3",
            playlist_id="course",
            playlist_index=3,
            watch_position=0,
            watched=0,
        )

        result = server.continue_watching_items()

        self.assertEqual(
            [item["video"]["id"] for item in result["continuing"]],
            ["standalone-progress"],
        )
        self.assertEqual(
            [item["video"]["id"] for item in result["next_up"]],
            ["course-2"],
        )
        self.assertEqual(result["next_up"][0]["playlist_title"], "个人课程")

    def test_unfinished_playlist_episode_does_not_surface_the_following_episode(self) -> None:
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO playlists (
                    id, source_url, source_platform, title, total_count,
                    source_total_count, created_at, updated_at
                ) VALUES ('series', 'https://example.com/series', 'Example',
                          '连续剧', 2, 2, '2026-07-31T00:00:00+00:00',
                          '2026-07-31T00:00:00+00:00')
                """
            )
        self.insert_video(
            "series-1",
            playlist_id="series",
            playlist_index=1,
            watch_position=40,
            watched=0,
            last_watched_at="2026-08-02T02:00:00+00:00",
        )
        self.insert_video(
            "series-2",
            playlist_id="series",
            playlist_index=2,
        )

        result = server.continue_watching_items()

        self.assertEqual([item["video"]["id"] for item in result["continuing"]], ["series-1"])
        self.assertEqual(result["next_up"], [])

    def test_library_can_filter_sort_and_paginate(self) -> None:
        self.insert_video(
            "charlie", title="Charlie", file_size=30, favorite=1,
            created_at="2026-07-31T03:00:00+00:00",
        )
        self.insert_video(
            "alpha", title="Alpha", file_size=10,
            created_at="2026-07-31T01:00:00+00:00",
        )
        self.insert_video(
            "bravo", title="Bravo", file_size=20, favorite=1,
            created_at="2026-07-31T02:00:00+00:00",
        )

        first_page = self.library(
            page=1, page_size=2, sort_by="title", sort_order="asc"
        )
        second_page = self.library(
            page=2, page_size=2, sort_by="title", sort_order="asc"
        )
        favorites = self.library(
            favorite_only=True, sort_by="file_size", sort_order="asc"
        )

        self.assertEqual(first_page["total"], 3)
        self.assertEqual(first_page["total_pages"], 2)
        self.assertEqual([item["title"] for item in first_page["items"]], ["Alpha", "Bravo"])
        self.assertEqual([item["title"] for item in second_page["items"]], ["Charlie"])
        self.assertEqual([item["title"] for item in favorites["items"]], ["Bravo", "Charlie"])

    def test_manual_directory_scan_adds_new_videos_and_skips_existing_ones(self) -> None:
        library_root = self.root / "library"
        nested = library_root / "nested"
        nested.mkdir(parents=True)
        (library_root / "first.mp4").write_bytes(b"first")
        (nested / "second.mkv").write_bytes(b"second")
        (nested / "notes.txt").write_text("not a video")
        with storage.connection() as database:
            database.execute(
                "UPDATE app_settings SET value = ? WHERE key = 'library_dirs'",
                (json.dumps([str(library_root)]),),
            )

        with patch.object(
            server,
            "probe_local_media_file",
            return_value={
                "media_type": "video",
                "duration": 12.5,
                "resolution": "1280x720",
                "codec": "h264",
                "bit_rate": 1_000_000,
                "format_name": "mov,mp4",
            },
        ):
            first_scan = server.scan_library_directories()
            second_scan = server.scan_library_directories()

        self.assertEqual(first_scan["scanned"], 2)
        self.assertEqual(first_scan["added"], 2)
        self.assertEqual(first_scan["failed"], [])
        self.assertEqual(second_scan["added"], 0)
        self.assertEqual(second_scan["skipped"], 2)
        self.assertEqual(self.library()["total"], 2)

    def test_directory_scan_lists_video_and_audio_for_selected_batch_add(self) -> None:
        media_root = self.root / "select-media"
        nested = media_root / "nested"
        nested.mkdir(parents=True)
        video_path = media_root / "movie.mp4"
        audio_path = nested / "song.mp3"
        video_path.write_bytes(b"video")
        audio_path.write_bytes(b"audio")
        (media_root / "notes.txt").write_text("ignore")

        scan = server.scan_local_media_directory(
            LocalDirectoryScanRequest(directory_path=str(media_root))
        )
        self.assertEqual(scan["total"], 2)
        self.assertEqual(scan["available"], 2)
        self.assertEqual(
            {item["media_type"] for item in scan["files"]},
            {"video", "audio"},
        )

        with patch.object(
            server,
            "probe_local_media_file",
            return_value={
                "media_type": "audio",
                "duration": 185.25,
                "resolution": None,
                "codec": "mp3",
                "bit_rate": 320_000,
                "format_name": "mp3",
            },
        ):
            added = server.create_local_media_batch(
                LocalMediaBatchRequest(file_paths=[str(audio_path.resolve())])
            )
        self.assertEqual(added["added"], 1)
        self.assertEqual(added["failed"], 0)
        self.assertEqual(self.library()["total"], 1)
        audio = self.library()["items"][0]
        self.assertEqual(audio["title"], "song")
        self.assertEqual(audio["duration"], 185.25)
        self.assertEqual(audio["metadata"]["media_type"], "audio")
        self.assertEqual(audio["metadata"]["bit_rate"], 320_000)
        self.assertEqual(self.library(media_type="audio")["total"], 1)
        self.assertEqual(self.library(media_type="video")["total"], 0)

        rescanned = server.scan_local_media_directory(
            LocalDirectoryScanRequest(directory_path=str(media_root))
        )
        states = {item["path"]: item["already_added"] for item in rescanned["files"]}
        self.assertFalse(states[str(video_path.resolve())])
        self.assertTrue(states[str(audio_path.resolve())])

    def test_ffprobe_media_information_is_parsed(self) -> None:
        probe_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "duration": "8.0",
                    "disposition": {"attached_pic": 0},
                },
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "192000"},
            ],
            "format": {
                "duration": "8.25",
                "bit_rate": "1800000",
                "format_name": "mov,mp4,m4a",
            },
        }
        with (
            patch.object(server.shutil, "which", return_value="/usr/local/bin/ffprobe"),
            patch.object(
                server.subprocess,
                "run",
                return_value=server.subprocess.CompletedProcess(
                    args=["ffprobe"],
                    returncode=0,
                    stdout=json.dumps(probe_output),
                    stderr="",
                ),
            ),
        ):
            info = server.probe_local_media_file(self.root / "movie.mp4")

        self.assertEqual(info["media_type"], "video")
        self.assertEqual(info["duration"], 8.25)
        self.assertEqual(info["resolution"], "1920x1080")
        self.assertEqual(info["codec"], "h264")
        self.assertEqual(info["bit_rate"], 192_000)

    def test_legacy_local_media_metadata_is_filled_on_startup_refresh(self) -> None:
        self.insert_video(
            "legacy-local",
            duration=None,
            resolution=None,
            metadata_json="{}",
        )
        with patch.object(
            server,
            "probe_local_media_file",
            return_value={
                "media_type": "audio",
                "duration": 95.5,
                "resolution": None,
                "codec": "aac",
                "bit_rate": 256_000,
                "format_name": "mov,mp4,m4a",
            },
        ):
            server.refresh_local_media_metadata()

        item = self.library(media_type="audio")["items"][0]
        self.assertEqual(item["id"], "legacy-local")
        self.assertEqual(item["duration"], 95.5)
        self.assertEqual(item["metadata"]["bit_rate"], 256_000)

    def test_directory_selection_scans_selected_directory_immediately(self) -> None:
        media_root = self.root / "chosen-media"
        media_root.mkdir()
        (media_root / "movie.mp4").write_bytes(b"video")

        with (
            patch.object(server.sys, "platform", "darwin"),
            patch.object(
                server.subprocess,
                "run",
                return_value=server.subprocess.CompletedProcess(
                    args=["osascript"],
                    returncode=0,
                    stdout=f"{media_root}\n",
                    stderr="",
                ),
            ),
        ):
            result = server.select_local_media_directory()

        self.assertFalse(result["cancelled"])
        self.assertEqual(result["directory_path"], str(media_root.resolve()))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["files"][0]["name"], "movie.mp4")

    def test_library_scan_reports_missing_and_possible_moved_file_without_auto_relink(self) -> None:
        self.insert_video("moved-video", title="移动的视频")
        original = self.root / "moved-video.mp4"
        original_bytes = original.read_bytes()
        original.unlink()
        library_root = self.root / "library"
        library_root.mkdir()
        candidate = library_root / "moved-video.mp4"
        candidate.write_bytes(original_bytes)
        with storage.connection() as database:
            database.execute(
                "UPDATE app_settings SET value = ? WHERE key = 'library_dirs'",
                (json.dumps([str(library_root)]),),
            )

        report = server.scan_library_directories()

        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["added"], 0)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["missing_items"][0]["id"], "moved-video")
        self.assertEqual(report["possible_moves"][0]["candidate_path"], str(candidate.resolve()))
        self.assertEqual(self.library(file_status="missing")["total"], 1)

    def test_relink_updates_missing_record_and_rejects_media_type_change(self) -> None:
        self.insert_video("relink-video", metadata_json=json.dumps({"media_type": "video"}))
        old_path = self.root / "relink-video.mp4"
        old_path.unlink()
        replacement = self.root / "replacement.mp4"
        replacement.write_bytes(b"replacement")
        probe = {
            "media_type": "video",
            "duration": 12.5,
            "resolution": "1920x1080",
            "codec": "h264",
            "bit_rate": 1_500_000,
            "format_name": "mov,mp4",
        }

        with patch.object(server, "probe_local_media_file", return_value=probe):
            updated = server.relink_video(
                "relink-video",
                VideoRelinkRequest(file_path=str(replacement)),
            )

        self.assertEqual(updated["file_path"], str(replacement.resolve()))
        self.assertTrue(updated["file_exists"])
        self.assertEqual(updated["duration"], 12.5)
        self.assertEqual(self.library(file_status="missing")["total"], 0)
        self.assertEqual(self.library(file_status="available")["total"], 1)

        audio = self.root / "wrong.mp3"
        audio.write_bytes(b"audio")
        with patch.object(server, "probe_local_media_file", return_value={**probe, "media_type": "audio"}):
            with self.assertRaisesRegex(Exception, "文件类型与原媒体记录不一致"):
                server.relink_video("relink-video", VideoRelinkRequest(file_path=str(audio)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
