from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from fastapi import HTTPException
from hypothesis import given, settings, strategies as st

from app import server
from app.core import storage
from app.core.models import IncompleteCleanupRequest, VideoRenameRequest


FUZZ_SETTINGS = settings(
    max_examples=500,
    deadline=None,
    derandomize=True,
    database=None,
    print_blob=True,
)


class SafetyEdgeCaseTests(unittest.TestCase):
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
        *,
        status: str = "failed",
        download_dir: str | None = None,
        file_path: str | None = None,
        output_files: object | None = None,
    ) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with storage.connection() as database:
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_type, status, title,
                    file_path, progress, downloaded_bytes, metadata_json, download_dir,
                    output_files_json, library_visible, task_deleted, file_origin,
                    created_at, updated_at
                ) VALUES (?, 'aria2', 'test', 'https://example.com/file', 'direct', ?,
                    'movie.mp4', ?, 0, 0, '{}', ?, ?, 0, 0, 'downloaded', ?, ?)
                """,
                (
                    download_id,
                    status,
                    file_path,
                    download_dir,
                    json.dumps(output_files) if output_files is not None else None,
                    timestamp,
                    timestamp,
                ),
            )

    def test_url_and_download_directory_validation_errors(self) -> None:
        for value in ("", "ftp://example.com/file", "https:///missing-host"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                server.validate_url(value)
        server.validate_url("https://example.com/video")

        with self.assertRaisesRegex(HTTPException, "绝对路径"):
            server.resolve_download_dir("relative/path")
        with patch.object(Path, "mkdir", side_effect=OSError("read only")):
            with self.assertRaisesRegex(HTTPException, "无法创建下载位置"):
                server.resolve_download_dir(str(self.root / "blocked"))

    def test_directory_pattern_validation_covers_unsafe_syntax(self) -> None:
        invalid_patterns = (
            "/absolute/{title}",
            "windows\\{title}",
            "{title!r}",
            "{title:>10}",
            "{title",
            "bad:name/{title}",
            "{title}//child",
        )
        for pattern in invalid_patterns:
            with self.subTest(pattern=pattern), self.assertRaises(HTTPException):
                server.validate_directory_pattern(pattern)

        self.assertEqual(
            server.normalize_library_dirs(["", f" {self.root} ", str(self.root)]),
            [str(self.root.resolve())],
        )
        with self.assertRaisesRegex(HTTPException, "绝对路径"):
            server.normalize_library_dirs(["relative/media"])
        self.assertEqual(server.safe_directory_value(" . /bad:* name. ", "fallback"), "_bad__ name")
        self.assertEqual(server.safe_directory_value("...", "fallback"), "fallback")

    def test_formatted_directory_rejects_symlink_escape_and_mkdir_failure(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        base = self.root / "downloads"
        base.mkdir()
        (base / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(HTTPException, "之外"):
            server.formatted_download_dir(
                str(base),
                "escape/{title}",
                platform="test",
                uploader="test",
                title="movie",
            )

        with (
            patch("app.server.resolve_download_dir", return_value=base.resolve()),
            patch.object(Path, "mkdir", side_effect=OSError("read only")),
            self.assertRaisesRegex(HTTPException, "无法创建格式化下载目录"),
        ):
            server.formatted_download_dir(
                str(base),
                "{title}",
                platform="test",
                uploader="test",
                title="movie",
            )

    def test_system_notification_covers_disabled_platform_and_process_errors(self) -> None:
        with patch("app.server.sys.platform", "linux"):
            self.assertFalse(server.send_system_notification("title", "message"))
        with (
            patch("app.server.sys.platform", "darwin"),
            patch("app.server.get_download_settings", return_value={"system_notifications": False}),
        ):
            self.assertFalse(server.send_system_notification("title", "message"))
        with (
            patch("app.server.sys.platform", "darwin"),
            patch("app.server.get_download_settings", return_value={"system_notifications": True}),
            patch("app.server.subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 5)),
        ):
            self.assertFalse(server.send_system_notification("title", "message"))

    def test_related_files_and_incomplete_task_boundaries(self) -> None:
        media = self.root / "movie.mp4"
        with patch.object(Path, "iterdir", side_effect=OSError("unreadable")):
            self.assertEqual(server.related_video_files(media), [media])

        self.assertEqual(server.incomplete_task_files({"status": "completed", "download_dir": str(self.root)}), [])
        self.assertEqual(server.incomplete_task_files({"status": "failed"}), [])
        outside = self.root.parent / "outside.mp4.part"
        record = {
            "id": "outside",
            "status": "failed",
            "download_dir": str(self.root),
            "file_path": str(outside),
            "engine": "yt-dlp",
        }
        with patch.object(Path, "exists", return_value=True):
            self.assertEqual(server.incomplete_task_files(record), [])

    def test_path_tree_size_covers_directories_symlinks_and_io_errors(self) -> None:
        directory = self.root / "tree"
        directory.mkdir()
        (directory / "one.part").write_bytes(b"123")
        (directory / "two.part").write_bytes(b"45")
        (directory / "link.part").symlink_to(directory / "one.part")
        self.assertEqual(server.path_tree_size(directory), (5, 2))

        file_path = MagicMock()
        file_path.is_file.return_value = True
        file_path.stat.side_effect = OSError("gone")
        self.assertEqual(server.path_tree_size(file_path), (0, 0))

        bad_child = MagicMock()
        bad_child.is_file.side_effect = OSError("gone")
        fake_directory = MagicMock()
        fake_directory.is_file.return_value = False
        fake_directory.rglob.return_value = [bad_child]
        self.assertEqual(server.path_tree_size(fake_directory), (0, 0))

        fake_directory.rglob.side_effect = OSError("unreadable")
        self.assertEqual(server.path_tree_size(fake_directory), (0, 0))

    def test_incomplete_preview_covers_missing_roots_torrent_cache_and_scan_errors(self) -> None:
        self.insert_download("no-root", download_dir=None)
        self.insert_download("missing-root", download_dir=str(self.root / "missing"))

        download_root = self.root / "downloads"
        torrent_dir = download_root / ".video-downloader" / "torrents"
        torrent_dir.mkdir(parents=True)
        torrent_file = torrent_dir / "torrent-cache.torrent"
        torrent_file.write_bytes(b"torrent")
        self.insert_download("torrent-cache", download_dir=str(download_root))
        preview = server.incomplete_residue_preview()
        self.assertIn(str(torrent_file.resolve()), {item["path"] for item in preview["items"]})

        outside = self.root.parent / "outside.part"
        missing = download_root / "missing.part"
        with patch("app.server.incomplete_task_files", return_value=[outside, missing, missing]):
            preview = server.incomplete_residue_preview()
        self.assertNotIn(str(outside), {item["path"] for item in preview["items"]})

        original_rglob = Path.rglob

        def failing_rglob(path: Path, pattern: str):
            if path == download_root.resolve():
                raise OSError("unreadable")
            return original_rglob(path, pattern)

        with patch.object(Path, "rglob", failing_rglob):
            server.incomplete_residue_preview()

    def test_incomplete_cleanup_rejects_stale_paths_deduplicates_and_reports_failures(self) -> None:
        parent = self.root / "residue"
        child = parent / "movie.part"
        parent.mkdir()
        child.write_bytes(b"123")
        preview = {
            "items": [{"path": str(parent.resolve())}, {"path": str(child.resolve())}],
            "total_items": 2,
            "total_bytes": 3,
        }
        with patch("app.server.incomplete_residue_preview", return_value=preview):
            with self.assertRaisesRegex(HTTPException, "已经变化"):
                server.cleanup_incomplete_residues(IncompleteCleanupRequest(paths=[str(self.root / "stale")]))

            with patch("app.server.move_to_trash", side_effect=RuntimeError("\x1b[31mdenied\x1b[0m")) as move:
                result = server.cleanup_incomplete_residues(
                    IncompleteCleanupRequest(paths=[str(child.resolve()), str(parent.resolve()), str(child.resolve())])
                )
        move.assert_called_once_with(parent.resolve())
        self.assertEqual(result["freed_bytes"], 0)
        self.assertEqual(result["failed_files"][0]["error"], "denied")

    def test_direct_download_file_name_covers_safe_unsafe_and_collision_cases(self) -> None:
        with self.assertRaises(HTTPException):
            server.direct_download_file_name(str(self.root), "../movie.mp4", "abcdef123")
        self.assertEqual(server.direct_download_file_name(str(self.root), "movie.mp4", "abcdef123"), "movie.mp4")
        (self.root / "movie.mp4.aria2").write_bytes(b"")
        self.assertEqual(
            server.direct_download_file_name(str(self.root), "movie.mp4", "abcdef123"),
            "movie [abcdef12].mp4",
        )
        (self.root / "archive").write_bytes(b"")
        self.assertEqual(server.direct_download_file_name(str(self.root), "archive", "123456789"), "archive [12345678]")

    def test_update_check_covers_cache_network_and_invalid_response(self) -> None:
        cached = {"current_version": "1", "latest_version": "2"}
        with (
            patch("app.server.yt_dlp_update_cache", cached),
            patch("app.server.yt_dlp_update_cache_at", 100.0),
            patch("app.server.time.monotonic", return_value=101.0),
        ):
            self.assertEqual(server.check_ytdlp_update(), cached)

        opener = MagicMock()
        opener.open.side_effect = URLError("offline")
        with patch("app.server.build_opener", return_value=opener):
            with self.assertRaisesRegex(HTTPException, "暂时无法检查"):
                server.check_ytdlp_update(force=True)

        response = MagicMock()
        response.read.return_value = json.dumps({"info": {"version": ""}}).encode()
        response.__enter__.return_value = response
        opener.open.side_effect = None
        opener.open.return_value = response
        with patch("app.server.build_opener", return_value=opener):
            with self.assertRaisesRegex(HTTPException, "有效的 yt-dlp 版本"):
                server.check_ytdlp_update(force=True)

    def test_replace_output_path_covers_invalid_unchanged_and_updated_records(self) -> None:
        root = self.root / "outputs"
        root.mkdir()
        old_path = root / "old.mp4"
        new_path = root / "new.mp4"
        self.insert_download("no-outputs", download_dir=str(root))
        server.replace_output_path("no-outputs", old_path.resolve(), new_path.resolve())

        self.insert_download("invalid-json", download_dir=str(root), output_files=[])
        with storage.connection() as database:
            database.execute("UPDATE downloads SET output_files_json = '{' WHERE id = 'invalid-json'")
        server.replace_output_path("invalid-json", old_path.resolve(), new_path.resolve())

        self.insert_download("not-list", download_dir=str(root), output_files={"relative_path": "old.mp4"})
        server.replace_output_path("not-list", old_path.resolve(), new_path.resolve())

        self.insert_download(
            "valid-outputs",
            download_dir=str(root),
            output_files=[{"relative_path": "old.mp4"}, {"relative_path": "other.mp4"}, "ignored"],
        )
        server.replace_output_path("valid-outputs", old_path.resolve(), new_path.resolve())
        with storage.connection() as database:
            value = database.execute(
                "SELECT output_files_json FROM downloads WHERE id = 'valid-outputs'"
            ).fetchone()["output_files_json"]
        self.assertEqual(json.loads(value)[0]["relative_path"], "new.mp4")

        outside = self.root / "outside.mp4"
        server.replace_output_path("valid-outputs", outside.resolve(), new_path.resolve())

    def test_rename_covers_invalid_title_collision_rollback_and_parent_update(self) -> None:
        with self.assertRaisesRegex(HTTPException, "有效的标题"):
            server.safe_media_title(" /:.. ")

        media = self.root / "old.mp4"
        subtitle = self.root / "old.srt"
        media.write_bytes(b"video")
        subtitle.write_text("subtitle", encoding="utf-8")
        video = {
            "id": "video",
            "file_origin": "local",
            "parent_download_id": "parent",
        }
        with (
            patch("app.server.library_video", return_value=video),
            patch("app.server.local_video_path", return_value=(media, self.root)),
            patch("app.server.update_download"),
            patch("app.server.publish_download"),
            patch("app.server.download_record", return_value={"id": "video"}),
            patch("app.server.replace_output_path") as replace,
        ):
            server.rename_video("video", VideoRenameRequest(title="new", rename_file=True))
        self.assertEqual(replace.call_count, 2)

        collision_media = self.root / "collision.mp4"
        target_media = self.root / "taken.mp4"
        collision_media.write_bytes(b"video")
        target_media.write_bytes(b"existing")
        with (
            patch("app.server.library_video", return_value={"id": "collision"}),
            patch("app.server.local_video_path", return_value=(collision_media, self.root)),
            self.assertRaisesRegex(HTTPException, "已存在文件"),
        ):
            server.rename_video("collision", VideoRenameRequest(title="taken", rename_file=True))

        rollback_media = self.root / "rollback.mp4"
        rollback_subtitle = self.root / "rollback.srt"
        rollback_media.write_bytes(b"video")
        rollback_subtitle.write_text("subtitle", encoding="utf-8")
        with (
            patch("app.server.library_video", return_value={"id": "rollback"}),
            patch("app.server.local_video_path", return_value=(rollback_media, self.root)),
            patch.object(Path, "rename", side_effect=[None, OSError("rename failed"), OSError("rollback failed")]),
            self.assertRaisesRegex(HTTPException, "文件重命名失败"),
        ):
            server.rename_video("rollback", VideoRenameRequest(title="failed", rename_file=True))


class SafetyFuzzTests(unittest.TestCase):
    @FUZZ_SETTINGS
    @given(st.text(max_size=400))
    def test_fuzz_media_title_is_bounded_and_path_safe(self, value: str) -> None:
        try:
            title = server.safe_media_title(value)
        except HTTPException as error:
            self.assertEqual(error.status_code, 422)
            return
        self.assertTrue(title)
        self.assertLessEqual(len(title), 180)
        self.assertNotRegex(title, r"[\\/:\x00]")
        self.assertEqual(title, title.strip())

    @FUZZ_SETTINGS
    @given(st.text(max_size=400), st.text(min_size=1, max_size=30))
    def test_fuzz_directory_component_is_bounded_and_safe(self, value: str, fallback: str) -> None:
        component = server.safe_directory_value(value, fallback)
        self.assertLessEqual(len(component), 120)
        self.assertNotRegex(component, r'[<>:"/\\|?*\x00-\x1f]')
        self.assertFalse(component.endswith((" ", ".")))

    @FUZZ_SETTINGS
    @given(st.text(max_size=120))
    def test_fuzz_directory_pattern_only_returns_safe_relative_patterns(self, value: str) -> None:
        try:
            pattern = server.validate_directory_pattern(value)
        except HTTPException as error:
            self.assertEqual(error.status_code, 422)
            return
        self.assertFalse(pattern.startswith(("/", "\\")))
        self.assertNotIn("\\", pattern)
        self.assertNotRegex(pattern, r'[<>:"|?*\x00-\x1f]')

    @FUZZ_SETTINGS
    @given(
        st.text(
            alphabet=st.characters(blacklist_characters="/\x00", blacklist_categories=("Cs",)),
            min_size=1,
            max_size=80,
        ).filter(lambda value: value not in {".", ".."} and Path(value).name == value),
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=32),
    )
    def test_fuzz_safe_direct_filename_stays_inside_download_directory(self, name: str, download_id: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = server.direct_download_file_name(temporary_directory, name, download_id)
            target = (Path(temporary_directory) / result).resolve()
            target.relative_to(Path(temporary_directory).resolve())
            self.assertEqual(Path(result).name, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
