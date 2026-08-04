from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import server
from app.core import storage
from app.core.models import LocalVideoRequest


class LocalLibraryAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        data_patch = patch.object(server, "DATA_DIR", self.root / "app-data")
        database_patch.start()
        data_patch.start()
        self.addCleanup(database_patch.stop)
        self.addCleanup(data_patch.stop)
        storage.initialize_database()

    @staticmethod
    def probe() -> dict[str, object]:
        return {
            "media_type": "video", "duration": 100, "resolution": "1920x1080",
            "codec": "h264", "video_codec": "h264", "audio_codec": "aac",
            "audio_track_count": 1, "subtitle_track_count": 0,
            "bit_rate": 1_000_000, "format_name": "mov,mp4",
        }

    def test_local_video_gets_cached_cover_without_modifying_source(self) -> None:
        source = self.root / "source.mp4"
        source.write_bytes(b"unchanged-source")

        def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            Path(command[-1]).write_bytes(b"jpeg")
            return SimpleNamespace(returncode=0, stderr=b"")

        with (
            patch.object(server, "probe_local_media_file", return_value=self.probe()),
            patch.object(server.shutil, "which", return_value="/usr/local/bin/ffmpeg"),
            patch.object(server.subprocess, "run", side_effect=fake_run),
        ):
            video = server.register_local_video(LocalVideoRequest(file_path=str(source)))

        self.assertEqual(source.read_bytes(), b"unchanged-source")
        self.assertEqual(video["thumbnail"], f"/api/v1/videos/{video['id']}/thumbnail")
        self.assertTrue(server.local_video_thumbnail_path(video["id"]).is_file())

    def test_audio_import_does_not_generate_video_cover(self) -> None:
        source = self.root / "source.mp3"
        source.write_bytes(b"audio")
        probe = {**self.probe(), "media_type": "audio", "resolution": None}
        with (
            patch.object(server, "probe_local_media_file", return_value=probe),
            patch.object(server, "generate_local_video_thumbnail") as generate,
        ):
            video = server.register_local_video(LocalVideoRequest(file_path=str(source)))
        self.assertIsNone(video["thumbnail"])
        generate.assert_not_called()

    def test_startup_scan_runs_incrementally_and_persists_report(self) -> None:
        report = {"roots": [str(self.root)], "scanned": 2, "added": 1, "skipped": 1, "missing": 0,
                  "missing_items": [], "possible_moves": [], "failed": []}
        with (
            patch.object(server, "refresh_local_media_metadata") as refresh,
            patch.object(server, "get_download_settings", return_value={"scan_library_on_startup": True, "library_dirs": [str(self.root)]}),
            patch.object(server, "scan_library_directories", return_value=report) as scan,
            patch.object(server, "save_settings_values", return_value={}) as save,
        ):
            server.refresh_local_library_on_startup()
        refresh.assert_called_once_with()
        scan.assert_called_once_with()
        saved_report = save.call_args.args[0]["last_library_scan"]
        self.assertEqual(saved_report["status"], "completed")
        self.assertEqual(saved_report["added"], 1)
        self.assertIsNotNone(saved_report["finished_at"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
