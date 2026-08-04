from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core import storage
from app.core.models import PlaylistDownloadRequest, VideoFavoriteRequest


class LibraryItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()
        with storage.connection() as database:
            database.execute(
                "UPDATE app_settings SET value = ? WHERE key = 'download_dir'",
                (json.dumps(str(self.root / "downloads")),),
            )

    def insert_playlist(self, playlist_id: str, **values: object) -> None:
        record = {
            "source_url": "https://www.youtube.com/playlist?list=PL-test",
            "source_platform": "YouTube",
            "external_id": "PL-test",
            "title": "测试合集",
            "uploader": "测试作者",
            "thumbnail": "https://example.com/cover.jpg",
            "description": "合集说明",
            "total_count": 3,
            "source_total_count": 8,
            "created_at": "2026-07-31T01:00:00+00:00",
            "updated_at": "2026-07-31T01:00:00+00:00",
        }
        record.update(values)
        columns = ["id", *record.keys()]
        with storage.connection() as database:
            database.execute(
                f"INSERT INTO playlists ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [playlist_id, *record.values()],
            )

    def insert_download(self, download_id: str, **values: object) -> None:
        record = {
            "engine": "yt-dlp",
            "engine_version": "test",
            "source_url": f"https://www.youtube.com/watch?v={download_id}",
            "source_platform": "YouTube",
            "status": "completed",
            "title": download_id,
            "duration": 60,
            "resolution": "1080p",
            "file_path": str(self.root / "downloads" / f"{download_id}.mp4"),
            "file_size": 100,
            "library_visible": 1,
            "created_at": "2026-07-31T01:00:00+00:00",
            "updated_at": "2026-07-31T01:00:00+00:00",
        }
        record.update(values)
        columns = ["id", *record.keys()]
        with storage.connection() as database:
            database.execute(
                f"INSERT INTO downloads ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [download_id, *record.values()],
            )

    def library(self, **filters: object) -> dict[str, object]:
        return server.list_library_items(
            q=filters.get("q"),
            platform=filters.get("platform", []),
            file_format=filters.get("file_format", []),
            resolution=filters.get("resolution"),
            media_type=filters.get("media_type"),
            include_playlists=filters.get("include_playlists", False),
            favorite_only=filters.get("favorite_only", False),
            limit=100,
        )

    def test_playlist_summary_keeps_source_metadata(self) -> None:
        summary = server.playlist_summary(
            {
                "_type": "playlist",
                "id": "PL-source",
                "title": "来源合集",
                "channel": "来源作者",
                "description": "来源说明",
                "playlist_count": 12,
                "entries": [
                    {
                        "title": "第一集",
                        "url": "https://www.youtube.com/watch?v=first",
                        "playlist_index": 1,
                    }
                ],
            },
            "https://www.youtube.com/playlist?list=PL-source",
        )

        self.assertEqual(summary["external_id"], "PL-source")
        self.assertEqual(summary["description"], "来源说明")
        self.assertEqual(summary["source_total_count"], 12)
        self.assertEqual(summary["entry_count"], 1)

    def test_create_playlist_persists_metadata_and_selected_count(self) -> None:
        inspect_id = "inspect-library"
        server.inspect_jobs[inspect_id] = {
            "id": inspect_id,
            "status": "completed",
            "media": {
                "kind": "playlist",
                "external_id": "PL-source",
                "title": "来源合集",
                "uploader": "来源作者",
                "thumbnail": "https://example.com/source.jpg",
                "description": "来源说明",
                "source_url": "https://www.youtube.com/playlist?list=PL-source",
                "source_platform": "YouTube",
                "source_total_count": 5,
                "entries": [
                    {"title": "第一集", "webpage_url": "https://www.youtube.com/watch?v=first", "playlist_index": 1},
                    {
                        "title": "第二集",
                        "uploader": "单集作者",
                        "thumbnail": "https://example.com/second.jpg",
                        "webpage_url": "https://www.youtube.com/watch?v=second",
                        "playlist_index": 2,
                    },
                ],
            },
        }
        self.addCleanup(server.inspect_jobs.pop, inspect_id, None)

        playlist = server.create_playlist_download(
            PlaylistDownloadRequest(
                inspect_id=inspect_id,
                entry_urls=["https://www.youtube.com/watch?v=second"],
            ),
            BackgroundTasks(),
        )

        self.assertEqual(playlist["external_id"], "PL-source")
        self.assertEqual(playlist["description"], "来源说明")
        self.assertEqual(playlist["source_total_count"], 5)
        self.assertEqual(playlist["total_count"], 1)
        with storage.connection() as database:
            task = database.execute(
                "SELECT title, uploader, thumbnail, webpage_url FROM downloads WHERE playlist_id = ?",
                (playlist["id"],),
            ).fetchone()
        self.assertEqual(task["title"], "第二集")
        self.assertEqual(task["uploader"], "单集作者")
        self.assertEqual(task["thumbnail"], "https://example.com/second.jpg")
        self.assertEqual(task["webpage_url"], "https://www.youtube.com/watch?v=second")

    def test_library_groups_completed_playlist_videos(self) -> None:
        self.insert_playlist("playlist-1")
        self.insert_download("standalone", title="独立视频", created_at="2026-07-31T02:00:00+00:00")
        self.insert_download("child-1", title="合集第一集", playlist_id="playlist-1", playlist_index=1)
        self.insert_download(
            "child-2",
            title="合集第二集",
            playlist_id="playlist-1",
            playlist_index=2,
            duration=90,
            file_size=200,
            resolution="720p",
        )
        self.insert_download(
            "child-3",
            title="尚未完成",
            playlist_id="playlist-1",
            playlist_index=3,
            status="running",
        )

        result = self.library()

        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 2)
        playlist = next(item for item in result["items"] if item["kind"] == "playlist")
        self.assertEqual(playlist["completed_count"], 2)
        self.assertEqual(playlist["total_count"], 3)
        self.assertEqual(playlist["source_total_count"], 8)
        self.assertEqual(playlist["duration"], 150)
        self.assertEqual(playlist["file_size"], 300)
        self.assertEqual(playlist["resolutions"], ["1080p", "720p"])
        self.assertEqual(playlist["favorite"], 0)

    def test_playlist_can_be_favorited_independently(self) -> None:
        self.insert_playlist("playlist-1")
        self.insert_download("child-1", playlist_id="playlist-1", favorite=0)

        updated = server.update_playlist_favorite(
            "playlist-1",
            VideoFavoriteRequest(favorite=True),
        )
        filtered = self.library(favorite_only=True)

        self.assertEqual(updated, {"id": "playlist-1", "favorite": 1})
        self.assertEqual([item["id"] for item in filtered["items"]], ["playlist-1"])
        self.assertEqual(filtered["items"][0]["favorite"], 1)
        self.assertEqual(filtered["items"][0]["favorite_count"], 0)

    def test_deleting_playlist_removes_its_videos_and_keeps_files_when_requested(self) -> None:
        self.insert_playlist("playlist-1")
        self.insert_download("child-1", playlist_id="playlist-1", playlist_index=1)

        deleted = server.delete_playlist("playlist-1", remove_files=False)

        self.assertEqual(
            deleted,
            {"id": "playlist-1", "deleted_video_count": 1, "trashed_files": []},
        )
        with self.assertRaises(HTTPException):
            server.download_record("child-1")
        self.assertEqual(self.library()["items"], [])
        with self.assertRaises(HTTPException) as error:
            server.get_playlist("playlist-1")
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_playlist_moves_local_files_to_trash_by_default(self) -> None:
        self.insert_playlist("playlist-1")
        media_path = self.root / "downloads" / "child-1.mp4"
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"video")
        self.insert_download(
            "child-1",
            playlist_id="playlist-1",
            file_path=str(media_path),
            download_dir=str(media_path.parent),
        )

        with patch.object(server, "move_to_trash") as move_to_trash:
            deleted = server.delete_playlist("playlist-1")

        move_to_trash.assert_called_once_with(media_path.resolve())
        self.assertEqual(deleted["trashed_files"], [str(media_path.resolve())])
        with self.assertRaises(HTTPException):
            server.download_record("child-1")

    def test_library_filters_playlist_by_collection_or_child_information(self) -> None:
        self.insert_playlist("playlist-1", title="陶艺教程")
        self.insert_download("child-1", title="制作小碗", playlist_id="playlist-1", playlist_index=1)
        self.insert_download(
            "audio-1",
            title="配乐",
            file_path=str(self.root / "downloads" / "audio-1.mp3"),
        )

        self.assertEqual(len(self.library(q="陶艺")["items"]), 1)
        self.assertEqual(len(self.library(q="小碗")["items"]), 1)
        self.assertEqual(len(self.library(file_format=["mp4"])["items"]), 1)
        self.assertEqual(len(self.library(resolution="720p")["items"]), 0)
        self.assertEqual(
            [item["id"] for item in self.library(media_type="audio")["items"]],
            ["audio-1"],
        )
        self.assertEqual(
            [item["id"] for item in self.library(media_type="playlist")["items"]],
            ["playlist-1"],
        )
        self.assertEqual(
            [item["id"] for item in self.library(media_type="video", include_playlists=True)["items"]],
            ["playlist-1"],
        )


class PlaylistMigrationTests(unittest.TestCase):
    def test_existing_playlist_table_gets_new_metadata_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(database_path)) as database:
                with database:
                    database.execute(
                        """
                        CREATE TABLE playlists (
                            id TEXT PRIMARY KEY,
                            source_url TEXT NOT NULL,
                            source_platform TEXT,
                            title TEXT NOT NULL,
                            uploader TEXT,
                            thumbnail TEXT,
                            total_count INTEGER NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    database.execute(
                        "INSERT INTO playlists VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        ("legacy", "https://example.com/list", "YouTube", "旧合集", None, None, 4, "now", "now"),
                    )

            with patch.object(storage, "DATABASE_PATH", database_path):
                storage.initialize_database()
                with storage.connection() as database:
                    columns = {row["name"] for row in database.execute("PRAGMA table_info(playlists)")}
                    row = database.execute(
                        "SELECT external_id, description, source_total_count FROM playlists WHERE id = 'legacy'"
                    ).fetchone()

            self.assertTrue({"external_id", "description", "source_total_count", "favorite"}.issubset(columns))
            self.assertIsNone(row["external_id"])
            self.assertIsNone(row["description"])
            self.assertEqual(row["source_total_count"], 4)
            with patch.object(storage, "DATABASE_PATH", database_path):
                with storage.connection() as database:
                    favorite = database.execute(
                        "SELECT favorite FROM playlists WHERE id = 'legacy'"
                    ).fetchone()["favorite"]
            self.assertEqual(favorite, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
