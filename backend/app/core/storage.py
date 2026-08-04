from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from .config import DATABASE_PATH, DEFAULT_DOWNLOAD_SETTINGS


database_initialization_lock = threading.Lock()


def now() -> str:
    return datetime.now(UTC).isoformat()


def open_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE_PATH)
    database.row_factory = sqlite3.Row
    return database


@contextmanager
def managed_connection() -> Iterator[sqlite3.Connection]:
    database = open_connection()
    try:
        with database:
            yield database
    finally:
        database.close()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    if not DATABASE_PATH.exists():
        with database_initialization_lock:
            if not DATABASE_PATH.exists():
                initialize_database()
    with managed_connection() as database:
        yield database


def initialize_database() -> None:
    with managed_connection() as database:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                id TEXT PRIMARY KEY,
                engine TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_platform TEXT,
                requested_format TEXT,
                status TEXT NOT NULL,
                title TEXT,
                uploader TEXT,
                thumbnail TEXT,
                webpage_url TEXT,
                video_id TEXT,
                duration REAL,
                upload_date TEXT,
                resolution TEXT,
                file_path TEXT,
                file_size INTEGER,
                progress REAL NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER,
                speed REAL,
                eta INTEGER,
                error TEXT,
                metadata_json TEXT,
                download_dir TEXT,
                write_thumbnail INTEGER,
                write_info_json INTEGER,
                yt_dlp_config TEXT,
                ffmpeg_config TEXT,
                playlist_id TEXT,
                playlist_index INTEGER,
                engine_task_id TEXT,
                engine_metadata_json TEXT,
                resolved_url TEXT,
                source_type TEXT,
                output_files_json TEXT,
                library_visible INTEGER NOT NULL DEFAULT 1,
                task_deleted INTEGER NOT NULL DEFAULT 0,
                file_origin TEXT NOT NULL DEFAULT 'downloaded',
                favorite INTEGER NOT NULL DEFAULT 0,
                watch_position REAL NOT NULL DEFAULT 0,
                watched INTEGER NOT NULL DEFAULT 0,
                last_watched_at TEXT,
                parent_download_id TEXT,
                parent_output_index INTEGER,
                restart_pending INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 0,
                upgrade_from_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in database.execute("PRAGMA table_info(downloads)")}
        additions = {
            "source_platform": "TEXT",
            "download_dir": "TEXT",
            "write_thumbnail": "INTEGER",
            "write_info_json": "INTEGER",
            "yt_dlp_config": "TEXT",
            "ffmpeg_config": "TEXT",
            "playlist_id": "TEXT",
            "playlist_index": "INTEGER",
            "engine_task_id": "TEXT",
            "engine_metadata_json": "TEXT",
            "resolved_url": "TEXT",
            "source_type": "TEXT",
            "output_files_json": "TEXT",
            "library_visible": "INTEGER NOT NULL DEFAULT 1",
            "task_deleted": "INTEGER NOT NULL DEFAULT 0",
            "file_origin": "TEXT NOT NULL DEFAULT 'downloaded'",
            "favorite": "INTEGER NOT NULL DEFAULT 0",
            "watch_position": "REAL NOT NULL DEFAULT 0",
            "watched": "INTEGER NOT NULL DEFAULT 0",
            "last_watched_at": "TEXT",
            "parent_download_id": "TEXT",
            "parent_output_index": "INTEGER",
            "restart_pending": "INTEGER NOT NULL DEFAULT 0",
            "priority": "INTEGER NOT NULL DEFAULT 0",
            "upgrade_from_id": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                database.execute(f"ALTER TABLE downloads ADD COLUMN {name} {definition}")
        database.execute(
            "CREATE INDEX IF NOT EXISTS downloads_library_created_idx "
            "ON downloads(status, library_visible, created_at DESC)"
        )
        database.execute(
            "CREATE INDEX IF NOT EXISTS downloads_library_favorite_idx "
            "ON downloads(status, library_visible, favorite)"
        )
        database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS downloads_parent_output_idx "
            "ON downloads(parent_download_id, parent_output_index)"
        )
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                source_platform TEXT,
                external_id TEXT,
                title TEXT NOT NULL,
                uploader TEXT,
                thumbnail TEXT,
                description TEXT,
                total_count INTEGER NOT NULL,
                source_total_count INTEGER,
                favorite INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        playlist_columns = {row["name"] for row in database.execute("PRAGMA table_info(playlists)")}
        playlist_additions = {
            "external_id": "TEXT",
            "description": "TEXT",
            "source_total_count": "INTEGER",
            "favorite": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in playlist_additions.items():
            if name not in playlist_columns:
                database.execute(f"ALTER TABLE playlists ADD COLUMN {name} {definition}")
        database.execute(
            "UPDATE playlists SET source_total_count = total_count WHERE source_total_count IS NULL"
        )
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS download_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                download_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(download_id) REFERENCES downloads(id)
            )
            """
        )
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS video_denoise_jobs (
                id TEXT PRIMARY KEY,
                download_id TEXT NOT NULL,
                preset TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                filter_config TEXT,
                file_path TEXT,
                file_size INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(download_id) REFERENCES downloads(id)
            )
            """
        )
        denoise_columns = {row["name"] for row in database.execute("PRAGMA table_info(video_denoise_jobs)")}
        if "filter_config" not in denoise_columns:
            database.execute("ALTER TABLE video_denoise_jobs ADD COLUMN filter_config TEXT")
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS source_follows (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL UNIQUE,
                source_platform TEXT,
                title TEXT NOT NULL,
                uploader TEXT,
                thumbnail TEXT,
                external_id TEXT,
                check_on_startup INTEGER NOT NULL DEFAULT 1,
                last_checked_at TEXT,
                last_error TEXT,
                entries_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS media_derivative_jobs (
                id TEXT PRIMARY KEY,
                download_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                file_path TEXT,
                file_size INTEGER,
                library_video_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(download_id) REFERENCES downloads(id)
            )
            """
        )
        database.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for key, value in DEFAULT_DOWNLOAD_SETTINGS.items():
            database.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        database.execute(
            """
            UPDATE downloads
            SET
                status = 'interrupted',
                restart_pending = 1,
                speed = NULL,
                eta = NULL,
                error = CASE
                    WHEN source_type IN ('magnet', 'torrent_url', 'torrent_file', 'thunder_bt')
                        AND COALESCE(downloaded_bytes, 0) = 0
                    THEN '未获取文件信息或下载数据。请重新添加磁力链接，待文件列表出现后选择需要的文件再下载。'
                    ELSE error
                END,
                updated_at = ?
            WHERE status IN ('queued', 'running', 'processing')
            """,
            (now(),),
        )
        database.execute(
            "UPDATE video_denoise_jobs SET status = 'interrupted', error = '应用关闭，处理已停止。', updated_at = ? WHERE status IN ('queued', 'running')",
            (now(),),
        )
        database.execute(
            "UPDATE media_derivative_jobs SET status = 'interrupted', error = '应用关闭，处理已停止。', updated_at = ? WHERE status IN ('queued', 'running')",
            (now(),),
        )
