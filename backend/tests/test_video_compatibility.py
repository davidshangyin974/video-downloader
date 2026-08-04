from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage
from app.core.models import ExternalPlayerRequest


class VideoCompatibilityTests(unittest.TestCase):
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
        with storage.connection() as database:
            database.execute(
                "UPDATE app_settings SET value = ? WHERE key = 'download_dir'",
                (f'"{self.root / "downloads"}"',),
            )

    def insert_video(self, suffix: str = ".mkv") -> Path:
        media = self.root / f"source{suffix}"
        media.write_bytes(b"video")
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_platform,
                    status, title, file_path, file_size, download_dir,
                    library_visible, created_at, updated_at
                ) VALUES ('video', 'local', 'filesystem', ?, '本地文件',
                          'completed', '测试视频', ?, ?, ?, 1, 'now', 'now')
                """,
                (media.as_uri(), str(media), media.stat().st_size, str(self.root)),
            )
        return media

    @staticmethod
    def probe(video_codec: str = "h264", audio_codec: str = "aac") -> dict[str, object]:
        return {
            "media_type": "video",
            "duration": 60,
            "resolution": "1920x1080",
            "codec": video_codec,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "audio_track_count": 1,
            "subtitle_track_count": 0,
            "bit_rate": 1_000_000,
            "format_name": "matroska",
        }

    def test_compatible_codec_in_mkv_offers_fast_remux(self) -> None:
        self.insert_video(".mkv")
        with (
            patch.object(server, "probe_local_media_file", return_value=self.probe()),
            patch.object(server, "external_player_availability", return_value={"system": True, "iina": True, "vlc": False}),
        ):
            result = server.video_compatibility("video")

        self.assertFalse(result["direct_play_likely"])
        self.assertTrue(result["remux_available"])
        self.assertTrue(result["players"]["iina"])

    def test_unsupported_codec_recommends_external_player_without_remux(self) -> None:
        self.insert_video(".mkv")
        with (
            patch.object(server, "probe_local_media_file", return_value=self.probe("vp9", "opus")),
            patch.object(server, "external_player_availability", return_value={"system": True, "iina": False, "vlc": True}),
        ):
            result = server.video_compatibility("video")

        self.assertFalse(result["direct_play_likely"])
        self.assertFalse(result["remux_available"])
        self.assertIn("VP9", result["reason"])

    def test_external_player_open_uses_argument_array_and_checks_installation(self) -> None:
        media = self.insert_video(".mp4")
        process = Mock(pid=1234)
        with (
            patch.object(server.sys, "platform", "darwin"),
            patch.object(server, "external_player_availability", return_value={"system": True, "iina": True, "vlc": False}),
            patch.object(server.subprocess, "Popen", return_value=process) as popen,
        ):
            result = server.open_video_in_external_player(
                "video", ExternalPlayerRequest(player="iina")
            )
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(popen.call_args.args[0], ["open", "-a", "IINA", str(media.resolve())])

        with (
            patch.object(server.sys, "platform", "darwin"),
            patch.object(server, "external_player_availability", return_value={"system": True, "iina": False, "vlc": False}),
        ):
            with self.assertRaises(HTTPException) as context:
                server.open_video_in_external_player(
                    "video", ExternalPlayerRequest(player="vlc")
                )
        self.assertEqual(context.exception.status_code, 409)

    def test_remux_job_registers_verified_compatible_copy(self) -> None:
        self.insert_video(".mkv")
        background = BackgroundTasks()
        with patch.object(server, "video_compatibility", return_value={"remux_available": True}):
            job = server.create_compatibility_remux("video", background)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(background.tasks), 1)

        class FakeProcess:
            returncode = 0

            def __init__(self, command: list[str], **_kwargs: object) -> None:
                Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(command[-1]).write_bytes(b"compatible")

            def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        with (
            patch.object(server, "video_compatibility", return_value={"remux_available": True}),
            patch.object(server.shutil, "which", return_value="/usr/local/bin/ffmpeg"),
            patch.object(server.subprocess, "Popen", side_effect=FakeProcess),
            patch.object(server, "probe_local_media_file", return_value=self.probe()),
            patch.object(server, "register_local_video", return_value={"id": "compatible-video"}),
        ):
            server.run_compatibility_remux(job["id"], "video")

        completed = server.media_derivative_job_record(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["library_video_id"], "compatible-video")
        self.assertTrue(Path(completed["file_path"]).is_file())
        with (
            patch.object(server, "video_compatibility", return_value={"remux_available": True}),
            self.assertRaises(HTTPException) as duplicate,
        ):
            server.create_compatibility_remux("video", BackgroundTasks())
        self.assertEqual(duplicate.exception.status_code, 409)

    def test_remux_disk_preflight_failure_marks_job_failed(self) -> None:
        self.insert_video(".mkv")
        background = BackgroundTasks()
        with patch.object(server, "video_compatibility", return_value={"remux_available": True}):
            job = server.create_compatibility_remux("video", background)

        with (
            patch.object(server, "video_compatibility", return_value={"remux_available": True}),
            patch.object(server.shutil, "which", return_value="/usr/local/bin/ffmpeg"),
            patch.object(
                server,
                "ensure_disk_capacity",
                side_effect=HTTPException(status_code=409, detail="磁盘空间不足"),
            ),
        ):
            server.run_compatibility_remux(job["id"], "video")

        failed = server.media_derivative_job_record(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("磁盘空间不足", failed["error"])

    def test_video_cannot_be_deleted_while_compatibility_copy_is_running(self) -> None:
        self.insert_video(".mkv")
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO media_derivative_jobs (
                    id, download_id, kind, status, created_at, updated_at
                ) VALUES ('active-job', 'video', 'compatibility_remux', 'running', 'now', 'now')
                """
            )
        with self.assertRaises(HTTPException) as context:
            server.delete_video("video", remove_file=False)
        self.assertEqual(context.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
