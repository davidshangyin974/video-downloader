from __future__ import annotations

import asyncio
import base64
import html
import heapq
import hashlib
import io
import json
import mimetypes
import os
import queue
import re
import shlex
import shutil
import sqlite3
import string
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from functools import cache
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from yt_dlp import parse_options
from yt_dlp.options import create_parser
from yt_dlp.utils import DownloadError, std_headers

from .core.config import DATA_DIR, DEFAULT_DOWNLOAD_SETTINGS, DENOISE_PRESETS, DOWNLOAD_DIR, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from .core.events import EventBus
from .core.models import (
    Aria2Settings,
    BackupRestoreRequest,
    BatchDownloadRequest,
    DenoiseRequest,
    DownloadRequest,
    DownloadProgressRequest,
    DownloadSettingsRequest,
    DuplicateCleanupRequest,
    EngineSettingsRequest,
    ExternalPlayerRequest,
    GeneralSettingsRequest,
    InspectRequest,
    LocalDirectoryScanRequest,
    IncompleteCleanupRequest,
    LocalMediaBatchRequest,
    LocalVideoRequest,
    MaintenanceCleanupRequest,
    MaintenanceSettings,
    PlaybackProgressRequest,
    PlaylistDownloadRequest,
    QbittorrentSettings,
    ResourceSearchRequest,
    SourceFollowDownloadRequest,
    SourceFollowRequest,
    SourceFollowUpdateRequest,
    SubtitlePreferenceRequest,
    VideoFavoriteRequest,
    VideoMetadataRequest,
    VideoRelinkRequest,
    VideoRenameRequest,
    VideoWatchedRequest,
    VideoUpgradeFinalizeRequest,
    VideoUpgradeRequest,
    YtDlpSettingsRequest,
    YtDlpSimpleSettings,
)
from .core.storage import connection as storage_connection
from .core.storage import initialize_database as storage_initialize_database
from .core.storage import now as storage_now
from .engines import aria2, qbittorrent
from .engines.inputs import (
    InputError,
    MAX_TORRENT_FILE_BYTES,
    MAX_TORRENT_FILES,
    fetch_torrent_url,
    inspect_non_ytdlp_input,
    inspect_torrent,
    is_media_file,
    magnet_info_hash,
    route_input,
)
from .services.engine_tasks import run_aria2_task, run_qbittorrent_task


events = EventBus()
inspect_jobs: dict[str, dict[str, Any]] = {}
inspect_jobs_lock = threading.Lock()
download_creation_lock = threading.Lock()
cancelled_downloads: set[str] = set()
cancelled_downloads_lock = threading.Lock()
engine_processes: dict[str, subprocess.Popen[bytes]] = {}
engine_processes_lock = threading.Lock()
media_job_processes: dict[str, subprocess.Popen[bytes]] = {}
media_job_processes_lock = threading.Lock()
MAX_CONCURRENT_DOWNLOADS = 5
MAX_DOWNLOAD_WORKERS = 10
download_executor = ThreadPoolExecutor(
    max_workers=MAX_DOWNLOAD_WORKERS,
    thread_name_prefix="video-download",
)
download_queue_condition = threading.Condition()
download_queue_entries: list[tuple[int, int, Any, tuple[Any, ...], Future[Any]]] = []
download_queue_sequence = 0
active_scheduled_downloads = 0
download_scheduler_thread: threading.Thread | None = None
INSPECT_HEARTBEAT_SECONDS = 8
MAGNET_METADATA_CACHE_DIR = DATA_DIR / ".video-downloader" / "torrent-metadata"
MAGNET_METADATA_CACHE_URLS = (
    "https://itorrents.org/torrent/{info_hash}.torrent",
)
YT_DLP_UPDATE_CACHE_SECONDS = 3600
yt_dlp_update_cache: dict[str, Any] | None = None
yt_dlp_update_cache_at = 0.0
yt_dlp_update_lock = threading.Lock()
BACKUP_FORMAT = "video-downloader-backup"
BACKUP_FORMAT_VERSION = 1
BACKUP_MAX_BYTES = 25 * 1024 * 1024
BACKUP_PREVIEW_TTL_SECONDS = 600
BACKUP_TABLES = (
    "playlists",
    "downloads",
    "download_logs",
    "video_denoise_jobs",
    "media_derivative_jobs",
    "source_follows",
    "app_settings",
)
backup_previews: dict[str, dict[str, Any]] = {}
backup_previews_lock = threading.Lock()
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
SEARCH_PROVIDER_LABELS = {
    "ytsearch": "YouTube",
    "bilisearch": "Bilibili",
    "scsearch": "SoundCloud",
    "gvsearch": "Google Video",
    "nicosearch": "NicoVideo",
    "nicosearchdate": "NicoVideo（按日期）",
    "yvsearch": "Yahoo Video",
    "rkfnsearch": "Rokfin",
    "prxseries": "PRX Series",
    "prxstories": "PRX Stories",
}
SEARCH_PROVIDER_ORDER = tuple(SEARCH_PROVIDER_LABELS)

# The application object stays in this module while the public entry point is
# intentionally kept in main.py. New engines live in app.engines instead of here.
app = FastAPI(title="Video Downloader API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://tauri.localhost",
        "tauri://localhost",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def connection():
    return storage_connection()


def export_backup_payload() -> dict[str, Any]:
    with connection() as database:
        tables = {
            table: [dict(row) for row in database.execute(f"SELECT * FROM {table}").fetchall()]
            for table in BACKUP_TABLES
        }
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_FORMAT_VERSION,
        "created_at": now(),
        "application": {
            "api_version": app.version,
            "yt_dlp_version": yt_dlp.version.__version__,
        },
        "includes_media_files": False,
        "tables": tables,
    }


def validate_backup_payload(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="备份文件必须是 JSON 对象。")
    if payload.get("format") != BACKUP_FORMAT or payload.get("version") != BACKUP_FORMAT_VERSION:
        raise HTTPException(status_code=422, detail="备份文件格式或版本不受支持。")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise HTTPException(status_code=422, detail="备份文件缺少数据表。")
    unknown_tables = set(tables) - set(BACKUP_TABLES)
    if unknown_tables:
        raise HTTPException(status_code=422, detail=f"备份包含未知数据表：{', '.join(sorted(unknown_tables))}")

    normalized_tables: dict[str, list[dict[str, Any]]] = {}
    with connection() as database:
        columns_by_table = {
            table: {row["name"] for row in database.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in BACKUP_TABLES
        }
    required_keys = {
        "playlists": {"id"},
        "downloads": {"id"},
        "download_logs": {"id", "download_id"},
        "video_denoise_jobs": {"id", "download_id"},
        "media_derivative_jobs": {"id", "download_id"},
        "source_follows": {"id", "source_url"},
        "app_settings": {"key", "value"},
    }
    for table in BACKUP_TABLES:
        rows = tables.get(table, [])
        if not isinstance(rows, list):
            raise HTTPException(status_code=422, detail=f"备份中的 {table} 数据无效。")
        normalized_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise HTTPException(status_code=422, detail=f"备份中的 {table} 第 {index + 1} 行无效。")
            unknown_columns = set(row) - columns_by_table[table]
            if unknown_columns:
                raise HTTPException(status_code=422, detail=f"备份中的 {table} 包含未知字段。")
            if not required_keys[table].issubset(row):
                raise HTTPException(status_code=422, detail=f"备份中的 {table} 缺少必要字段。")
            if any(not isinstance(value, (str, int, float, bool, type(None))) for value in row.values()):
                raise HTTPException(status_code=422, detail=f"备份中的 {table} 包含无效字段值。")
            normalized_rows.append(dict(row))
        normalized_tables[table] = normalized_rows

    missing_files = sum(
        1
        for row in normalized_tables["downloads"]
        if row.get("status") == "completed"
        and row.get("library_visible")
        and row.get("file_path")
        and not Path(str(row["file_path"])).is_file()
    )
    summary = {
        "created_at": payload.get("created_at"),
        "application": payload.get("application") if isinstance(payload.get("application"), dict) else {},
        "counts": {table: len(normalized_tables[table]) for table in BACKUP_TABLES},
        "missing_media_files": missing_files,
        "includes_media_files": False,
    }
    return {**payload, "tables": normalized_tables}, summary


def restore_backup_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tables = payload["tables"]
    with connection() as database:
        active = database.execute(
            "SELECT COUNT(*) AS count FROM downloads WHERE task_deleted = 0 AND status IN ('queued', 'running', 'processing')"
        ).fetchone()["count"]
        if active:
            raise HTTPException(status_code=409, detail="存在进行中的下载任务，请先暂停或取消后再恢复备份。")
        for table in (
            "download_logs",
            "video_denoise_jobs",
            "media_derivative_jobs",
            "source_follows",
            "downloads",
            "playlists",
            "app_settings",
        ):
            database.execute(f"DELETE FROM {table}")
        for table in BACKUP_TABLES:
            for row in tables[table]:
                columns = list(row)
                database.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    [row[column] for column in columns],
                )
        for key, value in DEFAULT_DOWNLOAD_SETTINGS.items():
            database.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
    return {
        "restored_at": now(),
        "counts": {table: len(tables[table]) for table in BACKUP_TABLES},
        "includes_media_files": False,
    }


def platform_name(url: str) -> str:
    host = urlparse(url).hostname or ""
    host = host.lower().removeprefix("www.")
    if host.endswith("youtube.com") or host == "youtu.be":
        return "YouTube"
    if host.endswith("bilibili.com"):
        return "Bilibili"
    if host.endswith("pornhub.com"):
        return "Pornhub"
    return host or "未知平台"


def initialize_database() -> None:
    with connection() as database:
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
                output_files_json TEXT,
                task_deleted INTEGER NOT NULL DEFAULT 0,
                file_origin TEXT NOT NULL DEFAULT 'downloaded',
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
        if "source_platform" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN source_platform TEXT")
        if "download_dir" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN download_dir TEXT")
        if "write_thumbnail" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN write_thumbnail INTEGER")
        if "write_info_json" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN write_info_json INTEGER")
        if "yt_dlp_config" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN yt_dlp_config TEXT")
        if "ffmpeg_config" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN ffmpeg_config TEXT")
        if "playlist_id" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN playlist_id TEXT")
        if "playlist_index" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN playlist_index INTEGER")
        if "output_files_json" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN output_files_json TEXT")
        if "task_deleted" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN task_deleted INTEGER NOT NULL DEFAULT 0")
        if "file_origin" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN file_origin TEXT NOT NULL DEFAULT 'downloaded'")
        if "parent_download_id" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN parent_download_id TEXT")
        if "parent_output_index" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN parent_output_index INTEGER")
        if "restart_pending" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN restart_pending INTEGER NOT NULL DEFAULT 0")
        if "priority" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        if "upgrade_from_id" not in columns:
            database.execute("ALTER TABLE downloads ADD COLUMN upgrade_from_id TEXT")
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
        source_rows = database.execute(
            "SELECT id, source_url FROM downloads WHERE source_platform IS NULL OR source_platform = ''"
        ).fetchall()
        for row in source_rows:
            database.execute(
                "UPDATE downloads SET source_platform = ? WHERE id = ?",
                (platform_name(row["source_url"]), row["id"]),
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
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
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
        for key, value in DEFAULT_DOWNLOAD_SETTINGS.items():
            database.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        database.execute(
            """
            UPDATE downloads
            SET status = 'interrupted', restart_pending = 1, updated_at = ?
            WHERE status IN ('queued', 'running', 'processing')
            """,
            (now(),),
        )
        database.execute(
            """
            UPDATE video_denoise_jobs
            SET status = 'interrupted', error = '应用关闭，处理已停止。', updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            (now(),),
        )
        database.execute(
            """
            UPDATE media_derivative_jobs
            SET status = 'interrupted', error = '应用关闭，处理已停止。', updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            (now(),),
        )


@app.on_event("startup")
def startup() -> None:
    storage_initialize_database()
    recover_restart_pending_downloads()
    threading.Thread(
        target=refresh_local_library_on_startup,
        name="library-startup-scan",
        daemon=True,
    ).start()
    maintenance = get_download_settings()["maintenance"]
    if maintenance["auto_cleanup_enabled"]:
        try:
            run_maintenance_cleanup(MaintenanceCleanupRequest(**maintenance))
        except Exception:
            # Maintenance must never prevent the local download service from starting.
            pass


@app.on_event("shutdown")
def shutdown() -> None:
    timestamp = now()
    with connection() as database:
        qbittorrent_hashes = [
            row["engine_task_id"]
            for row in database.execute(
                "SELECT engine_task_id FROM downloads WHERE engine = 'qbittorrent' AND status IN ('queued', 'running', 'processing') AND engine_task_id IS NOT NULL"
            ).fetchall()
        ]
        database.execute(
            """
            UPDATE downloads
            SET status = 'interrupted', restart_pending = 1, speed = NULL, eta = NULL, updated_at = ?
            WHERE status IN ('queued', 'running', 'processing')
            """,
            (timestamp,),
        )
    if qbittorrent_hashes:
        try:
            qbittorrent_client().pause("|".join(qbittorrent_hashes))
        except qbittorrent.QbittorrentError:
            pass
    with engine_processes_lock:
        processes = list(engine_processes.values())
    for process in processes:
        if process.poll() is None:
            process.terminate()
    with media_job_processes_lock:
        media_processes = list(media_job_processes.values())
    for process in media_processes:
        if process.poll() is None:
            process.terminate()


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="请输入有效的 http 或 https 视频链接。")


def resolve_download_dir(value: str) -> Path:
    directory = Path(value).expanduser()
    if not directory.is_absolute():
        raise HTTPException(status_code=422, detail="下载位置必须填写绝对路径。")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise HTTPException(status_code=422, detail=f"无法创建下载位置：{error}") from error
    return directory.resolve()


def ensure_disk_capacity(
    download_dir: str,
    expected_size: int | None,
    downloaded_bytes: int = 0,
    minimum_free_space_mb: int | None = None,
) -> None:
    directory = resolve_download_dir(download_dir)
    reserve_mb = (
        get_download_settings()["minimum_free_space_mb"]
        if minimum_free_space_mb is None
        else minimum_free_space_mb
    )
    remaining_bytes = max(0, int(expected_size or 0) - max(0, int(downloaded_bytes)))
    required_bytes = remaining_bytes + max(0, int(reserve_mb)) * 1024 * 1024
    free_bytes = shutil.disk_usage(directory).free
    if free_bytes < required_bytes:
        raise HTTPException(
            status_code=409,
            detail=(
                f"下载位置剩余空间不足：还需约 {format_size(required_bytes) or '未知容量'}，"
                f"当前可用 {format_size(free_bytes) or '未知容量'}。"
            ),
        )


DIRECTORY_PATTERN_FIELDS = {"platform", "uploader", "title", "year", "month", "day"}
INVALID_DIRECTORY_NAME_PATTERN = re.compile(r'[<>:"|?*\x00-\x1f]')


def validate_directory_pattern(value: str) -> str:
    pattern = value.strip()
    if not pattern:
        return ""
    if pattern.startswith(("/", "\\")) or "\\" in pattern:
        raise HTTPException(status_code=422, detail="目录格式必须是相对于默认下载位置的路径，并使用 / 分隔。")
    try:
        fields = []
        for _, field_name, format_spec, conversion in string.Formatter().parse(pattern):
            if field_name is not None:
                fields.append(field_name)
                if format_spec or conversion:
                    raise HTTPException(status_code=422, detail="目录变量不支持格式化参数。")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"目录格式中的大括号不完整：{error}") from error
    unknown_fields = sorted(set(fields) - DIRECTORY_PATTERN_FIELDS)
    if unknown_fields:
        raise HTTPException(
            status_code=422,
            detail=f"目录格式包含不支持的变量：{', '.join(f'{{{field}}}' for field in unknown_fields)}",
        )
    if INVALID_DIRECTORY_NAME_PATTERN.search(
        "".join(literal for literal, _, _, _ in string.Formatter().parse(pattern))
    ):
        raise HTTPException(status_code=422, detail='目录格式不能包含 < > : " | ? * 等字符。')
    sample = pattern.format_map({field: "示例" for field in DIRECTORY_PATTERN_FIELDS})
    if any(part in {"", ".", ".."} for part in sample.split("/")):
        raise HTTPException(status_code=422, detail="目录格式不能包含空目录、. 或 ..。")
    return pattern


def normalize_library_dirs(values: list[str]) -> list[str]:
    directories: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        directory = Path(stripped).expanduser()
        if not directory.is_absolute():
            raise HTTPException(status_code=422, detail="媒体目录必须填写绝对路径。")
        resolved = str(directory.resolve())
        if resolved not in directories:
            directories.append(resolved)
    return directories


def safe_directory_value(value: Any, fallback: str) -> str:
    def clean(candidate: Any) -> str:
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(candidate or ""))
        return re.sub(r"\s+", " ", text).strip(" .")

    return (clean(value) or clean(fallback) or "未命名")[:120].rstrip(" .")


def formatted_download_dir(
    base_dir: str,
    directory_pattern: str,
    *,
    platform: Any,
    uploader: Any,
    title: Any,
    created_at: datetime | None = None,
) -> str:
    root = resolve_download_dir(base_dir)
    pattern = validate_directory_pattern(directory_pattern)
    if not pattern:
        return str(root)
    timestamp = created_at or datetime.now().astimezone()
    relative_path = pattern.format_map(
        {
            "platform": safe_directory_value(platform, "未知平台"),
            "uploader": safe_directory_value(uploader, "未知作者"),
            "title": safe_directory_value(title, "未命名内容"),
            "year": timestamp.strftime("%Y"),
            "month": timestamp.strftime("%m"),
            "day": timestamp.strftime("%d"),
        }
    )
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="目录格式生成了默认下载位置之外的路径。") from error
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise HTTPException(status_code=422, detail=f"无法创建格式化下载目录：{error}") from error
    return str(target)


def ytdlp_config_tokens(config: str) -> list[str]:
    tokens: list[str] = []
    for line_number, line in enumerate(config.splitlines(), start=1):
        try:
            tokens.extend(shlex.split(line, comments=True, posix=True))
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=f"yt-dlp 参数第 {line_number} 行无法解析：{error}",
            ) from error
    return tokens


def parse_ytdlp_config(config: str) -> tuple[list[str], dict[str, Any]]:
    tokens = ytdlp_config_tokens(config)
    if not tokens:
        return [], {}

    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            parsed = parse_options(tokens)
    except SystemExit as error:
        message = output.getvalue().strip().splitlines()
        detail = message[-1] if message else "yt-dlp 参数无效。"
        raise HTTPException(status_code=422, detail=detail) from error
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"yt-dlp 参数无效：{error}") from error

    if parsed.urls:
        raise HTTPException(status_code=422, detail="yt-dlp 配置中不要填写视频链接，请在开始下载时粘贴链接。")
    return tokens, dict(parsed.ydl_opts)


YTDLP_SETTINGS_ALLOWED_OPTIONS = {"--proxy", "--cookies-from-browser"}
FFMPEG_SETTINGS_ALLOWED_OPTIONS = {
    "-movflags": {"+faststart"},
    "-c:v": {"libx264"},
    "-crf": {"18", "23", "28"},
    "-preset": {"fast", "medium", "slow"},
    "-c:a": {"aac"},
    "-b:a": {"128k", "192k", "256k"},
    "-ar": {"44100", "48000"},
}


def filtered_ytdlp_settings_config(config: str) -> str:
    lines: list[str] = []
    for line in config.splitlines():
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if len(tokens) == 2 and tokens[0] in YTDLP_SETTINGS_ALLOWED_OPTIONS:
            lines.append(shlex.join(tokens))
    return "\n".join(lines)


def ffmpeg_config_tokens(config: str) -> list[str]:
    tokens: list[str] = []
    for line_number, line in enumerate(config.splitlines(), start=1):
        try:
            line_tokens = shlex.split(line, comments=True, posix=True)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=f"ffmpeg 参数第 {line_number} 行无法解析：{error}",
            ) from error
        for index in range(len(line_tokens) - 1):
            option = line_tokens[index]
            value = line_tokens[index + 1]
            if option in FFMPEG_SETTINGS_ALLOWED_OPTIONS and value in FFMPEG_SETTINGS_ALLOWED_OPTIONS[option]:
                tokens.extend([option, value])
    return tokens


def filtered_ffmpeg_settings_config(config: str) -> str:
    tokens = ffmpeg_config_tokens(config)
    return "\n".join(
        shlex.join(tokens[index:index + 2])
        for index in range(0, len(tokens), 2)
    )


def config_has_option(tokens: list[str], *names: str) -> bool:
    for token in tokens:
        option = token.split("=", 1)[0]
        if option in names:
            return True
        if option.startswith("-P") and "-P" in names:
            return True
        if option.startswith("-o") and "-o" in names:
            return True
    return False


YTDLP_CATALOG_HIDDEN_GROUPS = {
    "General Options",
    "Internet Shortcut Options",
    "Verbosity and Simulation Options",
    "SponsorBlock Options",
}

YTDLP_CATALOG_HIDDEN_FLAGS = {
    "--ap-list-mso",
    "--batch-file",
    "--downloader",
    "--downloader-args",
    "--enable-file-urls",
    "--exec",
    "--list-formats",
    "--list-impersonate-targets",
    "--list-subs",
    "--list-thumbnails",
    "--load-info-json",
    "--no-batch-file",
    "--no-exec",
    "--rm-cache-dir",
    "--use-postprocessor",
}


def ytdlp_option_catalog() -> dict[str, Any]:
    parser = create_parser()
    groups: list[dict[str, Any]] = []
    for group in parser.option_groups:
        if group.title in YTDLP_CATALOG_HIDDEN_GROUPS:
            continue
        options: list[dict[str, Any]] = []
        for option in group.option_list:
            if not option.help or option.help == "SUPPRESSHELP":
                continue
            flags = [*option._short_opts, *option._long_opts]
            if not flags or any(flag in YTDLP_CATALOG_HIDDEN_FLAGS for flag in flags):
                continue
            options.append(
                {
                    "flags": flags,
                    "metavar": option.metavar,
                    "help": option.help,
                    "help_zh": ytdlp_option_help_zh(flags),
                    "detail_zh": ytdlp_option_detail_zh(flags),
                    "example": ytdlp_option_example(flags, option.metavar),
                    "action": option.action,
                    "type": option.type,
                    "choices": option.choices,
                }
            )
        if options:
            groups.append({"title": group.title, "options": options})
    return {"version": yt_dlp.version.__version__, "groups": groups}


YTDLP_OPTION_HELP_ZH = {
    "--write-thumbnail": "保存封面图片文件。",
    "--no-write-thumbnail": "不保存封面图片文件。",
    "--write-all-thumbnails": "保存全部可用封面图片。",
    "--list-thumbnails": "列出可用封面图片，不下载视频。",
    "--write-subs": "保存字幕文件。",
    "--no-write-subs": "不保存字幕文件。",
    "--write-auto-subs": "保存自动生成的字幕文件。",
    "--no-write-auto-subs": "不保存自动生成的字幕文件。",
    "--sub-langs": "选择要保存的字幕语言。",
    "--sub-format": "设置优先下载的字幕格式。",
    "--format": "选择下载的视频和音频格式。",
    "--format-sort": "设置视频格式的优先级排序。",
    "--paths": "设置下载文件的保存目录。",
    "--output": "设置下载文件的命名模板。",
    "--write-info-json": "保存媒体信息 JSON 文件。",
    "--no-write-info-json": "不保存媒体信息 JSON 文件。",
    "--concurrent-fragments": "设置同时下载的分片数量。",
    "--limit-rate": "限制下载速度。",
    "--retries": "设置下载失败后的重试次数。",
    "--proxy": "通过代理服务器连接。",
    "--socket-timeout": "设置网络连接超时时间。",
    "--extract-audio": "仅保留音频，并在下载后提取音频文件。",
    "--audio-format": "设置提取后音频的输出格式。",
    "--audio-quality": "设置提取后音频的质量。",
    "--remux-video": "将视频重新封装为指定格式。",
    "--recode-video": "将视频重新编码为指定格式。",
    "--postprocessor-args": "向后处理工具传递额外参数。",
    "--embed-thumbnail": "将封面嵌入媒体文件。",
    "--embed-subs": "将字幕嵌入媒体文件。",
    "--add-metadata": "将标题等媒体信息写入文件。",
    "--embed-chapters": "将章节信息嵌入媒体文件。",
    "--cookies": "使用 cookies 文件访问需要登录的内容。",
    "--cookies-from-browser": "从本机浏览器读取 cookies。",
    "--username": "设置登录用户名。",
    "--password": "设置登录密码。",
    "--ffmpeg-location": "设置 ffmpeg 程序所在位置。",
    "--write-link": "保存指向原视频页面的系统快捷方式。",
    "--write-url-link": "保存 Windows 的 .url 网页快捷方式。",
    "--write-webloc-link": "保存 macOS 的 .webloc 网页快捷方式。",
    "--write-desktop-link": "保存 Linux 的 .desktop 网页快捷方式。",
}

YTDLP_OPTION_DETAIL_ZH = {
    "--write-link": "会在下载目录创建一个指向原视频网页的快捷方式，并按当前系统自动选择 .url、.webloc 或 .desktop 格式。它不是视频文件，也不能离线观看；只用于以后快速打开原页面。普通下载通常不需要开启。",
    "--write-url-link": "仅适合 Windows。会生成一个 .url 文件，双击后会在默认浏览器打开原视频页面。不会下载更多视频内容；如果只是保存本地视频，一般不需要开启。",
    "--write-webloc-link": "仅适合 macOS。会生成一个 .webloc 文件，双击后会在默认浏览器打开原视频页面。它只是书签，不是视频或封面文件；通常可以保持关闭。",
    "--write-desktop-link": "仅适合 Linux。会生成一个 .desktop 网页快捷方式，双击后会打开原视频页面。它不是桌面应用，也不包含下载的视频；普通用户通常不需要开启。",
}

YTDLP_OPTION_EXAMPLES = {
    "--paths": "--paths /Users/你的用户名/Movies",
    "--output": "--output '%(title)s.%(ext)s'",
    "--batch-file": "--batch-file /Users/你的用户名/videos.txt",
    "--concurrent-fragments": "--concurrent-fragments 4",
    "--retries": "--retries 10",
    "--limit-rate": "--limit-rate 5M",
    "--proxy": "--proxy http://127.0.0.1:7890",
    "--socket-timeout": "--socket-timeout 30",
    "--format": "--format 'bv*+ba/b'",
    "--format-sort": "--format-sort res:1080,fps",
    "--write-subs": "--write-subs",
    "--write-auto-subs": "--write-auto-subs",
    "--sub-langs": "--sub-langs zh.*,en",
    "--sub-format": "--sub-format srt/vtt/best",
    "--extract-audio": "--extract-audio",
    "--audio-format": "--audio-format mp3",
    "--audio-quality": "--audio-quality 192K",
    "--remux-video": "--remux-video mp4",
    "--recode-video": "--recode-video mp4",
    "--cookies": "--cookies /Users/你的用户名/cookies.txt",
    "--cookies-from-browser": "--cookies-from-browser chrome",
    "--username": "--username your-account",
    "--password": "--password your-password",
    "--ffmpeg-location": "--ffmpeg-location /opt/homebrew/bin",
}


def ytdlp_option_help_zh(flags: list[str]) -> str:
    long_flag = next((flag for flag in flags if flag.startswith("--")), flags[0])
    if long_flag in YTDLP_OPTION_HELP_ZH:
        return YTDLP_OPTION_HELP_ZH[long_flag]
    if long_flag.startswith("--no-"):
        return f"关闭 {long_flag[5:].replace('-', ' ')} 相关功能。"
    if long_flag.startswith("--write-"):
        return f"保存 {long_flag[8:].replace('-', ' ')} 相关文件。"
    return f"按需要启用 {long_flag}；不确定用途时可以不填，默认下载不受影响。"


def ytdlp_option_detail_zh(flags: list[str]) -> str:
    long_flag = next((flag for flag in flags if flag.startswith("--")), flags[0])
    if long_flag in YTDLP_OPTION_DETAIL_ZH:
        return YTDLP_OPTION_DETAIL_ZH[long_flag]
    return "这是 yt-dlp 的原生高级选项。确认它符合你的下载需求后再填入；不确定时保持不填，应用会使用默认下载方式。"


def ytdlp_option_example(flags: list[str], metavar: str | None) -> str:
    long_flag = next((flag for flag in flags if flag.startswith("--")), flags[0])
    if long_flag in YTDLP_OPTION_EXAMPLES:
        return YTDLP_OPTION_EXAMPLES[long_flag]
    if not metavar:
        return long_flag
    upper_metavar = metavar.upper()
    if "PATH" in upper_metavar or "FILE" in upper_metavar or "DIR" in upper_metavar:
        return f"{long_flag} /Users/你的用户名/示例文件"
    if "URL" in upper_metavar:
        return f"{long_flag} https://example.com"
    if "NUMBER" in upper_metavar or upper_metavar in {"N", "NUM"}:
        return f"{long_flag} 4"
    return f"{long_flag} 示例值"


def combine_ytdlp_config(global_config: str, task_config: str | None) -> str:
    return "\n".join(part.strip() for part in (global_config, task_config or "") if part.strip())


def ytdlp_download_part_count(format_selector: str) -> int:
    alternatives = [part.strip() for part in format_selector.split("/") if part.strip()]
    if not alternatives:
        return 1
    return min(8, max(part.count("+") + 1 for part in alternatives))


def ytdlp_simple_config(settings: dict[str, Any]) -> str:
    simple = YtDlpSimpleSettings.model_validate(settings)
    lines: list[str] = []
    if simple.concurrent_fragments is not None:
        lines.append(f"--concurrent-fragments {simple.concurrent_fragments}")
    if simple.retries is not None:
        lines.append(f"--retries {simple.retries}")
    if simple.write_subs:
        lines.append("--write-subs")
    if simple.write_auto_subs:
        lines.append("--write-auto-subs")
    if (simple.write_subs or simple.write_auto_subs) and simple.sub_langs.strip():
        lines.append(f"--sub-langs {shlex.quote(simple.sub_langs.strip())}")
    if simple.extract_audio:
        lines.append("--extract-audio")
        if simple.audio_format:
            lines.append(f"--audio-format {simple.audio_format}")
    return "\n".join(lines)


def build_ytdlp_download_options(
    config: str,
    requested_format: str | None,
    download_dir: str,
    write_thumbnail: bool,
    write_info_json: bool,
    ffmpeg_config: str,
    progress_hook: Any,
    logger: Any,
) -> dict[str, Any]:
    tokens, options = parse_ytdlp_config(config)

    if config_has_option(tokens, "-x", "--extract-audio"):
        # yt-dlp selects bestaudio/best for audio extraction. Do not replace it
        # with the video format currently selected in the download dialog.
        pass
    elif requested_format:
        options["format"] = requested_format
    elif not options.get("format"):
        options["format"] = "bv*+ba/b"

    if not config_has_option(tokens, "-o", "--output", "-P", "--paths"):
        options["outtmpl"] = str(Path(download_dir) / "%(title).180B [%(id)s].%(ext)s")
    if not config_has_option(tokens, "--yes-playlist", "--no-playlist"):
        options["noplaylist"] = True
    if not config_has_option(tokens, "-c", "--continue", "--no-continue"):
        options["continuedl"] = True
    if not config_has_option(tokens, "--merge-output-format"):
        options["merge_output_format"] = "mp4"
    if not config_has_option(tokens, "--write-thumbnail", "--no-write-thumbnail", "--write-all-thumbnails"):
        options["writethumbnail"] = write_thumbnail
    if not config_has_option(tokens, "--write-info-json", "--no-write-info-json"):
        options["writeinfojson"] = write_info_json

    ffmpeg_arguments = ffmpeg_config_tokens(ffmpeg_config)
    if ffmpeg_arguments:
        postprocessor_args = dict(options.get("postprocessor_args") or {})
        postprocessor_args["ffmpeg"] = [
            *(postprocessor_args.get("ffmpeg") or []),
            *ffmpeg_arguments,
        ]
        options["postprocessor_args"] = postprocessor_args

    options["progress_hooks"] = [*options.get("progress_hooks", []), progress_hook]
    options["logger"] = logger
    return options


def get_download_settings() -> dict[str, Any]:
    with connection() as database:
        rows = database.execute("SELECT key, value FROM app_settings").fetchall()

    values = {row["key"]: json.loads(row["value"]) for row in rows}
    return {
        "download_dir": str(resolve_download_dir(values.get("download_dir", str(DOWNLOAD_DIR)))),
        "directory_pattern": validate_directory_pattern(
            str(values.get("directory_pattern", DEFAULT_DOWNLOAD_SETTINGS["directory_pattern"]))
        ),
        "library_dirs": normalize_library_dirs(
            values.get("library_dirs", DEFAULT_DOWNLOAD_SETTINGS["library_dirs"])
        ),
        "scan_library_on_startup": bool(
            values.get("scan_library_on_startup", DEFAULT_DOWNLOAD_SETTINGS["scan_library_on_startup"])
        ),
        "last_library_scan": values.get("last_library_scan"),
        "write_thumbnail": bool(values.get("write_thumbnail", True)),
        "write_info_json": bool(values.get("write_info_json", True)),
        "max_concurrent_downloads": max(1, min(10, int(values.get("max_concurrent_downloads", 5)))),
        "download_rate_limit_kbps": max(0, int(values.get("download_rate_limit_kbps", 0))),
        "minimum_free_space_mb": max(0, int(values.get("minimum_free_space_mb", 1024))),
        "system_notifications": bool(values.get("system_notifications", False)),
        "maintenance": MaintenanceSettings.model_validate(
            values.get("maintenance", DEFAULT_DOWNLOAD_SETTINGS["maintenance"])
        ).model_dump(),
        "yt_dlp_config": filtered_ytdlp_settings_config(str(values.get("yt_dlp_config", ""))),
        "yt_dlp_simple": YtDlpSimpleSettings.model_validate(
            values.get("yt_dlp_simple", DEFAULT_DOWNLOAD_SETTINGS["yt_dlp_simple"])
        ).model_dump(),
        "ffmpeg_config": filtered_ffmpeg_settings_config(str(values.get("ffmpeg_config", ""))),
        "aria2": Aria2Settings.model_validate(
            values.get("aria2", DEFAULT_DOWNLOAD_SETTINGS["aria2"])
        ).model_dump(),
        "qbittorrent": QbittorrentSettings.model_validate(
            values.get("qbittorrent", DEFAULT_DOWNLOAD_SETTINGS["qbittorrent"])
        ).model_dump(),
    }


def save_download_settings(request: DownloadSettingsRequest) -> dict[str, Any]:
    parse_ytdlp_config(request.yt_dlp_config)
    ffmpeg_config_tokens(request.ffmpeg_config)
    settings = {
        "download_dir": str(resolve_download_dir(request.download_dir)),
        "directory_pattern": validate_directory_pattern(request.directory_pattern),
        "library_dirs": normalize_library_dirs(request.library_dirs),
        "scan_library_on_startup": request.scan_library_on_startup,
        "write_thumbnail": request.write_thumbnail,
        "write_info_json": request.write_info_json,
        "max_concurrent_downloads": request.max_concurrent_downloads,
        "download_rate_limit_kbps": request.download_rate_limit_kbps,
        "minimum_free_space_mb": request.minimum_free_space_mb,
        "system_notifications": request.system_notifications,
        "yt_dlp_config": filtered_ytdlp_settings_config(request.yt_dlp_config),
        "ffmpeg_config": filtered_ffmpeg_settings_config(request.ffmpeg_config),
    }
    with connection() as database:
        for key, value in settings.items():
            database.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, json.dumps(value)),
            )
    return settings


def save_settings_values(values: dict[str, Any]) -> dict[str, Any]:
    with connection() as database:
        for key, value in values.items():
            database.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, json.dumps(value)),
            )
    return get_download_settings()


def save_general_settings(request: GeneralSettingsRequest) -> dict[str, Any]:
    settings = save_settings_values(
        {
            "download_dir": str(resolve_download_dir(request.download_dir)),
            "directory_pattern": validate_directory_pattern(request.directory_pattern),
            "library_dirs": normalize_library_dirs(request.library_dirs),
            "scan_library_on_startup": request.scan_library_on_startup,
            "write_thumbnail": request.write_thumbnail,
            "write_info_json": request.write_info_json,
            "max_concurrent_downloads": request.max_concurrent_downloads,
            "download_rate_limit_kbps": request.download_rate_limit_kbps,
            "minimum_free_space_mb": request.minimum_free_space_mb,
            "system_notifications": request.system_notifications,
        }
    )
    notify_download_scheduler()
    return settings


def save_ytdlp_settings(request: YtDlpSettingsRequest) -> dict[str, Any]:
    parse_ytdlp_config(request.config)
    if request.simple.audio_format and request.simple.audio_format not in {"mp3", "m4a", "opus", "wav", "flac"}:
        raise HTTPException(status_code=422, detail="请选择支持的音频格式。")
    return save_settings_values(
        {
            "yt_dlp_config": filtered_ytdlp_settings_config(request.config),
            "yt_dlp_simple": request.simple.model_dump(),
        }
    )


def save_maintenance_settings(request: MaintenanceSettings) -> dict[str, Any]:
    return save_settings_values({"maintenance": request.model_dump()})


def save_ffmpeg_settings(request: EngineSettingsRequest) -> dict[str, Any]:
    ffmpeg_config_tokens(request.config)
    return save_settings_values({"ffmpeg_config": filtered_ffmpeg_settings_config(request.config)})


def save_aria2_settings(request: Aria2Settings) -> dict[str, Any]:
    return save_settings_values({"aria2": request.model_dump()})


def qbittorrent_client(settings: dict[str, Any] | None = None, timeout: float = 8) -> qbittorrent.Client:
    values = settings or get_download_settings()["qbittorrent"]
    return qbittorrent.Client(
        str(values.get("base_url", "")),
        str(values.get("username", "")),
        str(values.get("password", "")),
        timeout,
    )


def save_qbittorrent_settings(request: QbittorrentSettings) -> dict[str, Any]:
    values = request.model_dump()
    client = qbittorrent_client(values)
    if request.enabled:
        client.app_version()
    return save_settings_values({"qbittorrent": values})


def format_size(value: int | None) -> str | None:
    if value is None:
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return None


def format_label(item: dict[str, Any]) -> str:
    resolution = item.get("resolution")
    if not resolution and item.get("height"):
        resolution = f"{item['height']}p"
    if not resolution:
        resolution = "原始清晰度"
    extension = item.get("ext", "未知格式").upper()
    return f"{resolution} · {extension}"


def format_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    # yt-dlp returns formats from lower to higher preference. The first option
    # is selected by default in the UI, so expose the best choices first.
    for item in reversed(info.get("formats") or []):
        if not item.get("format_id") or item.get("vcodec") == "none":
            continue

        format_id = item["format_id"]
        if item.get("acodec") == "none":
            format_id = f"{format_id}+bestaudio/best"

        options.append(
            {
                "format_id": format_id,
                "label": format_label(item),
                "resolution": item.get("resolution") or (
                    f"{item['height']}p" if item.get("height") else None
                ),
                "extension": item.get("ext"),
                "file_size": item.get("filesize") or item.get("filesize_approx"),
                "file_size_label": format_size(
                    item.get("filesize") or item.get("filesize_approx")
                ),
                "fps": item.get("fps"),
            }
        )
    return options


def thumbnail_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme == "http" and (parsed.hostname or "").endswith("hdslb.com"):
        return f"https://{value.removeprefix('http://')}"
    return value


def search_result_thumbnail(info: dict[str, Any]) -> str | None:
    thumbnail = thumbnail_url(info.get("thumbnail"))
    if thumbnail:
        return thumbnail
    thumbnails = info.get("thumbnails")
    if not isinstance(thumbnails, list):
        return None
    for item in reversed(thumbnails):
        if not isinstance(item, dict):
            continue
        thumbnail = thumbnail_url(item.get("url"))
        if thumbnail:
            return thumbnail
    return None


def bilibili_search_duration(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    duration = 0
    for part in parts:
        duration = duration * 60 + int(part)
    return duration


def bilibili_resource_search(
    request: ResourceSearchRequest,
    provider: dict[str, str],
) -> dict[str, Any]:
    parameters = urlencode(
        {
            "Search_key": request.query,
            "keyword": request.query,
            "page": 1,
            "search_type": "video",
            "__refresh__": "true",
        }
    )
    api_request = Request(
        f"https://api.bilibili.com/x/web-interface/search/type?{parameters}",
        headers={
            **std_headers,
            "Referer": "https://search.bilibili.com/",
            "Cookie": f"buvid3={uuid.uuid4()}infoc",
        },
    )
    try:
        with build_opener().open(api_request, timeout=15) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=502,
            detail="Bilibili 搜索暂时不可用，请稍后重试。",
        ) from error

    data = payload.get("data")
    if payload.get("code") != 0 or not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail="Bilibili 搜索没有返回有效结果，请稍后重试。",
        )
    videos = data.get("result", [])
    results: list[dict[str, Any]] = []
    for index, video in enumerate(videos[: request.limit], start=1):
        if not isinstance(video, dict):
            continue
        webpage_url = video.get("arcurl")
        if not isinstance(webpage_url, str) or not webpage_url.startswith(("http://", "https://")):
            continue
        thumbnail = video.get("pic")
        if isinstance(thumbnail, str) and thumbnail.startswith("//"):
            thumbnail = f"https:{thumbnail}"
        published_at = video.get("pubdate")
        upload_date = (
            datetime.fromtimestamp(published_at, UTC).strftime("%Y%m%d")
            if isinstance(published_at, (int, float))
            else None
        )
        results.append(
            {
                "id": f"bilisearch:{video.get('bvid') or video.get('aid') or index}",
                "title": html.unescape(re.sub(r"<[^>]+>", "", str(video.get("title") or f"搜索结果 {index}"))),
                "uploader": video.get("author"),
                "thumbnail": thumbnail if isinstance(thumbnail, str) else None,
                "duration": bilibili_search_duration(video.get("duration")),
                "upload_date": upload_date,
                "view_count": video.get("play") if isinstance(video.get("play"), int) else None,
                "webpage_url": webpage_url,
                "provider": request.provider,
                "provider_label": provider["label"],
            }
        )
    return {
        "provider": request.provider,
        "provider_label": provider["label"],
        "query": request.query,
        "results": results,
    }


@cache
def resource_search_providers() -> list[dict[str, str]]:
    from yt_dlp.extractor import gen_extractor_classes

    discovered: dict[str, str] = {}
    for extractor in gen_extractor_classes():
        search_key = getattr(extractor, "SEARCH_KEY", None)
        if not isinstance(search_key, str) or not search_key:
            continue
        discovered[search_key] = SEARCH_PROVIDER_LABELS.get(
            search_key,
            str(getattr(extractor, "IE_NAME", search_key)),
        )

    ordered_keys = [key for key in SEARCH_PROVIDER_ORDER if key in discovered]
    ordered_keys.extend(sorted(key for key in discovered if key not in SEARCH_PROVIDER_ORDER))
    return [{"key": key, "label": discovered[key]} for key in ordered_keys]


def resource_search(request: ResourceSearchRequest) -> dict[str, Any]:
    providers = {provider["key"]: provider for provider in resource_search_providers()}
    provider = providers.get(request.provider)
    if provider is None:
        raise HTTPException(status_code=422, detail="所选平台不支持关键词搜索。")

    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="请输入搜索关键词。")
    request.query = query

    if request.provider == "bilisearch":
        return bilibili_resource_search(request, provider)

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": request.limit,
    }
    search_url = f"{request.provider}{request.limit}:{query}"
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(search_url, download=False)
    except DownloadError as error:
        raise HTTPException(
            status_code=502,
            detail=f"{provider['label']} 搜索暂时不可用，请稍后重试。",
        ) from error

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    entries = info.get("entries") if isinstance(info, dict) else None
    for index, entry in enumerate(entries or [], start=1):
        if not isinstance(entry, dict):
            continue
        webpage_url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
        if (
            not isinstance(webpage_url, str)
            or not webpage_url.startswith(("http://", "https://"))
            or webpage_url in seen_urls
        ):
            continue
        seen_urls.add(webpage_url)
        results.append(
            {
                "id": f"{request.provider}:{entry.get('id') or index}",
                "title": entry.get("title") or f"搜索结果 {index}",
                "uploader": entry.get("uploader") or entry.get("channel"),
                "thumbnail": search_result_thumbnail(entry),
                "duration": entry.get("duration"),
                "upload_date": entry.get("upload_date"),
                "view_count": entry.get("view_count"),
                "webpage_url": webpage_url,
                "provider": request.provider,
                "provider_label": provider["label"],
            }
        )

    return {
        "provider": request.provider,
        "provider_label": provider["label"],
        "query": query,
        "results": results,
    }


def media_summary(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": info.get("title") or "未命名视频",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": thumbnail_url(info.get("thumbnail")),
        "webpage_url": info.get("webpage_url") or info.get("original_url"),
        "video_id": info.get("id"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "resolution": info.get("resolution")
        or (f"{info['height']}p" if info.get("height") else None),
        "formats": format_options(info),
    }


def playlist_entry_summary(info: dict[str, Any], index: int) -> dict[str, Any] | None:
    webpage_url = info.get("webpage_url") or info.get("original_url") or info.get("url")
    if not isinstance(webpage_url, str) or not webpage_url.startswith(("http://", "https://")):
        return None
    return {
        "title": info.get("title") or f"第 {index} 个视频",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": thumbnail_url(info.get("thumbnail")),
        "duration": info.get("duration"),
        "webpage_url": webpage_url,
        "playlist_index": info.get("playlist_index") or index,
    }


def playlist_summary(info: dict[str, Any], source_url: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, entry in enumerate(info.get("entries") or [], start=1):
        if not isinstance(entry, dict):
            continue
        summary = playlist_entry_summary(entry, index)
        if summary is None or summary["webpage_url"] in seen_urls:
            continue
        seen_urls.add(summary["webpage_url"])
        entries.append(summary)
    if not entries:
        raise DownloadError("播放列表中没有可下载的视频。")
    return {
        "kind": "playlist",
        "external_id": info.get("id"),
        "title": info.get("title") or "未命名播放列表",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": thumbnail_url(info.get("thumbnail")) or entries[0].get("thumbnail"),
        "description": info.get("description"),
        "source_url": source_url,
        "source_platform": platform_name(source_url),
        "entry_count": len(entries),
        "source_total_count": info.get("playlist_count") or len(entries),
        "entries": entries,
    }


class InspectLogger:
    def __init__(self, inspect_id: str) -> None:
        self.inspect_id = inspect_id

    def debug(self, message: str) -> None:
        clean_message = inspect_log_message(message)
        if clean_message and not clean_message.startswith("[debug] "):
            append_inspect_log(self.inspect_id, "info", clean_message)

    def warning(self, message: str) -> None:
        append_inspect_log(self.inspect_id, "warning", message)

    def error(self, message: str) -> None:
        # yt-dlp will raise a DownloadError immediately after this callback.
        # Let run_inspect turn it into one concise, actionable Chinese message.
        return


def inspect_log_message(message: str) -> str:
    clean_message = normalize_message(message)
    lower_message = clean_message.lower()
    if "redownloading playlist api json" in lower_message:
        return "正在核对播放列表中的可用视频。"
    if "downloading playlist" in lower_message:
        return "正在读取播放列表信息。"
    if "extracting videos in" in lower_message:
        return "正在整理播放列表中的视频。"
    if "downloading webpage" in lower_message:
        return "正在连接视频网站，读取页面信息。"
    if "extracting url:" in lower_message:
        return "正在定位视频或播放列表地址。"
    return clean_message


def inspect_url(url: str, logger: InspectLogger | None = None) -> dict[str, Any]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
    }
    parsed_url = urlparse(url)
    is_bilibili_bangumi_media = (
        (parsed_url.hostname or "") == "bilibili.com"
        or (parsed_url.hostname or "").endswith(".bilibili.com")
    ) and parsed_url.path.startswith("/bangumi/media/")
    is_bilibili_space_collection = (
        (parsed_url.hostname or "").lower() == "space.bilibili.com"
        and re.fullmatch(
            r"/\d+/(?:lists/\d+|channel/collectiondetail)/?",
            parsed_url.path,
        )
        is not None
    )
    if not is_bilibili_bangumi_media and not is_bilibili_space_collection:
        # Most playlists provide titles in flat mode. Bilibili bangumi media
        # and space collection pages only provide entry URLs, so those pages
        # must be fully expanded to obtain each video's title and thumbnail.
        options["extract_flat"] = "in_playlist"
    if logger is not None:
        options["logger"] = logger
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)

    if not info:
        raise DownloadError("未找到可下载的媒体。")

    if info.get("_type") in {"playlist", "multi_video"}:
        return playlist_summary(info, url)

    formats = info.get("formats") if isinstance(info.get("formats"), list) else []
    media_formats = [info, *[item for item in formats if isinstance(item, dict)]]
    if not any(
        item.get("vcodec") not in {None, "none"} or item.get("acodec") not in {None, "none"}
        or (
            str(item.get("protocol") or "").startswith("m3u8")
            and is_media_file(f"stream.{str(item.get('ext') or '').lstrip('.')}")
        )
        for item in media_formats
    ):
        raise InputError("只支持下载视频和音频内容，其他文件类型不允许下载。")

    return {"kind": "video", **media_summary(info)}


def inspect_job_record(inspect_id: str) -> dict[str, Any]:
    with inspect_jobs_lock:
        job = inspect_jobs.get(inspect_id)
        if job is None:
            raise HTTPException(status_code=404, detail="解析记录不存在。")
        return {
            "id": job["id"],
            "status": job["status"],
            "logs": list(job["logs"]),
            "media": job["media"],
            "error": job["error"],
            "started_at": job["started_at"],
            "last_activity_at": job["last_activity_at"],
        }


def friendly_inspect_error(error: Exception) -> str:
    message = str(error).lower()
    if "unsupported url" in message:
        return "暂不支持这个网站或链接格式。请粘贴视频详情页链接。"
    if "private video" in message:
        return "该视频是私密内容，无法下载。"
    if "sign in" in message or "login" in message or "cookies" in message:
        return "该视频需要登录后才能访问，请配置浏览器登录信息后再试。"
    if "403" in message or "forbidden" in message:
        return "当前没有访问权限，视频可能需要登录或受地区限制。"
    if any(text in message for text in ("timed out", "network is unreachable", "name or service not known", "temporary failure in name resolution", "connection refused")):
        return "无法连接到该视频网站。请检查网络连接后重试。"
    if "ssl" in message or "unable to download webpage" in message:
        return "无法连接到该视频网站。请检查网络或代理设置后重试。"
    if any(text in message for text in ("not available", "unavailable", "has been removed", "not found")):
        return "该视频无法访问，可能已删除、设为私密或链接已失效。"
    return "该链接暂时无法解析。请确认它是可公开访问的视频详情页后重试。"


def append_inspect_log(inspect_id: str, level: str, message: str) -> None:
    normalized_message = normalize_message(message)[:1000]
    if not normalized_message:
        return
    with inspect_jobs_lock:
        job = inspect_jobs.get(inspect_id)
        if job is None:
            return
        job["logs"].append(
            {
                "id": len(job["logs"]) + 1,
                "level": level,
                "message": normalized_message,
                "created_at": now(),
            }
        )
        job["last_activity_at"] = now()


def prepare_inspect_source(request: InspectRequest) -> dict[str, Any]:
    has_torrent_file = bool(request.torrent_base64)
    if has_torrent_file and request.url and request.url.strip():
        raise HTTPException(status_code=422, detail="一次只能解析一个链接或一个 .torrent 文件。")
    if has_torrent_file:
        try:
            torrent_data = base64.b64decode(request.torrent_base64 or "", validate=True)
        except Exception as error:
            raise HTTPException(status_code=422, detail="种子文件读取失败，请重新选择 .torrent 文件。") from error
        if not torrent_data:
            raise HTTPException(status_code=422, detail="种子文件为空，请重新选择文件。")
        try:
            route = route_input("", request.engine_hint, has_torrent_file=True)
        except InputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if request.engine_hint == "auto" and get_download_settings()["qbittorrent"]["enabled"]:
            route["engine"] = "qbittorrent"
        return {"url": None, "route": route, "torrent_data": torrent_data, "torrent_name": request.torrent_name}
    if not request.url or not request.url.strip():
        raise HTTPException(status_code=422, detail="请输入下载链接或选择 .torrent 文件。")
    try:
        route = route_input(request.url, request.engine_hint)
    except InputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if (
        request.engine_hint == "auto"
        and route["source_type"] in {"magnet", "torrent_url", "thunder_bt"}
        and get_download_settings()["qbittorrent"]["enabled"]
    ):
        route["engine"] = "qbittorrent"
    return {"url": request.url.strip(), "route": route, "torrent_data": None, "torrent_name": None}


def cached_magnet_metadata(magnet_url: str) -> bytes | None:
    info_hash = magnet_info_hash(magnet_url)
    if info_hash is None:
        return None
    cache_path = MAGNET_METADATA_CACHE_DIR / f"{info_hash}.torrent"
    try:
        if not cache_path.is_file() or cache_path.stat().st_size > MAX_TORRENT_FILE_BYTES:
            return None
        torrent_data = cache_path.read_bytes()
        inspected = inspect_torrent(torrent_data, cache_path.name)
    except (InputError, OSError):
        return None
    return torrent_data if inspected.get("info_hash") == info_hash else None


def cache_magnet_metadata(magnet_url: str, torrent_data: bytes) -> None:
    info_hash = magnet_info_hash(magnet_url)
    if info_hash is None or len(torrent_data) > MAX_TORRENT_FILE_BYTES:
        raise InputError("磁力链接返回了无法校验的 BT 元数据。")
    inspected = inspect_torrent(torrent_data, f"{info_hash}.torrent")
    if inspected.get("info_hash") != info_hash:
        raise InputError("磁力链接返回的 BT 元数据与 Info Hash 不一致，已停止解析。")
    MAGNET_METADATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = MAGNET_METADATA_CACHE_DIR / f"{info_hash}.torrent"
    temporary = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(torrent_data)
        temporary.replace(cache_path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_public_magnet_metadata(magnet_url: str) -> bytes | None:
    """Fetch allowlisted public metadata and accept it only after hash validation."""
    info_hash = magnet_info_hash(magnet_url)
    if info_hash is None:
        return None
    for template in MAGNET_METADATA_CACHE_URLS:
        try:
            torrent_data, _name = fetch_torrent_url(template.format(info_hash=info_hash.upper()))
            inspected = inspect_torrent(torrent_data, f"{info_hash}.torrent")
        except InputError:
            continue
        if inspected.get("info_hash") == info_hash:
            return torrent_data
    return None


def inspect_source(source: dict[str, Any], logger: InspectLogger | None = None) -> dict[str, Any]:
    route = source["route"]
    if route["engine"] == "yt-dlp":
        return inspect_url(route["resolved_url"], logger)
    if route["engine"] == "qbittorrent" and route["source_type"] in {"magnet", "torrent_url", "thunder_bt"}:
        settings = get_download_settings()["qbittorrent"]
        if not settings["enabled"]:
            raise qbittorrent.QbittorrentError("请先在设置中启用并连接 qBittorrent。")
        client = qbittorrent_client(settings)
        tag = f"video-downloader-inspect-{uuid.uuid4()}"
        inspect_dir = DATA_DIR / ".qbittorrent-inspect" / tag
        inspect_dir.mkdir(parents=True, exist_ok=True)
        torrent_hash = ""
        try:
            if logger is not None:
                logger.debug("正在通过 qBittorrent 获取 BT 文件列表。")
            added_hashes = client.add(route["resolved_url"], None, str(inspect_dir), tag)
            deadline = time.monotonic() + int(settings["bt_stall_timeout"])
            tasks: list[dict[str, Any]] = []
            files: list[dict[str, Any]] = []
            while time.monotonic() < deadline:
                tasks = client.info(hashes="|".join(added_hashes)) if added_hashes else client.info(tag=tag)
                if tasks:
                    torrent_hash = str(tasks[0].get("hash") or "")
                    if torrent_hash:
                        client.resume(torrent_hash)
                        files = client.files(torrent_hash)
                        if files:
                            break
                time.sleep(0.5)
            if not tasks or not torrent_hash or not files:
                raise qbittorrent.QbittorrentError(
                    f"qBittorrent 在 {settings['bt_stall_timeout']} 秒内没有取得 BT 文件列表。"
                )
            task = tasks[0]
            inspected_files = [
                {
                    "index": int(item.get("index", index)),
                    "path": str(item.get("name") or ""),
                    "size": int(item.get("size") or 0) or None,
                }
                for index, item in enumerate(files)
            ]
            if len(inspected_files) > MAX_TORRENT_FILES:
                raise qbittorrent.QbittorrentError(
                    f"种子包含超过 {MAX_TORRENT_FILES} 个文件，暂不支持读取。"
                )
            inspected = {
                "kind": "torrent",
                "title": str(task.get("name") or "BT 下载任务"),
                "engine": "qbittorrent",
                "source_type": route["source_type"],
                "info_hash": torrent_hash.lower(),
                "file_count": len(inspected_files),
                "file_size": sum(item["size"] or 0 for item in inspected_files) or None,
                "files": inspected_files,
                "message": "qBittorrent 已读取文件列表。默认全选；你可以只保留需要下载的媒体文件。",
            }
            source["bt_info_hash"] = inspected["info_hash"]
            return inspected
        finally:
            if torrent_hash:
                try:
                    client.pause(torrent_hash)
                    client.delete(torrent_hash, delete_files=True)
                except qbittorrent.QbittorrentError:
                    pass
            shutil.rmtree(inspect_dir, ignore_errors=True)
    if route["source_type"] in {"magnet", "thunder_bt"} and route["resolved_url"].startswith("magnet:"):
        source["torrent_data"] = cached_magnet_metadata(route["resolved_url"])
        if source["torrent_data"] is not None:
            if logger is not None:
                logger.debug("已从本地缓存读取 BT 元数据，正在整理文件列表。")
        else:
            if logger is not None:
                logger.debug("正在查询公共 BT 元数据缓存；只会提交该磁力链接的 Info Hash。")
            source["torrent_data"] = fetch_public_magnet_metadata(route["resolved_url"])
            if source["torrent_data"] is None:
                if logger is not None:
                    logger.debug("公共元数据缓存未命中，正在通过 DHT 查找可返回文件列表的节点。")
                source["torrent_data"] = aria2.fetch_magnet_metadata(
                    route["resolved_url"],
                    get_download_settings()["aria2"]["bt_stall_timeout"],
                    str(DATA_DIR / ".video-downloader" / "aria2-dht.dat"),
                )
            cache_magnet_metadata(route["resolved_url"], source["torrent_data"])
            if logger is not None:
                logger.debug("已收到并校验 BT 元数据，正在整理文件列表。")
    elif route["source_type"] in {"torrent_url", "thunder_bt"} and route["resolved_url"].lower().startswith(("http://", "https://")):
        if logger is not None:
            logger.debug("正在读取远程 .torrent 文件。")
        torrent_data, torrent_name = fetch_torrent_url(route["resolved_url"])
        source["torrent_data"] = torrent_data
        source["torrent_name"] = torrent_name
        if logger is not None:
            logger.debug("远程种子文件读取完成，正在整理文件列表。")
    inspected = inspect_non_ytdlp_input(
        route,
        source.get("url") or "",
        source.get("torrent_data"),
        source.get("torrent_name"),
    )
    if inspected.get("kind") == "torrent":
        info_hash = inspected.get("info_hash") or magnet_info_hash(route.get("resolved_url") or "")
        if info_hash:
            source["bt_info_hash"] = info_hash
            inspected["info_hash"] = info_hash
    return inspected


def run_inspect(inspect_id: str, source: dict[str, Any]) -> None:
    with inspect_jobs_lock:
        inspect_jobs[inspect_id]["status"] = "running"
        inspect_jobs[inspect_id]["started_at"] = now()
        inspect_jobs[inspect_id]["last_activity_at"] = now()
    stop_heartbeat = threading.Event()

    def keep_inspect_feedback_alive() -> None:
        while not stop_heartbeat.wait(INSPECT_HEARTBEAT_SECONDS):
            append_inspect_log(
                inspect_id,
                "info",
                "仍在解析，正在等待下载源返回信息。网页视频和大型种子通常需要更多时间。",
            )

    heartbeat = threading.Thread(target=keep_inspect_feedback_alive, daemon=True)
    heartbeat.start()
    route = source["route"]
    engine_label = {"yt-dlp": "yt-dlp", "aria2": "aria2", "qbittorrent": "qBittorrent"}[route["engine"]]
    append_inspect_log(inspect_id, "info", f"已识别为 {engine_label} 任务。")
    append_inspect_log(inspect_id, "info", "正在读取下载内容信息。")
    try:
        media = inspect_source(source, InspectLogger(inspect_id))
        append_inspect_log(inspect_id, "info", "下载内容信息读取完成，正在整理任务。")
        with inspect_jobs_lock:
            inspect_jobs[inspect_id]["status"] = "completed"
            inspect_jobs[inspect_id]["media"] = media
        append_inspect_log(inspect_id, "info", "解析完成，可以确认下载。")
    except (DownloadError, InputError, aria2.Aria2Error, qbittorrent.QbittorrentError) as error:
        message = friendly_inspect_error(error) if isinstance(error, DownloadError) else str(error)
        with inspect_jobs_lock:
            inspect_jobs[inspect_id]["status"] = "failed"
            inspect_jobs[inspect_id]["error"] = message
        append_inspect_log(inspect_id, "error", f"解析失败：{message}")
    except Exception as error:
        message = friendly_inspect_error(error)
        with inspect_jobs_lock:
            inspect_jobs[inspect_id]["status"] = "failed"
            inspect_jobs[inspect_id]["error"] = message
        append_inspect_log(inspect_id, "error", f"解析异常：{message}")
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=1)


def update_download(download_id: str, **values: Any) -> None:
    if not values:
        return

    if isinstance(values.get("error"), str):
        values["error"] = normalize_message(values["error"])
    values["updated_at"] = now()
    assignments = ", ".join(f"{column} = ?" for column in values)
    with connection() as database:
        previous = database.execute(
            "SELECT status, title FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
        database.execute(
            f"UPDATE downloads SET {assignments} WHERE id = ?",
            [*values.values(), download_id],
        )
    new_status = values.get("status")
    if (
        previous is not None
        and new_status in {"completed", "failed"}
        and previous["status"] != new_status
    ):
        title = str(values.get("title") or previous["title"] or "未命名任务")
        message = f"{title} 下载完成。" if new_status == "completed" else f"{title} 下载失败。"
        threading.Thread(
            target=send_system_notification,
            args=("Video Downloader", message),
            name="download-notification",
            daemon=True,
        ).start()


def send_system_notification(title: str, message: str) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        if not get_download_settings()["system_notifications"]:
            return False
        result = subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e",
                "end run",
                "--",
                title,
                message,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def download_record(download_id: str, include_metadata: bool = False) -> dict[str, Any]:
    with connection() as database:
        row = database.execute(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="找不到这条下载记录。")

    record = dict(row)
    record["queue_position"] = None
    if record.get("status") == "queued":
        with connection() as database:
            position = database.execute(
                """
                SELECT COUNT(*) AS position
                FROM downloads
                WHERE task_deleted = 0 AND status = 'queued'
                  AND (
                    priority > ?
                    OR (priority = ? AND created_at < ?)
                    OR (priority = ? AND created_at = ? AND id <= ?)
                  )
                """,
                (
                    record.get("priority") or 0,
                    record.get("priority") or 0,
                    record["created_at"],
                    record.get("priority") or 0,
                    record["created_at"],
                    download_id,
                ),
            ).fetchone()
        record["queue_position"] = position["position"] if position else None
    # The full extractor result may contain temporary media URLs. Keep the curated
    # metadata locally for future library features, but never send it to the UI.
    metadata_json = record.pop("metadata_json")
    record.pop("engine_metadata_json", None)
    record.pop("output_files_json", None)
    record["file_exists"] = False
    if record.get("file_path"):
        media_path = Path(record["file_path"]).resolve()
        download_dir = Path(record.get("download_dir") or DOWNLOAD_DIR).resolve()
        try:
            media_path.relative_to(download_dir)
            record["file_exists"] = media_path.is_file()
        except ValueError:
            pass
    if include_metadata:
        record["metadata"] = json.loads(metadata_json or "{}")
    return record


def download_engine_metadata(download_id: str) -> dict[str, Any]:
    with connection() as database:
        row = database.execute("SELECT engine_metadata_json FROM downloads WHERE id = ?", (download_id,)).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row["engine_metadata_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def download_output_files(download_id: str) -> list[dict[str, Any]]:
    download_record(download_id)
    with connection() as database:
        row = database.execute("SELECT output_files_json FROM downloads WHERE id = ?", (download_id,)).fetchone()
    if row is None:
        return []
    try:
        raw_outputs = json.loads(row["output_files_json"] or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_outputs, list):
        return []

    outputs: list[dict[str, Any]] = []
    for item in raw_outputs:
        if not isinstance(item, dict) or not isinstance(item.get("relative_path"), str):
            continue
        outputs.append(
            {
                "index": len(outputs),
                "relative_path": item["relative_path"],
                "size": item.get("size") if isinstance(item.get("size"), int) else None,
                "file_type": item.get("file_type") if isinstance(item.get("file_type"), str) else "application/octet-stream",
                "playable": bool(item.get("playable")),
            }
        )
    return outputs


def resolve_download_output(download_id: str, output_index: int, playable_only: bool = False) -> tuple[dict[str, Any], Path]:
    record = download_record(download_id)
    outputs = download_output_files(download_id)
    if output_index < 0 or output_index >= len(outputs):
        raise HTTPException(status_code=404, detail="找不到这个输出文件。")
    output = outputs[output_index]
    if playable_only and not output["playable"]:
        raise HTTPException(status_code=409, detail="这个输出不是可播放的视频文件。")

    relative_path = Path(output["relative_path"])
    if relative_path.is_absolute():
        raise HTTPException(status_code=409, detail="输出文件路径无效。")
    download_dir = Path(record.get("download_dir") or DOWNLOAD_DIR).resolve()
    output_path = (download_dir / relative_path).resolve()
    allowed_root = (
        download_dir / "BT" / download_id
        if record.get("source_type") in {"magnet", "torrent_url", "torrent_file", "thunder_bt"}
        else download_dir
    ).resolve()
    try:
        output_path.relative_to(allowed_root)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="输出文件不在记录的保存位置中。") from error
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="本地输出文件不存在。")
    return output, output_path


def reveal_in_finder(path: Path) -> None:
    if sys.platform != "darwin":
        raise HTTPException(status_code=409, detail="当前系统不支持在 Finder 中显示文件。")
    try:
        result = subprocess.run(["open", "-R", str(path)], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=500, detail=f"无法打开 Finder：{error}") from error
    if result.returncode != 0:
        message = normalize_message(result.stderr) or "Finder 没有打开该文件。"
        raise HTTPException(status_code=500, detail=message)


def move_to_trash(path: Path) -> Path:
    trash_dir = Path.home() / ".Trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    destination = trash_dir / path.name
    counter = 2
    while destination.exists():
        destination = trash_dir / f"{path.stem} {counter}{path.suffix}"
        counter += 1
    try:
        shutil.move(str(path), str(destination))
    except OSError as error:
        raise RuntimeError(str(error)) from error
    return destination


def related_video_files(media_path: Path) -> list[Path]:
    allowed_suffixes = {".jpg", ".jpeg", ".webp", ".png", ".vtt", ".srt", ".ass", ".ssa", ".lrc"}
    related = {media_path}
    prefix = f"{media_path.stem}."
    try:
        siblings = list(media_path.parent.iterdir())
    except OSError:
        siblings = []
    for sibling in siblings:
        if not sibling.is_file() or not sibling.name.startswith(prefix):
            continue
        lower_name = sibling.name.lower()
        if lower_name == f"{media_path.stem.lower()}.info.json" or sibling.suffix.lower() in allowed_suffixes:
            related.add(sibling.resolve())
    return sorted(related, key=lambda item: str(item).lower())


def playable_subtitle_files(media_path: Path) -> list[Path]:
    prefix = f"{media_path.stem}."
    try:
        siblings = media_path.parent.iterdir()
    except OSError:
        return []
    return sorted(
        (
            sibling.resolve()
            for sibling in siblings
            if sibling.is_file()
            and not sibling.is_symlink()
            and sibling.name.startswith(prefix)
            and sibling.suffix.lower() in {".vtt", ".srt"}
        ),
        key=lambda item: item.name.casefold(),
    )


def subtitle_track_label(media_path: Path, subtitle_path: Path) -> tuple[str, str]:
    suffix = subtitle_path.name[len(media_path.stem): -len(subtitle_path.suffix)].strip("._- ")
    language_candidate = suffix.split(".", 1)[0]
    language = language_candidate if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language_candidate) else "und"
    return (suffix or "默认字幕", language[:35])


def srt_to_webvtt(path: Path) -> bytes:
    content = path.read_bytes()
    text: str | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(status_code=422, detail="字幕文件编码无法识别。")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(
        r"(?m)^(\d{1,2}:\d{2}:\d{2}),(\d{3})(\s+-->\s+\d{1,2}:\d{2}:\d{2}),(\d{3})",
        r"\1.\2\3.\4",
        normalized,
    )
    return f"WEBVTT\n\n{normalized.lstrip()}".encode("utf-8")


def incomplete_task_files(record: dict[str, Any]) -> list[Path]:
    if record.get("status") == "completed" or not record.get("download_dir"):
        return []

    download_root = Path(record["download_dir"]).resolve()
    candidates: list[Path] = []
    if record.get("source_type") in {"magnet", "torrent_url", "torrent_file", "thunder_bt"}:
        candidates.append(download_root / "BT" / str(record["id"]))
    else:
        file_path = record.get("file_path")
        if file_path:
            candidates.append(Path(file_path))
        title = record.get("title")
        if record.get("engine") == "aria2" and isinstance(title, str) and Path(title).name == title:
            candidates.extend([download_root / title, download_root / f"{title}.aria2"])

    managed_files: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(download_root)
        except ValueError:
            continue
        if resolved.exists() and resolved not in seen:
            managed_files.append(resolved)
            seen.add(resolved)
    return managed_files


def path_tree_size(path: Path) -> tuple[int, int]:
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    total_size = 0
    file_count = 0
    try:
        descendants = path.rglob("*")
        for child in descendants:
            try:
                if child.is_file() and not child.is_symlink():
                    total_size += child.stat().st_size
                    file_count += 1
            except OSError:
                continue
    except OSError:
        pass
    return total_size, file_count


def incomplete_residue_preview() -> dict[str, Any]:
    with connection() as database:
        rows = database.execute(
            """
            SELECT * FROM downloads
            WHERE status IN ('failed', 'cancelled', 'interrupted')
            ORDER BY updated_at DESC
            """
        ).fetchall()
        active_rows = database.execute(
            """
            SELECT download_dir FROM downloads
            WHERE status IN ('queued', 'running', 'processing', 'paused')
              AND download_dir IS NOT NULL
            """
        ).fetchall()

    protected_roots = {
        Path(row["download_dir"]).resolve()
        for row in active_rows
        if row["download_dir"]
    }

    residues: dict[Path, dict[str, Any]] = {}
    scanned_roots: set[Path] = set()
    for row in rows:
        record = dict(row)
        download_root_value = record.get("download_dir")
        if not download_root_value:
            continue
        download_root = Path(download_root_value).resolve()
        if not download_root.exists():
            continue

        candidates = incomplete_task_files(record)
        torrent_source = download_root / ".video-downloader" / "torrents" / f"{record['id']}.torrent"
        if torrent_source.is_file():
            candidates.append(torrent_source.resolve())

        if download_root not in scanned_roots:
            scanned_roots.add(download_root)
            for pattern in ("*.part", "*.ytdl", "*.aria2"):
                try:
                    for candidate in download_root.rglob(pattern):
                        if candidate.is_file() and not candidate.is_symlink():
                            candidates.append(candidate.resolve())
                except OSError:
                    continue

        for candidate in candidates:
            path = candidate.resolve()
            try:
                path.relative_to(download_root)
            except ValueError:
                continue
            if any(path == root or path.is_relative_to(root) for root in protected_roots):
                continue
            if not path.exists() or path in residues:
                continue
            size, file_count = path_tree_size(path)
            lower_name = path.name.lower()
            kind = (
                "BT 临时目录"
                if path.is_dir()
                else "aria2 控制文件"
                if lower_name.endswith(".aria2")
                else "yt-dlp 临时文件"
                if lower_name.endswith((".part", ".ytdl"))
                else "种子缓存"
                if lower_name.endswith(".torrent")
                else "未完成文件"
            )
            residues[path] = {
                "path": str(path),
                "kind": kind,
                "size": size,
                "file_count": file_count,
                "download_id": record["id"],
                "title": record.get("title") or "未命名任务",
                "status": record.get("status"),
                "task_deleted": bool(record.get("task_deleted")),
                "updated_at": record.get("updated_at"),
            }

    items = sorted(residues.values(), key=lambda item: (item["updated_at"] or "", item["path"]), reverse=True)
    return {
        "items": items,
        "total_items": len(items),
        "total_bytes": sum(item["size"] for item in items),
    }


def cleanup_incomplete_residues(request: IncompleteCleanupRequest) -> dict[str, Any]:
    preview = incomplete_residue_preview()
    allowed_paths = {item["path"] for item in preview["items"]}
    requested_paths = list(dict.fromkeys(request.paths))
    invalid_paths = [path for path in requested_paths if path not in allowed_paths]
    if invalid_paths:
        raise HTTPException(status_code=409, detail="待清理文件已经变化，请重新扫描后再试。")

    selected = sorted((Path(path).resolve() for path in requested_paths), key=lambda path: len(path.parts))
    targets: list[Path] = []
    for path in selected:
        if any(path == parent or path.is_relative_to(parent) for parent in targets):
            continue
        targets.append(path)

    trashed_files: list[str] = []
    failed_files: list[dict[str, str]] = []
    freed_bytes = 0
    for path in targets:
        size, _file_count = path_tree_size(path)
        try:
            move_to_trash(path)
            trashed_files.append(str(path))
            freed_bytes += size
        except RuntimeError as error:
            failed_files.append({"path": str(path), "error": normalize_message(str(error))})
    return {
        "trashed_files": trashed_files,
        "failed_files": failed_files,
        "freed_bytes": freed_bytes,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def duplicate_media_preview() -> dict[str, Any]:
    with connection() as database:
        rows = database.execute(
            """
            SELECT * FROM downloads
            WHERE status = 'completed'
              AND library_visible = 1
              AND file_path IS NOT NULL
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()

    paths: dict[str, dict[str, Any]] = {}
    skipped_files = 0
    for row in rows:
        record = dict(row)
        try:
            media_path, _download_dir = local_video_path(record)
            stat = media_path.stat()
        except (HTTPException, OSError):
            skipped_files += 1
            continue
        path_key = str(media_path)
        if path_key in paths:
            continue
        try:
            metadata = json.loads(record.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        paths[path_key] = {
            "id": record["id"],
            "title": record.get("title") or media_path.stem,
            "path": path_key,
            "size": stat.st_size,
            "file_origin": record.get("file_origin") or "downloaded",
            "created_at": record.get("created_at"),
            "media_type": metadata.get("media_type", "video"),
        }

    size_candidates: dict[int, list[dict[str, Any]]] = {}
    for item in paths.values():
        size_candidates.setdefault(item["size"], []).append(item)

    hashed_groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for size, items in size_candidates.items():
        if len(items) < 2:
            continue
        for item in items:
            try:
                digest = file_sha256(Path(item["path"]))
            except OSError:
                skipped_files += 1
                continue
            hashed_groups.setdefault((size, digest), []).append(item)

    groups = []
    for (size, digest), items in hashed_groups.items():
        if len(items) < 2:
            continue
        groups.append(
            {
                "fingerprint": digest,
                "size": size,
                "reclaimable_bytes": size * (len(items) - 1),
                "items": items,
            }
        )
    groups.sort(key=lambda group: (-group["reclaimable_bytes"], group["fingerprint"]))
    return {
        "groups": groups,
        "total_groups": len(groups),
        "total_files": sum(len(group["items"]) for group in groups),
        "scanned_files": len(paths),
        "skipped_files": skipped_files,
        "potential_reclaim_bytes": sum(group["reclaimable_bytes"] for group in groups),
    }


def cleanup_duplicate_media(request: DuplicateCleanupRequest) -> dict[str, Any]:
    if not request.confirm:
        raise HTTPException(status_code=422, detail="清理重复媒体前必须明确确认。")
    requested_ids = list(dict.fromkeys(request.download_ids))
    preview = duplicate_media_preview()
    item_groups = {
        item["id"]: group
        for group in preview["groups"]
        for item in group["items"]
    }
    if any(download_id not in item_groups for download_id in requested_ids):
        raise HTTPException(status_code=409, detail="重复媒体已经变化，请重新扫描后再试。")

    selected_ids = set(requested_ids)
    affected_groups = {item_groups[download_id]["fingerprint"]: item_groups[download_id] for download_id in requested_ids}
    if any(all(item["id"] in selected_ids for item in group["items"]) for group in affected_groups.values()):
        raise HTTPException(status_code=422, detail="每组重复媒体必须至少保留一个文件。")

    trashed_download_ids: list[str] = []
    trashed_files: list[str] = []
    failed_items: list[dict[str, str]] = []
    freed_bytes = 0
    items = {item["id"]: item for group in affected_groups.values() for item in group["items"]}
    for download_id in requested_ids:
        result = delete_video(download_id, remove_file=True)
        if isinstance(result, JSONResponse):
            detail = json.loads(result.body).get("detail", "文件未能移入废纸篓。")
            failed_items.append({"id": download_id, "error": detail})
            continue
        trashed_download_ids.append(download_id)
        trashed_files.extend(result["trashed_files"])
        freed_bytes += items[download_id]["size"]
    return {
        "trashed_download_ids": trashed_download_ids,
        "trashed_files": trashed_files,
        "failed_items": failed_items,
        "freed_bytes": freed_bytes,
    }


def maintenance_cleanup_preview(retention_days: int) -> dict[str, Any]:
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    with connection() as database:
        logs = database.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(LENGTH(message)), 0) AS text_bytes
            FROM download_logs
            WHERE created_at < ?
              AND download_id NOT IN (
                  SELECT id FROM downloads
                  WHERE status IN ('queued', 'running', 'processing', 'paused')
              )
            """,
            (cutoff,),
        ).fetchone()
        tasks = database.execute(
            """
            SELECT id, title, status, updated_at
            FROM downloads
            WHERE task_deleted = 0
              AND status IN ('failed', 'cancelled', 'interrupted')
              AND updated_at < ?
            ORDER BY updated_at ASC
            """,
            (cutoff,),
        ).fetchall()
    return {
        "retention_days": retention_days,
        "cutoff": cutoff,
        "log_count": int(logs["count"] or 0),
        "log_text_bytes": int(logs["text_bytes"] or 0),
        "task_count": len(tasks),
        "tasks": [dict(row) for row in tasks[:100]],
    }


def run_maintenance_cleanup(request: MaintenanceCleanupRequest) -> dict[str, Any]:
    preview = maintenance_cleanup_preview(request.retention_days)
    cutoff = preview["cutoff"]
    deleted_logs = 0
    cleaned_task_ids: list[str] = []
    with connection() as database:
        if request.clean_download_tasks:
            task_rows = database.execute(
                """
                SELECT id FROM downloads
                WHERE task_deleted = 0
                  AND status IN ('failed', 'cancelled', 'interrupted')
                  AND updated_at < ?
                """,
                (cutoff,),
            ).fetchall()
            cleaned_task_ids = [row["id"] for row in task_rows]
            if cleaned_task_ids:
                placeholders = ", ".join("?" for _ in cleaned_task_ids)
                deleted_logs += database.execute(
                    f"DELETE FROM download_logs WHERE download_id IN ({placeholders})",
                    cleaned_task_ids,
                ).rowcount
                database.execute(
                    f"UPDATE downloads SET task_deleted = 1, updated_at = ? WHERE id IN ({placeholders})",
                    [now(), *cleaned_task_ids],
                )
        if request.clean_download_logs:
            deleted_logs += database.execute(
                """
                DELETE FROM download_logs
                WHERE created_at < ?
                  AND download_id NOT IN (
                      SELECT id FROM downloads
                      WHERE status IN ('queued', 'running', 'processing', 'paused')
                  )
                """,
                (cutoff,),
            ).rowcount

    for download_id in cleaned_task_ids:
        events.publish({"type": "download_deleted", "download_id": download_id})
    return {
        **preview,
        "deleted_logs": deleted_logs,
        "cleaned_tasks": len(cleaned_task_ids),
        "cleaned_task_ids": cleaned_task_ids,
        "completed_at": now(),
    }


def direct_download_file_name(download_dir: str, requested_name: str, download_id: str) -> str:
    safe_name = Path(requested_name).name
    if not safe_name or safe_name != requested_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=422, detail="直链返回了不安全的文件名，无法保存。")
    target = Path(download_dir) / safe_name
    if not target.exists() and not Path(f"{target}.aria2").exists():
        return safe_name
    suffix = "".join(Path(safe_name).suffixes)
    stem = safe_name[: -len(suffix)] if suffix else safe_name
    return f"{stem} [{download_id[:8]}]{suffix}"


def playlist_record(playlist_id: str) -> dict[str, Any]:
    with connection() as database:
        row = database.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到这个播放列表。")
        counts = database.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status IN ('queued', 'running', 'processing') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status IN ('failed', 'interrupted', 'cancelled') THEN 1 ELSE 0 END) AS attention,
                AVG(progress) AS progress
            FROM downloads
            WHERE playlist_id = ?
            """,
            (playlist_id,),
        ).fetchone()
    record = dict(row)
    record.update({
        "download_count": counts["total"] or 0,
        "completed_count": counts["completed"] or 0,
        "active_count": counts["active"] or 0,
        "attention_count": counts["attention"] or 0,
        "progress": round(counts["progress"] or 0, 1),
    })
    return record


def normalize_message(message: str) -> str:
    return " ".join(ANSI_ESCAPE_PATTERN.sub("", message).replace("\r", " ").split())


def append_log(download_id: str, level: str, message: str) -> None:
    normalized_message = normalize_message(message)[:1000]
    if not normalized_message:
        return

    with connection() as database:
        database.execute(
            """
            INSERT INTO download_logs (download_id, level, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (download_id, level, normalized_message, now()),
        )
    events.publish({"type": "log", "download_id": download_id})


class DownloadLogger:
    def __init__(self, download_id: str) -> None:
        self.download_id = download_id

    def debug(self, message: str) -> None:
        if message.startswith("[debug]") or message.startswith("[download]"):
            return
        append_log(self.download_id, "info", message)

    def warning(self, message: str) -> None:
        append_log(self.download_id, "warning", message)

    def error(self, message: str) -> None:
        append_log(self.download_id, "error", message)


def metadata_values(info: dict[str, Any]) -> dict[str, Any]:
    subtitles = {
        language: [
            {"extension": item.get("ext"), "name": item.get("name")}
            for item in entries
        ]
        for language, entries in (info.get("subtitles") or {}).items()
    }
    library_metadata = {
        "description": info.get("description"),
        "chapters": [
            {"title": item.get("title"), "start_time": item.get("start_time")}
            for item in (info.get("chapters") or [])
        ],
        "categories": info.get("categories") or [],
        "tags": info.get("tags") or [],
        "playlist": info.get("playlist"),
        "playlist_index": info.get("playlist_index"),
        "subtitles": subtitles,
    }
    return {
        "title": info.get("title") or "未命名视频",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": thumbnail_url(info.get("thumbnail")),
        "webpage_url": info.get("webpage_url") or info.get("original_url"),
        "video_id": info.get("id"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "resolution": info.get("resolution")
        or (f"{info['height']}p" if info.get("height") else None),
        "metadata_json": json.dumps(library_metadata, ensure_ascii=False),
    }


def publish_download(download_id: str) -> None:
    events.publish({"type": "download", "download": download_record(download_id)})


def active_download_ids(source_url: str, excluded_id: str | None = None) -> list[str]:
    query = "SELECT id FROM downloads WHERE source_url = ? AND status IN ('queued', 'running', 'processing', 'paused')"
    values: list[str] = [source_url]
    if excluded_id:
        query += " AND id != ?"
        values.append(excluded_id)
    with connection() as database:
        return [row["id"] for row in database.execute(query, values).fetchall()]


def completed_download_ids(
    source_url: str,
    resolved_url: str | None = None,
    webpage_url: str | None = None,
    video_id: str | None = None,
    source_platform: str | None = None,
) -> list[str]:
    urls = list(dict.fromkeys(
        value for value in (source_url, resolved_url, webpage_url) if value
    ))
    clauses: list[str] = []
    values: list[str] = []
    if urls:
        placeholders = ", ".join("?" for _ in urls)
        clauses.append(
            f"(source_url IN ({placeholders}) OR resolved_url IN ({placeholders}) OR webpage_url IN ({placeholders}))"
        )
        values.extend([*urls, *urls, *urls])
    if video_id and source_platform:
        clauses.append("(video_id = ? AND source_platform = ?)")
        values.extend([video_id, source_platform])
    if not clauses:
        return []

    with connection() as database:
        rows = database.execute(
            f"""
            SELECT id, file_path
            FROM downloads
            WHERE status = 'completed'
              AND library_visible = 1
              AND ({' OR '.join(clauses)})
            """,
            values,
        ).fetchall()
    return [
        row["id"]
        for row in rows
        if row["file_path"] and Path(row["file_path"]).is_file()
    ]


def _download_bt_info_hash(row: sqlite3.Row) -> str | None:
    try:
        metadata = json.loads(row["engine_metadata_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    stored_hash = metadata.get("bt_info_hash") if isinstance(metadata, dict) else None
    if isinstance(stored_hash, str) and re.fullmatch(r"[0-9a-fA-F]{40}", stored_hash):
        return stored_hash.lower()
    encoded_torrent = metadata.get("torrent_base64") if isinstance(metadata, dict) else None
    if isinstance(encoded_torrent, str):
        try:
            inspected = inspect_torrent(base64.b64decode(encoded_torrent, validate=True), None)
            info_hash = inspected.get("info_hash")
            if isinstance(info_hash, str):
                return info_hash
        except (ValueError, InputError):
            pass
    for value in (row["resolved_url"], row["source_url"]):
        if isinstance(value, str):
            info_hash = magnet_info_hash(value)
            if info_hash:
                return info_hash
    return None


def _download_selected_bt_indexes(row: sqlite3.Row) -> list[int] | None:
    try:
        metadata = json.loads(row["engine_metadata_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    indexes = metadata.get("selected_file_indexes") if isinstance(metadata, dict) else None
    if not isinstance(indexes, list) or not all(isinstance(value, int) for value in indexes):
        return None
    return sorted(set(indexes))


def _completed_bt_output_exists(row: sqlite3.Row) -> bool:
    if row["file_path"] and Path(row["file_path"]).is_file():
        return True
    try:
        outputs = json.loads(row["output_files_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(outputs, list) or not row["download_dir"]:
        return False
    download_root = Path(row["download_dir"]).resolve()
    for output in outputs:
        if not isinstance(output, dict) or not isinstance(output.get("relative_path"), str):
            continue
        candidate = (download_root / output["relative_path"]).resolve()
        try:
            candidate.relative_to(download_root)
        except ValueError:
            continue
        if candidate.is_file():
            return True
    return False


def matching_bt_download_ids(
    info_hash: str | None,
    selected_file_indexes: list[int] | None,
    statuses: tuple[str, ...],
    *,
    require_completed_output: bool = False,
) -> list[str]:
    if not info_hash or not statuses:
        return []
    placeholders = ", ".join("?" for _ in statuses)
    with connection() as database:
        rows = database.execute(
            f"""
            SELECT id, source_url, resolved_url, file_path, output_files_json, download_dir, engine_metadata_json
            FROM downloads
            WHERE source_type IN ('magnet', 'torrent_url', 'torrent_file', 'thunder_bt')
              AND status IN ({placeholders})
            """,
            statuses,
        ).fetchall()
    expected_indexes = sorted(set(selected_file_indexes)) if selected_file_indexes is not None else None
    return [
        row["id"]
        for row in rows
        if _download_bt_info_hash(row) == info_hash.lower()
        and _download_selected_bt_indexes(row) == expected_indexes
        and (not require_completed_output or _completed_bt_output_exists(row))
    ]


def source_follow_record(follow_id: str) -> dict[str, Any]:
    with connection() as database:
        row = database.execute(
            "SELECT * FROM source_follows WHERE id = ?", (follow_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这个关注源。")
    record = dict(row)
    try:
        entries = json.loads(record.pop("entries_json") or "[]")
    except json.JSONDecodeError:
        entries = []
    record["entries"] = entries if isinstance(entries, list) else []
    record["new_count"] = len(record["entries"])
    record["check_on_startup"] = bool(record["check_on_startup"])
    return record


def list_source_follows() -> list[dict[str, Any]]:
    with connection() as database:
        ids = [
            row["id"]
            for row in database.execute(
                "SELECT id FROM source_follows ORDER BY created_at DESC"
            ).fetchall()
        ]
    return [source_follow_record(follow_id) for follow_id in ids]


def source_follow_new_entries(
    entries: list[dict[str, Any]], source_platform: str
) -> list[dict[str, Any]]:
    new_entries: list[dict[str, Any]] = []
    for entry in entries:
        webpage_url = entry.get("webpage_url")
        if not isinstance(webpage_url, str):
            continue
        if completed_download_ids(webpage_url, webpage_url=webpage_url, source_platform=source_platform):
            continue
        if active_download_ids(webpage_url):
            continue
        new_entries.append(entry)
    return new_entries


def inspect_follow_source(url: str) -> dict[str, Any]:
    validate_url(url)
    try:
        media = inspect_url(url)
    except DownloadError as error:
        raise HTTPException(status_code=422, detail=friendly_inspect_error(error)) from error
    except InputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if media.get("kind") != "playlist":
        raise HTTPException(status_code=422, detail="关注源必须是频道、合集或播放列表地址。")
    return media


def create_source_follow(request: SourceFollowRequest) -> dict[str, Any]:
    source_url = request.url.strip()
    with connection() as database:
        existing = database.execute(
            "SELECT id FROM source_follows WHERE source_url = ?", (source_url,)
        ).fetchone()
    if existing is not None:
        raise HTTPException(status_code=409, detail="这个频道或合集已经关注。")
    media = inspect_follow_source(source_url)
    entries = source_follow_new_entries(
        list(media.get("entries") or []),
        str(media.get("source_platform") or platform_name(source_url)),
    )
    timestamp = now()
    follow_id = str(uuid.uuid4())
    with connection() as database:
        database.execute(
            """
            INSERT INTO source_follows (
                id, source_url, source_platform, title, uploader, thumbnail,
                external_id, check_on_startup, last_checked_at, last_error,
                entries_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                follow_id,
                source_url,
                media.get("source_platform") or platform_name(source_url),
                media.get("title") or "未命名关注源",
                media.get("uploader"),
                media.get("thumbnail"),
                media.get("external_id"),
                int(request.check_on_startup),
                timestamp,
                json.dumps(entries, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
    return source_follow_record(follow_id)


def check_source_follow(follow_id: str) -> dict[str, Any]:
    follow = source_follow_record(follow_id)
    try:
        media = inspect_follow_source(follow["source_url"])
    except HTTPException as error:
        with connection() as database:
            database.execute(
                "UPDATE source_follows SET last_error = ?, last_checked_at = ?, updated_at = ? WHERE id = ?",
                (str(error.detail), now(), now(), follow_id),
            )
        raise HTTPException(status_code=502, detail=f"更新检查失败：{error.detail}") from error
    entries = source_follow_new_entries(
        list(media.get("entries") or []),
        str(media.get("source_platform") or follow["source_platform"]),
    )
    timestamp = now()
    with connection() as database:
        database.execute(
            """
            UPDATE source_follows
            SET source_platform = ?, title = ?, uploader = ?, thumbnail = ?,
                external_id = ?, last_checked_at = ?, last_error = NULL,
                entries_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                media.get("source_platform") or follow["source_platform"],
                media.get("title") or follow["title"],
                media.get("uploader"),
                media.get("thumbnail"),
                media.get("external_id"),
                timestamp,
                json.dumps(entries, ensure_ascii=False),
                timestamp,
                follow_id,
            ),
        )
    return source_follow_record(follow_id)


def register_engine_process(download_id: str, process: subprocess.Popen[bytes] | None) -> None:
    with engine_processes_lock:
        if process is None:
            engine_processes.pop(download_id, None)
        else:
            engine_processes[download_id] = process


def cancel_download(download_id: str, message: str) -> None:
    with cancelled_downloads_lock:
        cancelled_downloads.add(download_id)
    with engine_processes_lock:
        process = engine_processes.get(download_id)
    if process and process.poll() is None:
        process.terminate()
    update_download(download_id, status="cancelled", error=message, speed=None, eta=None, restart_pending=0)
    append_log(download_id, "warning", message)
    publish_download(download_id)


def download_was_cancelled(download_id: str) -> bool:
    with cancelled_downloads_lock:
        return download_id in cancelled_downloads


def download_status(download_id: str) -> str | None:
    with connection() as database:
        row = database.execute("SELECT status FROM downloads WHERE id = ?", (download_id,)).fetchone()
    return row["status"] if row is not None else None


def download_is_paused(download_id: str) -> bool:
    return download_status(download_id) == "paused"


def download_is_interrupted(download_id: str) -> bool:
    return download_status(download_id) == "interrupted"


def clear_download_cancellation(download_id: str) -> None:
    with cancelled_downloads_lock:
        cancelled_downloads.discard(download_id)


def configured_download_concurrency() -> int:
    try:
        return int(get_download_settings()["max_concurrent_downloads"])
    except Exception:
        return MAX_CONCURRENT_DOWNLOADS


def scheduled_download_has_capacity(args: tuple[Any, ...]) -> bool:
    if not args or not isinstance(args[0], str):
        return True
    download_id = args[0]
    with connection() as database:
        row = database.execute(
            "SELECT status, download_dir, total_bytes, downloaded_bytes FROM downloads WHERE id = ?",
            (download_id,),
        ).fetchone()
    if row is None or row["status"] != "queued":
        return True
    try:
        ensure_disk_capacity(
            row["download_dir"] or str(DOWNLOAD_DIR),
            row["total_bytes"],
            row["downloaded_bytes"] or 0,
        )
    except HTTPException as error:
        update_download(download_id, status="failed", error=str(error.detail), speed=None, eta=None, restart_pending=0)
        append_log(download_id, "error", f"下载开始前检查失败：{error.detail}")
        publish_download(download_id)
        return False
    return True


def run_scheduled_download(task: Any, args: tuple[Any, ...], future: Future[Any]) -> None:
    global active_scheduled_downloads
    try:
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(task(*args) if scheduled_download_has_capacity(args) else None)
            except BaseException as error:
                future.set_exception(error)
    finally:
        with download_queue_condition:
            active_scheduled_downloads -= 1
            download_queue_condition.notify_all()


def dispatch_download_queue() -> None:
    global active_scheduled_downloads
    while True:
        with download_queue_condition:
            while not download_queue_entries or active_scheduled_downloads >= configured_download_concurrency():
                download_queue_condition.wait(timeout=0.5)
            _sort_priority, _sequence, task, args, future = heapq.heappop(download_queue_entries)
            if future.cancelled():
                continue
            active_scheduled_downloads += 1
        download_executor.submit(run_scheduled_download, task, args, future)


def ensure_download_scheduler() -> None:
    global download_scheduler_thread
    with download_queue_condition:
        if download_scheduler_thread is not None and download_scheduler_thread.is_alive():
            return
        download_scheduler_thread = threading.Thread(
            target=dispatch_download_queue,
            name="download-scheduler",
            daemon=True,
        )
        download_scheduler_thread.start()


def notify_download_scheduler() -> None:
    with download_queue_condition:
        download_queue_condition.notify_all()


def submit_download_task(task: Any, *args: Any, priority: int = 0) -> Future[Any]:
    global download_queue_sequence
    future: Future[Any] = Future()
    with download_queue_condition:
        download_queue_sequence += 1
        heapq.heappush(
            download_queue_entries,
            (-max(-1, min(1, int(priority))), download_queue_sequence, task, args, future),
        )
    ensure_download_scheduler()
    notify_download_scheduler()
    return future


def run_download(
    download_id: str,
    url: str,
    requested_format: str | None,
    download_dir: str,
    write_thumbnail: bool,
    write_info_json: bool,
    yt_dlp_config: str,
    ffmpeg_config: str,
) -> None:
    if download_status(download_id) in {"paused", "interrupted", "cancelled"}:
        return
    if download_was_cancelled(download_id):
        clear_download_cancellation(download_id)
        return

    update_download(download_id, status="running", error=None, restart_pending=0)
    append_log(download_id, "info", "下载已开始：正在读取媒体信息。")
    publish_download(download_id)

    if download_was_cancelled(download_id):
        clear_download_cancellation(download_id)
        return

    download_started = False
    final_media_path: Path | None = None
    expected_download_parts = 1
    download_part_progress: dict[str, float] = {}
    finished_download_parts: set[str] = set()

    def progress_hook(progress: dict[str, Any]) -> None:
        nonlocal download_started, expected_download_parts
        stop_status = download_status(download_id)
        if stop_status in {"paused", "interrupted"}:
            raise DownloadError("下载已停止")
        if download_was_cancelled(download_id) or stop_status == "cancelled":
            raise DownloadError("下载已取消")
        info = progress.get("info_dict") or {}
        status = progress.get("status")
        values = metadata_values(info) if info else {}
        format_id = str(info.get("format_id") or progress.get("filename") or "media")
        vcodec = info.get("vcodec")
        acodec = info.get("acodec")
        if (
            expected_download_parts > 1
            and isinstance(vcodec, str)
            and isinstance(acodec, str)
            and vcodec != "none"
            and acodec != "none"
        ):
            expected_download_parts = 1

        if status == "downloading":
            if not download_started:
                append_log(download_id, "info", "已获取媒体信息，开始下载文件。")
                download_started = True
            total_bytes = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            downloaded_bytes = progress.get("downloaded_bytes") or 0
            part_progress = min(1.0, downloaded_bytes / total_bytes) if total_bytes else 0
            download_part_progress[format_id] = part_progress
            expected_download_parts = max(expected_download_parts, len(download_part_progress))
            percent = min(
                99,
                round(sum(download_part_progress.values()) / expected_download_parts * 100, 1),
            )
            values.update(
                {
                    "status": "running",
                    "progress": percent,
                    "downloaded_bytes": downloaded_bytes,
                    "total_bytes": total_bytes,
                    "speed": progress.get("speed"),
                    "eta": progress.get("eta"),
                }
            )
            if isinstance(progress.get("filename"), str):
                values["file_path"] = progress["filename"]
        elif status == "finished":
            download_part_progress[format_id] = 1
            finished_download_parts.add(format_id)
            expected_download_parts = max(expected_download_parts, len(download_part_progress))
            all_parts_finished = len(finished_download_parts) >= expected_download_parts
            if all_parts_finished:
                append_log(download_id, "info", "媒体文件下载完成，正在合并并写入媒体信息。")
            else:
                append_log(download_id, "info", "一个媒体流下载完成，继续下载其余内容。")
            values.update(
                {
                    "status": "processing" if all_parts_finished else "running",
                    "progress": min(
                        99,
                        round(sum(download_part_progress.values()) / expected_download_parts * 100, 1),
                    ),
                    "file_path": progress.get("filename"),
                    "speed": None,
                    "eta": None,
                }
            )
        else:
            return

        update_download(download_id, **values)
        publish_download(download_id)

    def postprocessor_hook(progress: dict[str, Any]) -> None:
        nonlocal final_media_path
        if progress.get("status") == "started":
            update_download(download_id, status="processing", progress=99, speed=None, eta=None)
            publish_download(download_id)
            return
        if progress.get("status") != "finished":
            return
        candidate = (progress.get("info_dict") or {}).get("filepath")
        if isinstance(candidate, str) and candidate:
            final_media_path = Path(candidate)

    try:
        options = build_ytdlp_download_options(
            yt_dlp_config,
            requested_format,
            download_dir,
            write_thumbnail,
            write_info_json,
            ffmpeg_config,
            progress_hook,
            DownloadLogger(download_id),
        )
        expected_download_parts = ytdlp_download_part_count(str(options.get("format") or ""))
        options["postprocessor_hooks"] = [*options.get("postprocessor_hooks", []), postprocessor_hook]
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)

        if download_is_paused(download_id) or download_is_interrupted(download_id):
            raise DownloadError("下载已停止")
        if download_was_cancelled(download_id):
            raise DownloadError("下载已取消")

        values = metadata_values(info)
        candidate_paths = [
            final_media_path,
            Path(info["filepath"]) if isinstance(info.get("filepath"), str) else None,
            Path(downloader.prepare_filename(info)),
        ]
        final_path = next((path for path in candidate_paths if path is not None and path.is_file()), None)
        if final_path is None:
            raise DownloadError("下载处理已结束，但未找到最终媒体文件。请检查 FFmpeg 配置或保存位置后重试。")
        final_size = final_path.stat().st_size
        try:
            existing_metadata = json.loads(values.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            existing_metadata = {}
        try:
            values.update(local_media_metadata_values(final_path, existing_metadata))
        except (HTTPException, OSError):
            # A completed download remains usable when ffprobe is unavailable;
            # source metadata and the file extension still provide a fallback.
            pass
        values.update(
            {
                "file_path": str(final_path),
                "file_size": final_size,
                "downloaded_bytes": final_size,
                "total_bytes": final_size,
                "download_dir": str(final_path.parent.resolve()),
                "status": "completed",
                "progress": 100,
                "speed": None,
                "eta": None,
                "error": None,
                "restart_pending": 0,
            }
        )
        update_download(download_id, **values)
        append_log(download_id, "info", "下载完成，媒体已加入本地媒体库。")
    except DownloadError as error:
        if download_is_paused(download_id):
            update_download(download_id, status="paused", error=None, speed=None, eta=None, restart_pending=0)
            append_log(download_id, "info", "下载已暂停，已下载部分会保留。")
        elif download_is_interrupted(download_id):
            update_download(download_id, status="interrupted", speed=None, eta=None, restart_pending=1)
            append_log(download_id, "warning", "下载因应用退出而中断，将在下次启动时恢复。")
        elif download_was_cancelled(download_id):
            update_download(download_id, status="cancelled", error="已由用户取消下载。", speed=None, eta=None, restart_pending=0)
            append_log(download_id, "info", "下载已取消。")
        else:
            message = normalize_message(str(error))
            update_download(download_id, status="failed", error=message, speed=None, eta=None, restart_pending=0)
            append_log(download_id, "error", f"下载失败：{error}")
    except Exception as error:  # The UI must keep a task record even for unexpected failures.
        message = normalize_message(str(error))
        update_download(download_id, status="failed", error=message, speed=None, eta=None, restart_pending=0)
        append_log(download_id, "error", f"下载时发生异常：{error}")

    clear_download_cancellation(download_id)
    publish_download(download_id)


def denoise_job_record(job_id: str) -> dict[str, Any]:
    with connection() as database:
        row = database.execute(
            "SELECT * FROM video_denoise_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这条降噪任务。")
    record = dict(row)
    record["filter_config"] = json.loads(record["filter_config"] or "null")
    return record


def update_denoise_job(job_id: str, **values: Any) -> None:
    if not values:
        return

    values["updated_at"] = now()
    assignments = ", ".join(f"{column} = ?" for column in values)
    with connection() as database:
        database.execute(
            f"UPDATE video_denoise_jobs SET {assignments} WHERE id = ?",
            [*values.values(), job_id],
        )


def publish_denoise_job(job_id: str) -> None:
    events.publish({"type": "denoise", "job": denoise_job_record(job_id)})


def local_video_path(video: dict[str, Any]) -> tuple[Path, Path]:
    if video["status"] != "completed" or not video["file_path"]:
        raise HTTPException(status_code=409, detail="只能处理已完成的本地视频。")

    media_path = Path(video["file_path"]).resolve()
    download_dir = Path(video.get("download_dir") or DOWNLOAD_DIR).resolve()
    try:
        media_path.relative_to(download_dir)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="视频文件不在记录的保存位置中。") from error
    if not media_path.is_file():
        raise HTTPException(status_code=404, detail="本地视频文件不存在。")
    return media_path, download_dir


def denoise_filter_config(request: DenoiseRequest, preset: str) -> dict[str, float]:
    defaults = DENOISE_PRESETS[preset]
    return {
        "luma_spatial": request.luma_spatial if request.luma_spatial is not None else defaults["luma_spatial"],
        "chroma_spatial": request.chroma_spatial if request.chroma_spatial is not None else defaults["chroma_spatial"],
        "luma_temporal": request.luma_temporal if request.luma_temporal is not None else defaults["luma_temporal"],
        "chroma_temporal": request.chroma_temporal if request.chroma_temporal is not None else defaults["chroma_temporal"],
    }


def denoise_filter_expression(filter_config: dict[str, float]) -> str:
    return "hqdn3d={luma_spatial}:{chroma_spatial}:{luma_temporal}:{chroma_temporal}".format(
        **filter_config
    )


def run_denoise(
    job_id: str,
    source_path: str,
    output_path: str,
    duration: float | None,
    filter_expression: str,
) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        update_denoise_job(job_id, status="failed", error="未找到 FFmpeg，无法生成降噪副本。")
        publish_denoise_job(job_id)
        return

    source = Path(source_path)
    output = Path(output_path)
    if not source.is_file():
        update_denoise_job(job_id, status="failed", error="原始视频已不存在，无法继续处理。")
        publish_denoise_job(job_id)
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-vf",
        filter_expression,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    update_denoise_job(job_id, status="running", progress=0, error=None)
    publish_denoise_job(job_id)

    recent_output: list[str] = []
    last_progress = -1
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                recent_output.append(line)
                recent_output = recent_output[-8:]
            if not duration or not line.startswith("out_time_ms="):
                continue
            try:
                elapsed_microseconds = int(line.removeprefix("out_time_ms="))
            except ValueError:
                continue
            progress = min(99, max(0, round(elapsed_microseconds / (duration * 1_000_000) * 100)))
            if progress == last_progress:
                continue
            last_progress = progress
            update_denoise_job(job_id, progress=progress)
            publish_denoise_job(job_id)

        if process.wait() != 0:
            detail = recent_output[-1] if recent_output else "FFmpeg 未返回可用的错误信息。"
            raise RuntimeError(detail)
        if not output.is_file():
            raise RuntimeError("FFmpeg 未生成输出文件。")

        update_denoise_job(
            job_id,
            status="completed",
            progress=100,
            file_path=str(output),
            file_size=output.stat().st_size,
            error=None,
        )
    except Exception as error:
        if output.is_file():
            output.unlink()
        update_denoise_job(job_id, status="failed", error=f"降噪处理失败：{error}")
    publish_denoise_job(job_id)


@cache
def ffmpeg_version() -> str | None:
    executable = shutil.which("ffmpeg")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    parts = first_line.split()
    return parts[2] if len(parts) >= 3 and parts[:2] == ["ffmpeg", "version"] else None


def version_parts(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts)


def check_ytdlp_update(force: bool = False) -> dict[str, Any]:
    global yt_dlp_update_cache, yt_dlp_update_cache_at
    with yt_dlp_update_lock:
        if (
            not force
            and yt_dlp_update_cache is not None
            and time.monotonic() - yt_dlp_update_cache_at < YT_DLP_UPDATE_CACHE_SECONDS
        ):
            return dict(yt_dlp_update_cache)
        request = Request(
            "https://pypi.org/pypi/yt-dlp/json",
            headers={"User-Agent": "Video Downloader/0.1"},
        )
        try:
            with build_opener().open(request, timeout=10) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, OSError, ValueError) as error:
            raise HTTPException(status_code=502, detail="暂时无法检查 yt-dlp 更新，请稍后重试。") from error
        latest = payload.get("info", {}).get("version") if isinstance(payload, dict) else None
        if not isinstance(latest, str) or not latest.strip():
            raise HTTPException(status_code=502, detail="PyPI 没有返回有效的 yt-dlp 版本。")
        current = yt_dlp.version.__version__
        result = {
            "current_version": current,
            "latest_version": latest,
            "update_available": version_parts(latest) > version_parts(current),
            "checked_at": now(),
            "source": "PyPI",
        }
        yt_dlp_update_cache = result
        yt_dlp_update_cache_at = time.monotonic()
        return dict(result)


@app.get("/health")
def health() -> dict[str, Any]:
    qbit_settings = get_download_settings()["qbittorrent"]
    qbit_version = None
    if qbit_settings["enabled"]:
        try:
            qbit_version = qbittorrent_client(qbit_settings, timeout=1).app_version()
        except qbittorrent.QbittorrentError:
            pass
    return {
        "status": "ok",
        "engine": "yt-dlp",
        "engine_version": yt_dlp.version.__version__,
        "ffmpeg_available": ffmpeg_version() is not None,
        "ffmpeg_version": ffmpeg_version(),
        "application": {
            "version": app.version,
            "fastapi_version": package_version("fastapi"),
            "uvicorn_version": package_version("uvicorn"),
        },
        "engines": {
            "yt-dlp": {"available": True, "version": yt_dlp.version.__version__},
            "aria2": {"available": aria2.executable() is not None, "version": aria2.version()},
            "qbittorrent": {"available": qbit_version is not None, "version": qbit_version, "configured": qbit_settings["enabled"]},
        },
    }


@app.post("/api/v1/downloads/inspect")
async def inspect(request: InspectRequest) -> dict[str, Any]:
    source = prepare_inspect_source(request)
    try:
        return await asyncio.to_thread(inspect_source, source)
    except DownloadError as error:
        raise HTTPException(status_code=422, detail=friendly_inspect_error(error)) from error
    except (InputError, aria2.Aria2Error, qbittorrent.QbittorrentError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/v1/search/providers")
def get_resource_search_providers() -> list[dict[str, str]]:
    return resource_search_providers()


@app.post("/api/v1/search")
async def search_resources(request: ResourceSearchRequest) -> dict[str, Any]:
    return await asyncio.to_thread(resource_search, request)


@app.get("/api/v1/follows")
def get_source_follows() -> list[dict[str, Any]]:
    return list_source_follows()


@app.post("/api/v1/follows", status_code=201)
async def add_source_follow(request: SourceFollowRequest) -> dict[str, Any]:
    return await asyncio.to_thread(create_source_follow, request)


@app.put("/api/v1/follows/{follow_id}")
def update_source_follow(
    follow_id: str, request: SourceFollowUpdateRequest
) -> dict[str, Any]:
    source_follow_record(follow_id)
    with connection() as database:
        database.execute(
            "UPDATE source_follows SET check_on_startup = ?, updated_at = ? WHERE id = ?",
            (int(request.check_on_startup), now(), follow_id),
        )
    return source_follow_record(follow_id)


@app.post("/api/v1/follows/{follow_id}/check")
async def refresh_source_follow(follow_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(check_source_follow, follow_id)


@app.post("/api/v1/follows/{follow_id}/downloads", status_code=201)
def download_source_follow_entries(
    follow_id: str,
    request: SourceFollowDownloadRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    follow = source_follow_record(follow_id)
    available_urls = {
        entry.get("webpage_url")
        for entry in follow["entries"]
        if isinstance(entry, dict) and isinstance(entry.get("webpage_url"), str)
    }
    selected_urls = list(dict.fromkeys(request.entry_urls))
    if not set(selected_urls).issubset(available_urls):
        raise HTTPException(status_code=422, detail="选择的新视频已变化，请重新检查关注源。")
    result = create_download_batch(
        BatchDownloadRequest(
            items=[
                DownloadRequest(
                    url=url,
                    format_id=request.format_id,
                    priority=request.priority,
                )
                for url in selected_urls
            ]
        ),
        background_tasks,
    )
    created_urls = {
        item["url"] for item in result["successes"] if isinstance(item.get("url"), str)
    }
    remaining = [
        entry for entry in follow["entries"]
        if entry.get("webpage_url") not in created_urls
    ]
    with connection() as database:
        database.execute(
            "UPDATE source_follows SET entries_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(remaining, ensure_ascii=False), now(), follow_id),
        )
    return {**result, "follow": source_follow_record(follow_id)}


@app.delete("/api/v1/follows/{follow_id}")
def delete_source_follow(follow_id: str) -> dict[str, str]:
    source_follow_record(follow_id)
    with connection() as database:
        database.execute("DELETE FROM source_follows WHERE id = ?", (follow_id,))
    return {"id": follow_id}


@app.post("/api/v1/downloads/inspect/start")
async def start_inspect(request: InspectRequest) -> dict[str, Any]:
    source = prepare_inspect_source(request)
    inspect_id = str(uuid.uuid4())
    with inspect_jobs_lock:
        inspect_jobs[inspect_id] = {
            "id": inspect_id,
            "status": "queued",
            "logs": [],
            "media": None,
            "error": None,
            "source": source,
            "started_at": now(),
            "last_activity_at": now(),
        }
    asyncio.create_task(asyncio.to_thread(run_inspect, inspect_id, source))
    return inspect_job_record(inspect_id)


@app.get("/api/v1/downloads/inspect/{inspect_id}")
def get_inspect(inspect_id: str) -> dict[str, Any]:
    return inspect_job_record(inspect_id)


@app.get("/api/v1/settings/download")
def get_saved_download_settings() -> dict[str, Any]:
    return get_download_settings()


def export_backup() -> StreamingResponse:
    payload = export_backup_payload()
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    filename = f"video-downloader-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/v1/backup/preview")
async def preview_backup(request: FastAPIRequest) -> dict[str, Any]:
    content = await request.body()
    if not content or len(content) > BACKUP_MAX_BYTES:
        raise HTTPException(status_code=413, detail="备份文件为空或超过 25 MB。")
    try:
        raw_payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="备份文件不是有效的 JSON。") from error
    payload, summary = validate_backup_payload(raw_payload)
    preview_token = str(uuid.uuid4())
    current = time.monotonic()
    with backup_previews_lock:
        expired = [
            token
            for token, preview in backup_previews.items()
            if current - preview["created_monotonic"] > BACKUP_PREVIEW_TTL_SECONDS
        ]
        for token in expired:
            backup_previews.pop(token, None)
        backup_previews[preview_token] = {
            "payload": payload,
            "created_monotonic": current,
        }
    return {"preview_token": preview_token, "expires_in_seconds": BACKUP_PREVIEW_TTL_SECONDS, **summary}


@app.post("/api/v1/backup/restore")
def restore_backup(request: BackupRestoreRequest) -> dict[str, Any]:
    if not request.confirm:
        raise HTTPException(status_code=422, detail="恢复备份前必须明确确认。")
    with backup_previews_lock:
        preview = backup_previews.get(request.preview_token)
    if preview is None or time.monotonic() - preview["created_monotonic"] > BACKUP_PREVIEW_TTL_SECONDS:
        with backup_previews_lock:
            backup_previews.pop(request.preview_token, None)
        raise HTTPException(status_code=410, detail="备份预览已过期，请重新选择文件。")
    with download_creation_lock:
        result = restore_backup_payload(preview["payload"])
    with backup_previews_lock:
        backup_previews.pop(request.preview_token, None)
    events.publish({"type": "library-restored", "restored_at": result["restored_at"]})
    return result


@app.get("/api/v1/settings/yt-dlp/options")
def get_ytdlp_option_catalog() -> dict[str, Any]:
    return ytdlp_option_catalog()


@app.get("/api/v1/settings/yt-dlp/update-check")
def get_ytdlp_update_check(refresh: bool = False) -> dict[str, Any]:
    return check_ytdlp_update(force=refresh)


@app.put("/api/v1/settings/download")
def update_saved_download_settings(request: DownloadSettingsRequest) -> dict[str, Any]:
    return save_download_settings(request)


@app.put("/api/v1/settings/general")
def update_general_settings(request: GeneralSettingsRequest) -> dict[str, Any]:
    return save_general_settings(request)


def update_ytdlp_settings(request: YtDlpSettingsRequest) -> dict[str, Any]:
    return save_ytdlp_settings(request)


@app.put("/api/v1/settings/ffmpeg")
def update_ffmpeg_settings(request: EngineSettingsRequest) -> dict[str, Any]:
    return save_ffmpeg_settings(request)


@app.put("/api/v1/settings/aria2")
def update_aria2_settings(request: Aria2Settings) -> dict[str, Any]:
    return save_aria2_settings(request)


@app.put("/api/v1/settings/qbittorrent")
def update_qbittorrent_settings(request: QbittorrentSettings) -> dict[str, Any]:
    try:
        return save_qbittorrent_settings(request)
    except qbittorrent.QbittorrentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.put("/api/v1/settings/maintenance")
def update_maintenance_settings(request: MaintenanceSettings) -> dict[str, Any]:
    return save_maintenance_settings(request)


@app.get("/api/v1/maintenance/cleanup-preview")
def get_maintenance_cleanup_preview(
    retention_days: int = Query(default=30, ge=1, le=3650),
) -> dict[str, Any]:
    return maintenance_cleanup_preview(retention_days)


@app.post("/api/v1/maintenance/cleanup")
def run_saved_maintenance_cleanup(request: MaintenanceCleanupRequest) -> dict[str, Any]:
    return run_maintenance_cleanup(request)


@app.get("/api/v1/maintenance/incomplete")
def get_incomplete_residues() -> dict[str, Any]:
    return incomplete_residue_preview()


@app.post("/api/v1/maintenance/incomplete/cleanup")
def remove_incomplete_residues(request: IncompleteCleanupRequest) -> dict[str, Any]:
    return cleanup_incomplete_residues(request)


@app.get("/api/v1/maintenance/duplicates")
def get_duplicate_media() -> dict[str, Any]:
    return duplicate_media_preview()


@app.post("/api/v1/maintenance/duplicates/cleanup")
def remove_duplicate_media(request: DuplicateCleanupRequest) -> dict[str, Any]:
    return cleanup_duplicate_media(request)


def source_from_download_request(request: DownloadRequest) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if request.inspect_id:
        with inspect_jobs_lock:
            inspect_job = inspect_jobs.get(request.inspect_id)
            if inspect_job and inspect_job.get("status") == "completed":
                return inspect_job["source"], inspect_job.get("media")
        raise HTTPException(status_code=409, detail="下载内容尚未解析完成，请重新解析后再试。")
    source = prepare_inspect_source(InspectRequest(url=request.url, engine_hint=request.engine_hint))
    return source, None


def insert_engine_download(
    download_id: str,
    engine: str,
    engine_version: str,
    source_url: str,
    source_platform: str,
    source_type: str,
    resolved_url: str | None,
    download_dir: str,
    title: str | None = None,
    total_bytes: int | None = None,
    engine_task_id: str | None = None,
    engine_metadata: dict[str, Any] | None = None,
    priority: int = 0,
) -> None:
    timestamp = now()
    with connection() as database:
        database.execute(
            """
            INSERT INTO downloads (
                id, engine, engine_version, source_url, source_platform, source_type, resolved_url,
                engine_task_id, engine_metadata_json, status, title, total_bytes, download_dir, library_visible, priority, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                download_id,
                engine,
                engine_version,
                source_url,
                source_platform,
                source_type,
                resolved_url,
                engine_task_id,
                json.dumps(engine_metadata or {}, ensure_ascii=False),
                "queued",
                title,
                total_bytes,
                download_dir,
                False,
                priority,
                timestamp,
                timestamp,
            ),
        )


@app.post("/api/v1/downloads", status_code=201)
def create_download(request: DownloadRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    source, media = source_from_download_request(request)
    if media is None:
        try:
            media = inspect_source(source)
        except DownloadError as error:
            raise HTTPException(status_code=422, detail=friendly_inspect_error(error)) from error
        except (InputError, aria2.Aria2Error, qbittorrent.QbittorrentError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    route = source["route"]
    upgrade_target: dict[str, Any] | None = None
    if request.upgrade_from_id:
        upgrade_target = download_record(request.upgrade_from_id)
        if (
            upgrade_target.get("status") != "completed"
            or upgrade_target.get("file_origin") != "downloaded"
            or not upgrade_target.get("file_exists")
            or route.get("engine") != "yt-dlp"
        ):
            raise HTTPException(status_code=409, detail="只能升级仍有本地文件的已完成在线视频。")
    selected_file_indexes = sorted(set(request.selected_file_indexes or [])) if request.selected_file_indexes is not None else None
    is_bt = route["source_type"] in {"magnet", "torrent_url", "torrent_file", "thunder_bt"}
    bt_info_hash: str | None = None
    if is_bt:
        if not isinstance(media, dict) or media.get("kind") != "torrent":
            raise HTTPException(status_code=422, detail="BT 文件列表尚未解析完成，请重新解析后再试。")
        available_files = media.get("files") if isinstance(media.get("files"), list) else []
        media_indexes = {
            item.get("index")
            for item in available_files
            if isinstance(item, dict)
            and isinstance(item.get("index"), int)
            and isinstance(item.get("path"), str)
            and is_media_file(item["path"])
        }
        if not media_indexes:
            raise HTTPException(status_code=422, detail="该 BT 任务中没有可下载的视频或音频文件。")
        if selected_file_indexes is None:
            selected_file_indexes = sorted(media_indexes)
        if not selected_file_indexes:
            raise HTTPException(status_code=422, detail="请至少选择一个视频或音频文件再开始下载。")
        if not set(selected_file_indexes).issubset(media_indexes):
            raise HTTPException(status_code=422, detail="只能选择视频或音频文件，其他文件类型不允许下载。")
        candidate_info_hash = source.get("bt_info_hash") or media.get("info_hash")
        if isinstance(candidate_info_hash, str) and re.fullmatch(r"[0-9a-fA-F]{40}", candidate_info_hash):
            bt_info_hash = candidate_info_hash.lower()
        if bt_info_hash is None:
            bt_info_hash = magnet_info_hash(route.get("resolved_url") or "")
    elif selected_file_indexes is not None:
        raise HTTPException(status_code=422, detail="只有已解析出文件列表的 BT 任务可以选择文件。")
    expected_size = (media or {}).get("file_size") if isinstance((media or {}).get("file_size"), int) else None
    if selected_file_indexes is not None and isinstance(media, dict):
        expected_size = sum(
            item.get("size") or 0
            for item in media.get("files", [])
            if isinstance(item, dict) and item.get("index") in selected_file_indexes and isinstance(item.get("size"), int)
        ) or None
    source_url = source.get("url") or f"torrent-file:{source.get('torrent_name') or 'upload'}"
    if upgrade_target is not None:
        target_urls = {
            str(value)
            for value in (upgrade_target.get("source_url"), upgrade_target.get("webpage_url"), upgrade_target.get("resolved_url"))
            if value
        }
        requested_urls = {
            str(value)
            for value in (source_url, route.get("resolved_url"))
            if value
        }
        if not target_urls.intersection(requested_urls):
            raise HTTPException(status_code=409, detail="升级来源与原视频不一致，已停止创建任务。")
    settings = get_download_settings()
    aria2_task_settings = {
        **settings["aria2"],
        "download_rate_limit_kbps": settings["download_rate_limit_kbps"],
    }
    qbit_task_settings = {
        **settings["qbittorrent"],
        "download_rate_limit_kbps": settings["download_rate_limit_kbps"],
    }
    base_download_dir = str(resolve_download_dir(request.download_dir)) if request.download_dir else settings["download_dir"]
    aria2_task_settings["dht_state_path"] = str(
        Path(base_download_dir) / ".video-downloader" / "aria2-dht.dat"
    )
    write_thumbnail = settings["write_thumbnail"] if request.write_thumbnail is None else request.write_thumbnail
    write_info_json = settings["write_info_json"] if request.write_info_json is None else request.write_info_json
    download_id = str(uuid.uuid4())
    aria2_file_name: str | None = None
    if route["engine"] == "yt-dlp":
        source_platform = platform_name(source_url)
    elif is_bt:
        source_platform = "BT 下载"
    elif route["source_type"] == "thunder":
        source_platform = "迅雷链接"
    else:
        source_platform = platform_name(route["resolved_url"])
    directory_title = str(
        (media or {}).get("title")
        or (media or {}).get("file_name")
        or "未命名内容"
    )
    download_dir = formatted_download_dir(
        base_download_dir,
        settings["directory_pattern"],
        platform=source_platform,
        uploader=(media or {}).get("uploader"),
        title=directory_title,
    )
    ensure_disk_capacity(
        download_dir,
        expected_size,
        minimum_free_space_mb=settings["minimum_free_space_mb"],
    )
    with download_creation_lock:
        completed_ids = completed_download_ids(
            source_url,
            route.get("resolved_url"),
            (media or {}).get("webpage_url"),
            (media or {}).get("video_id"),
            source_platform,
        )
        if is_bt:
            completed_ids = list(dict.fromkeys([
                *completed_ids,
                *matching_bt_download_ids(
                    bt_info_hash,
                    selected_file_indexes,
                    ("completed",),
                    require_completed_output=True,
                ),
            ]))
        if completed_ids and upgrade_target is None:
            raise HTTPException(status_code=409, detail="这个视频已下载，可在视频管理中查看。")
        existing_ids = active_download_ids(source_url)
        if is_bt:
            existing_ids = list(dict.fromkeys([
                *existing_ids,
                *matching_bt_download_ids(
                    bt_info_hash,
                    selected_file_indexes,
                    ("queued", "running", "processing", "paused"),
                ),
            ]))
        if existing_ids and not request.replace_existing:
            raise HTTPException(status_code=409, detail="这个链接正在下载中。")
        for existing_id in existing_ids:
            cancel_download(existing_id, "已取消当前下载，准备重新开始。")

        if route["engine"] == "yt-dlp":
            global_ytdlp_config = combine_ytdlp_config(
                ytdlp_simple_config(settings["yt_dlp_simple"]), settings["yt_dlp_config"]
            )
            yt_dlp_config = combine_ytdlp_config(global_ytdlp_config, request.yt_dlp_config)
            if settings["download_rate_limit_kbps"] > 0:
                yt_dlp_config = combine_ytdlp_config(
                    yt_dlp_config,
                    f"--limit-rate {settings['download_rate_limit_kbps']}K",
                )
            ffmpeg_config = combine_ytdlp_config(settings["ffmpeg_config"], request.ffmpeg_config)
            parse_ytdlp_config(yt_dlp_config)
            ffmpeg_config_tokens(ffmpeg_config)
            timestamp = now()
            with connection() as database:
                database.execute(
                    """
                    INSERT INTO downloads (
                        id, engine, engine_version, source_url, source_platform, source_type, resolved_url, requested_format, status,
                        download_dir, write_thumbnail, write_info_json, yt_dlp_config, ffmpeg_config, priority, upgrade_from_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        download_id, "yt-dlp", yt_dlp.version.__version__, source_url, source_platform, "webpage",
                        route["resolved_url"], request.format_id, "queued", download_dir, write_thumbnail, write_info_json,
                        yt_dlp_config, ffmpeg_config, request.priority, request.upgrade_from_id, timestamp, timestamp,
                    ),
                )
        elif route["engine"] == "aria2":
            if not aria2.executable():
                raise HTTPException(status_code=409, detail="未找到 aria2c，无法下载直链、迅雷链接或 BT 资源。")
            title = str((media or {}).get("file_name") or (media or {}).get("title") or "未命名文件")
            aria2_file_name = title if is_bt else direct_download_file_name(download_dir, title, download_id)
            engine_metadata: dict[str, Any] = {}
            if source.get("torrent_data"):
                engine_metadata["torrent_base64"] = base64.b64encode(source["torrent_data"]).decode()
            if selected_file_indexes is not None:
                engine_metadata["selected_file_indexes"] = selected_file_indexes
            if bt_info_hash:
                engine_metadata["bt_info_hash"] = bt_info_hash
            engine_metadata["aria2_settings"] = aria2_task_settings
            insert_engine_download(
                download_id, "aria2", aria2.version() or "未知版本", source_url,
                source_platform,
                route["source_type"], route["resolved_url"], download_dir, aria2_file_name,
                expected_size,
                engine_metadata=engine_metadata,
                priority=request.priority,
            )
        elif route["engine"] == "qbittorrent":
            qbit_settings = qbit_task_settings
            if not qbit_settings["enabled"]:
                raise HTTPException(status_code=409, detail="请先在设置中启用并连接 qBittorrent。")
            try:
                qbit_version = qbittorrent_client(qbit_settings).app_version()
            except qbittorrent.QbittorrentError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            engine_metadata = {}
            if source.get("torrent_data"):
                engine_metadata["torrent_base64"] = base64.b64encode(source["torrent_data"]).decode()
            if selected_file_indexes is not None:
                engine_metadata["selected_file_indexes"] = selected_file_indexes
            if bt_info_hash:
                engine_metadata["bt_info_hash"] = bt_info_hash
            insert_engine_download(
                download_id,
                "qbittorrent",
                qbit_version,
                source_url,
                source_platform,
                route["source_type"],
                route["resolved_url"],
                download_dir,
                str((media or {}).get("title") or "BT 下载任务"),
                expected_size,
                engine_metadata=engine_metadata,
                priority=request.priority,
            )

    if route["engine"] == "yt-dlp":
        append_log(download_id, "info", f"yt-dlp 下载已开始，将保存到：{download_dir}")
        if yt_dlp_config:
            append_log(download_id, "info", f"已应用 {len(ytdlp_config_tokens(yt_dlp_config))} 个 yt-dlp 参数。")
        if ffmpeg_config:
            append_log(download_id, "info", f"已应用 {len(ffmpeg_config_tokens(ffmpeg_config))} 个 ffmpeg 参数。")
        background_tasks.add_task(
            submit_download_task,
            run_download,
            download_id,
            route["resolved_url"],
            request.format_id,
            download_dir,
            write_thumbnail,
            write_info_json,
            yt_dlp_config,
            ffmpeg_config,
            priority=request.priority,
        )
    elif route["engine"] == "aria2":
        task_label = "BT" if route["source_type"] in {"magnet", "torrent_url", "torrent_file", "thunder_bt"} else ("迅雷" if route["source_type"] == "thunder" else "直链")
        append_log(download_id, "info", f"已识别为 {task_label} 下载，将由 aria2 保存到：{download_dir}")
        background_tasks.add_task(
            submit_download_task,
            run_aria2_task, download_id, route["resolved_url"], download_dir, route["source_type"], aria2_file_name,
            expected_size,
            source.get("torrent_data"),
            selected_file_indexes,
            update_download, append_log, publish_download, download_was_cancelled, register_engine_process,
            aria2_task_settings,
            register_download_media_outputs,
            download_is_paused,
            download_is_interrupted,
            priority=request.priority,
        )
    elif route["engine"] == "qbittorrent":
        append_log(download_id, "info", f"已识别为 BT 下载，将由 qBittorrent 保存到：{download_dir}")
        background_tasks.add_task(
            submit_download_task,
            run_qbittorrent_task,
            download_id,
            route["resolved_url"],
            download_dir,
            source.get("torrent_data"),
            selected_file_indexes,
            None,
            qbit_task_settings,
            update_download,
            append_log,
            publish_download,
            download_was_cancelled,
            register_download_media_outputs,
            download_is_paused,
            download_is_interrupted,
            priority=request.priority,
        )
    return download_record(download_id)


@app.post("/api/v1/downloads/batch", status_code=201)
def create_download_batch(
    request: BatchDownloadRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    def create_one(index: int, item: DownloadRequest) -> dict[str, Any]:
        item_background_tasks = BackgroundTasks()
        try:
            download = create_download(item, item_background_tasks)
            return {
                "index": index,
                "url": item.url,
                "download": download,
                "background_tasks": item_background_tasks,
            }
        except HTTPException as error:
            return {
                "index": index,
                "url": item.url,
                "error": str(error.detail),
            }
        except Exception as error:
            return {
                "index": index,
                "url": item.url,
                "error": normalize_message(str(error)),
            }

    with ThreadPoolExecutor(
        max_workers=min(MAX_CONCURRENT_DOWNLOADS, len(request.items)),
        thread_name_prefix="batch-create",
    ) as executor:
        results = list(executor.map(
            lambda indexed_item: create_one(*indexed_item),
            enumerate(request.items),
        ))

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for result in results:
        item_background_tasks = result.pop("background_tasks", None)
        if isinstance(item_background_tasks, BackgroundTasks):
            for task in item_background_tasks.tasks:
                background_tasks.add_task(task.func, *task.args, **task.kwargs)
            successes.append(result)
        else:
            failures.append(result)

    return {
        "created": len(successes),
        "failed": len(failures),
        "successes": successes,
        "failures": failures,
    }


@app.post("/api/v1/playlists", status_code=201)
def create_playlist_download(
    request: PlaylistDownloadRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    with inspect_jobs_lock:
        inspect_job = inspect_jobs.get(request.inspect_id)
        media = inspect_job.get("media") if inspect_job else None
    if not inspect_job or inspect_job.get("status") != "completed" or not isinstance(media, dict):
        raise HTTPException(status_code=409, detail="播放列表解析尚未完成，请重新解析后再试。")
    if media.get("kind") != "playlist":
        raise HTTPException(status_code=422, detail="这个链接不是可下载的播放列表。")

    available_entries = [entry for entry in media.get("entries", []) if isinstance(entry, dict)]
    if not available_entries:
        raise HTTPException(status_code=422, detail="播放列表中没有可下载的视频。")

    available_urls = {
        entry.get("webpage_url")
        for entry in available_entries
        if isinstance(entry.get("webpage_url"), str)
    }
    selected_urls = set(request.entry_urls) if request.entry_urls is not None else available_urls
    if not selected_urls:
        raise HTTPException(status_code=422, detail="请至少选择一个视频再开始下载。")
    if not selected_urls.issubset(available_urls):
        raise HTTPException(status_code=422, detail="选择的视频已变化，请重新解析播放列表。")
    entries = [
        entry for entry in available_entries
        if entry.get("webpage_url") in selected_urls
    ]

    settings = get_download_settings()
    global_ytdlp_config = combine_ytdlp_config(
        ytdlp_simple_config(settings["yt_dlp_simple"]),
        settings["yt_dlp_config"],
    )
    # Each playlist entry is a separate task. Force yt-dlp to stay on that one
    # entry even when the user's general settings contain --yes-playlist.
    playlist_ytdlp_config = combine_ytdlp_config(global_ytdlp_config, "--no-playlist")
    if settings["download_rate_limit_kbps"] > 0:
        playlist_ytdlp_config = combine_ytdlp_config(
            playlist_ytdlp_config,
            f"--limit-rate {settings['download_rate_limit_kbps']}K",
        )
    parse_ytdlp_config(playlist_ytdlp_config)
    ffmpeg_config = settings["ffmpeg_config"]
    ffmpeg_config_tokens(ffmpeg_config)
    source_urls = [entry["webpage_url"] for entry in entries if isinstance(entry.get("webpage_url"), str)]
    if len(source_urls) != len(entries):
        raise HTTPException(status_code=422, detail="播放列表里有视频缺少可下载链接，请重新解析后再试。")

    playlist_id = str(uuid.uuid4())
    timestamp = now()
    task_ids: list[str] = []
    task_download_dirs: list[str] = []
    with download_creation_lock:
        completed_urls = [
            source_url
            for source_url in source_urls
            if completed_download_ids(source_url, webpage_url=source_url)
        ]
        if completed_urls:
            raise HTTPException(status_code=409, detail="播放列表中有视频已下载，可在视频管理中查看。")
        existing_by_url = {
            source_url: active_download_ids(source_url)
            for source_url in source_urls
        }
        existing_ids = [download_id for ids in existing_by_url.values() for download_id in ids]
        if existing_ids and not request.replace_existing:
            raise HTTPException(status_code=409, detail="播放列表中有视频正在下载，避免重复下载。")
        for existing_id in existing_ids:
            cancel_download(existing_id, "已取消当前下载，准备重新开始。")

        with connection() as database:
            database.execute(
                """
                INSERT INTO playlists (
                    id, source_url, source_platform, external_id, title, uploader, thumbnail,
                    description, total_count, source_total_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    playlist_id,
                    media.get("source_url") or source_urls[0],
                    media.get("source_platform") or platform_name(source_urls[0]),
                    media.get("external_id"),
                    media.get("title") or "未命名播放列表",
                    media.get("uploader"),
                    media.get("thumbnail"),
                    media.get("description"),
                    len(entries),
                    media.get("source_total_count") or len(available_entries),
                    timestamp,
                    timestamp,
                ),
            )
            for offset, entry in enumerate(entries, start=1):
                download_id = str(uuid.uuid4())
                task_ids.append(download_id)
                entry_platform = platform_name(entry["webpage_url"])
                entry_download_dir = formatted_download_dir(
                    settings["download_dir"],
                    settings["directory_pattern"],
                    platform=entry_platform,
                    uploader=entry.get("uploader") or media.get("uploader"),
                    title=entry.get("title"),
                )
                entry_size = entry.get("file_size") or entry.get("filesize") or entry.get("filesize_approx")
                ensure_disk_capacity(
                    entry_download_dir,
                    int(entry_size) if isinstance(entry_size, (int, float)) else None,
                    minimum_free_space_mb=settings["minimum_free_space_mb"],
                )
                task_download_dirs.append(entry_download_dir)
                database.execute(
                    """
                    INSERT INTO downloads (
                        id, engine, engine_version, source_url, source_platform, requested_format, status,
                        title, uploader, thumbnail, webpage_url,
                        download_dir, write_thumbnail, write_info_json, yt_dlp_config, ffmpeg_config,
                        playlist_id, playlist_index, priority, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        download_id,
                        "yt-dlp",
                        yt_dlp.version.__version__,
                        entry["webpage_url"],
                        entry_platform,
                        request.format_id,
                        "queued",
                        entry.get("title"),
                        entry.get("uploader") or media.get("uploader"),
                        entry.get("thumbnail"),
                        entry["webpage_url"],
                        entry_download_dir,
                        settings["write_thumbnail"],
                        settings["write_info_json"],
                        playlist_ytdlp_config,
                        ffmpeg_config,
                        playlist_id,
                        entry.get("playlist_index") or offset,
                        request.priority,
                        timestamp,
                        timestamp,
                    ),
                )

    for offset, (download_id, entry, entry_download_dir) in enumerate(
        zip(task_ids, entries, task_download_dirs, strict=True),
        start=1,
    ):
        append_log(download_id, "info", f"播放列表“{media.get('title') or '未命名播放列表'}”第 {offset} 个视频，等待下载。")
        background_tasks.add_task(
            submit_download_task,
            run_download,
            download_id,
            entry["webpage_url"],
            request.format_id,
            entry_download_dir,
            settings["write_thumbnail"],
            settings["write_info_json"],
            playlist_ytdlp_config,
            ffmpeg_config,
            priority=request.priority,
        )
    return playlist_record(playlist_id)


def existing_download_task(record: dict[str, Any]) -> tuple[Any, tuple[Any, ...]]:
    download_id = record["id"]
    if record["engine"] == "yt-dlp":
        return run_download, (
            download_id,
            record["resolved_url"] or record["source_url"],
            record["requested_format"],
            record["download_dir"] or str(DOWNLOAD_DIR),
            bool(record["write_thumbnail"]),
            bool(record["write_info_json"]),
            record["yt_dlp_config"] or "",
            record["ffmpeg_config"] or "",
        )
    if record["engine"] == "aria2":
        engine_metadata = download_engine_metadata(download_id)
        torrent_data = None
        encoded_torrent = engine_metadata.get("torrent_base64")
        if isinstance(encoded_torrent, str):
            try:
                torrent_data = base64.b64decode(encoded_torrent, validate=True)
            except ValueError as error:
                raise HTTPException(status_code=409, detail="已保存的 BT 元数据已损坏，请重新添加链接。") from error
        elif record.get("source_type") == "torrent_file":
            raise HTTPException(status_code=409, detail="原始种子文件已不可用，请重新添加该 .torrent 文件。")
        selected_file_indexes = engine_metadata.get("selected_file_indexes")
        if not isinstance(selected_file_indexes, list) or not all(isinstance(index, int) and index >= 0 for index in selected_file_indexes):
            selected_file_indexes = None
        try:
            saved_aria2_settings = engine_metadata.get("aria2_settings")
            aria2_settings = Aria2Settings.model_validate(saved_aria2_settings).model_dump()
            if isinstance(saved_aria2_settings, dict):
                aria2_settings["download_rate_limit_kbps"] = max(
                    0,
                    int(saved_aria2_settings.get("download_rate_limit_kbps", 0)),
                )
                saved_dht_state_path = saved_aria2_settings.get("dht_state_path")
                if isinstance(saved_dht_state_path, str) and saved_dht_state_path:
                    aria2_settings["dht_state_path"] = saved_dht_state_path
            aria2_settings.setdefault(
                "dht_state_path",
                str(DATA_DIR / ".video-downloader" / "aria2-dht.dat"),
            )
        except Exception:
            current_settings = get_download_settings()
            aria2_settings = {
                **current_settings["aria2"],
                "download_rate_limit_kbps": current_settings["download_rate_limit_kbps"],
                "dht_state_path": str(DATA_DIR / ".video-downloader" / "aria2-dht.dat"),
            }
        return run_aria2_task, (
            download_id,
            record["resolved_url"] or record["source_url"],
            record["download_dir"] or str(DOWNLOAD_DIR),
            record["source_type"] or "direct",
            record["title"] or "未命名文件",
            record["total_bytes"],
            torrent_data,
            selected_file_indexes,
            update_download,
            append_log,
            publish_download,
            download_was_cancelled,
            register_engine_process,
            aria2_settings,
            register_download_media_outputs,
            download_is_paused,
            download_is_interrupted,
        )
    if record["engine"] == "qbittorrent":
        engine_metadata = download_engine_metadata(download_id)
        torrent_data = None
        encoded_torrent = engine_metadata.get("torrent_base64")
        if isinstance(encoded_torrent, str):
            try:
                torrent_data = base64.b64decode(encoded_torrent, validate=True)
            except ValueError as error:
                raise HTTPException(status_code=409, detail="已保存的 BT 元数据已损坏，请重新添加链接。") from error
        selected_file_indexes = engine_metadata.get("selected_file_indexes")
        if not isinstance(selected_file_indexes, list) or not all(isinstance(index, int) and index >= 0 for index in selected_file_indexes):
            selected_file_indexes = None
        current_settings = get_download_settings()
        qbit_settings = {
            **current_settings["qbittorrent"],
            "download_rate_limit_kbps": current_settings["download_rate_limit_kbps"],
        }
        if not qbit_settings["enabled"]:
            raise HTTPException(status_code=409, detail="qBittorrent 已停用，无法继续这个任务。")
        return run_qbittorrent_task, (
            download_id,
            record["resolved_url"] or "",
            record["download_dir"] or str(DOWNLOAD_DIR),
            torrent_data,
            selected_file_indexes,
            record.get("engine_task_id"),
            qbit_settings,
            update_download,
            append_log,
            publish_download,
            download_was_cancelled,
            register_download_media_outputs,
            download_is_paused,
            download_is_interrupted,
        )
    raise HTTPException(status_code=409, detail="这个任务使用了未知下载引擎，无法继续。")


def recover_restart_pending_downloads() -> None:
    with connection() as database:
        rows = database.execute(
            """
            SELECT id FROM downloads
            WHERE restart_pending = 1 AND status = 'interrupted' AND task_deleted = 0
            ORDER BY created_at ASC
            """
        ).fetchall()
    for row in rows:
        download_id = row["id"]
        try:
            record = download_record(download_id)
            task, args = existing_download_task(record)
            update_download(download_id, status="queued", restart_pending=0, error=None, speed=None, eta=None)
            append_log(download_id, "info", "应用已重新启动，正在从已有文件恢复下载。")
            submit_download_task(task, *args, priority=int(record.get("priority") or 0))
        except HTTPException as error:
            update_download(download_id, status="failed", restart_pending=0, error=str(error.detail), speed=None, eta=None)
            append_log(download_id, "error", f"重启恢复失败：{error.detail}")
            publish_download(download_id)
        except Exception as error:
            message = normalize_message(str(error))
            update_download(download_id, status="failed", restart_pending=0, error=message, speed=None, eta=None)
            append_log(download_id, "error", f"重启恢复异常：{message}")
            publish_download(download_id)


@app.post("/api/v1/downloads/{download_id}/retry")
def retry_download(download_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    with download_creation_lock:
        record = download_record(download_id)
        if record["status"] not in {"failed", "interrupted", "cancelled", "paused"}:
            raise HTTPException(status_code=409, detail="只能继续已暂停、已取消、已中断或失败的下载。")
        if active_download_ids(record["source_url"], excluded_id=download_id):
            raise HTTPException(status_code=409, detail="这个链接正在下载中。")
        task, args = existing_download_task(record)
        clear_download_cancellation(download_id)
        update_download(download_id, status="queued", error=None, speed=None, eta=None, restart_pending=0)
    append_log(download_id, "info", "正在继续下载；找到未完成文件时会从断点继续，否则重新下载。")
    background_tasks.add_task(
        submit_download_task,
        task,
        *args,
        priority=int(record.get("priority") or 0),
    )
    return download_record(download_id)


@app.post("/api/v1/downloads/{download_id}/pause")
def pause_active_download(download_id: str) -> dict[str, Any]:
    with download_creation_lock:
        record = download_record(download_id)
        if record["status"] not in {"queued", "running", "processing"}:
            raise HTTPException(status_code=409, detail="只能暂停等待中或正在处理的下载。")
        update_download(download_id, status="paused", error=None, speed=None, eta=None, restart_pending=0)
        with engine_processes_lock:
            process = engine_processes.get(download_id)
        if process is not None and process.poll() is None:
            process.terminate()
        append_log(download_id, "info", "下载已暂停；已下载部分会保留，之后可以继续。")
        publish_download(download_id)
    return download_record(download_id)


@app.post("/api/v1/downloads/{download_id}/cancel")
def cancel_active_download(download_id: str) -> dict[str, Any]:
    with download_creation_lock:
        record = download_record(download_id)
        if record["status"] not in {"queued", "running", "processing", "paused"}:
            raise HTTPException(status_code=409, detail="只能取消等待中、已暂停或正在处理的下载。")
        cancel_download(download_id, "已由用户取消下载。")
    return download_record(download_id)


@app.delete("/api/v1/downloads/{download_id}", response_model=None)
def delete_download_task(
    download_id: str, remove_files: bool = Query(default=True)
) -> dict[str, Any] | JSONResponse:
    with download_creation_lock:
        record = download_record(download_id)
        if record["status"] in {"queued", "running", "processing"}:
            raise HTTPException(status_code=409, detail="请先取消下载，再删除任务。")

        trashed_files: list[str] = []
        failed_files: list[dict[str, str]] = []
        if remove_files and record["status"] != "completed":
            for path in incomplete_task_files(record):
                try:
                    move_to_trash(path)
                    trashed_files.append(str(path))
                except RuntimeError as error:
                    failed_files.append({"path": str(path), "error": str(error)})
            if failed_files:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "部分未完成文件无法移入废纸篓，任务记录已保留。",
                        "trashed_files": trashed_files,
                        "failed_files": failed_files,
                    },
                )

        if record["engine"] == "qbittorrent" and record.get("engine_task_id"):
            try:
                qbittorrent_client().delete(str(record["engine_task_id"]), delete_files=False)
            except qbittorrent.QbittorrentError as error:
                raise HTTPException(
                    status_code=409,
                    detail=f"qBittorrent 任务尚未移除，任务记录已保留：{error}",
                ) from error

        with connection() as database:
            database.execute("DELETE FROM download_logs WHERE download_id = ?", (download_id,))
            database.execute("UPDATE downloads SET task_deleted = 1, updated_at = ? WHERE id = ?", (now(), download_id))
    events.publish({"type": "download_deleted", "download_id": download_id})
    return {"id": download_id, "trashed_files": trashed_files, "failed_files": []}


@app.get("/api/v1/downloads")
def list_downloads(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    page: int | None = None,
    page_size: int = 10,
    task_filter: str = "all",
) -> Any:
    if page is not None:
        if page < 1 or page_size < 1 or page_size > 200:
            raise HTTPException(status_code=422, detail="分页参数无效。")
        filter_conditions = {
            "all": "status != 'completed'",
            "active": "status IN ('queued', 'running', 'processing')",
            "attention": "status IN ('failed', 'interrupted', 'cancelled', 'paused')",
        }
        condition = filter_conditions.get(task_filter)
        if condition is None:
            raise HTTPException(status_code=422, detail="下载任务筛选无效。")

        with connection() as database:
            counts_row = database.execute(
                """
                SELECT
                    COUNT(*) AS all_count,
                    SUM(CASE WHEN status IN ('queued', 'running', 'processing') THEN 1 ELSE 0 END) AS active_count,
                    SUM(CASE WHEN status IN ('failed', 'interrupted', 'cancelled', 'paused') THEN 1 ELSE 0 END) AS attention_count
                FROM downloads
                WHERE task_deleted = 0 AND status != 'completed'
                """
            ).fetchone()
            total = database.execute(
                f"SELECT COUNT(*) FROM downloads WHERE task_deleted = 0 AND {condition}"
            ).fetchone()[0]
            rows = database.execute(
                f"""
                SELECT id
                FROM downloads
                WHERE task_deleted = 0 AND {condition}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()

        return {
            "items": [download_record(row["id"]) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "counts": {
                "all": counts_row["all_count"] or 0,
                "active": counts_row["active_count"] or 0,
                "attention": counts_row["attention_count"] or 0,
            },
        }

    query = "SELECT * FROM downloads WHERE task_deleted = 0 AND status != 'completed'"
    values: list[Any] = []
    if status:
        query += " AND status = ?"
        values.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    values.append(limit)

    with connection() as database:
        rows = database.execute(query, values).fetchall()

    return [download_record(row["id"]) for row in rows]


@app.post("/api/v1/downloads/progress")
def get_download_progress_batch(
    request: DownloadProgressRequest,
) -> dict[str, Any]:
    download_ids = list(dict.fromkeys(request.ids))
    placeholders = ", ".join("?" for _ in download_ids)
    with connection() as database:
        rows = database.execute(
            f"""
            SELECT
                id, status, title, thumbnail, progress, downloaded_bytes,
                total_bytes, speed, eta, error, resolution, updated_at
            FROM downloads
            WHERE task_deleted = 0 AND id IN ({placeholders})
            """,
            download_ids,
        ).fetchall()
        counts_row = database.execute(
            """
            SELECT
                COUNT(*) AS all_count,
                SUM(CASE WHEN status IN ('queued', 'running', 'processing') THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN status IN ('failed', 'interrupted', 'cancelled', 'paused') THEN 1 ELSE 0 END) AS attention_count
            FROM downloads
            WHERE task_deleted = 0 AND status != 'completed'
            """
        ).fetchone()

    rows_by_id = {row["id"]: dict(row) for row in rows}
    return {
        "items": [rows_by_id[download_id] for download_id in download_ids if download_id in rows_by_id],
        "counts": {
            "all": counts_row["all_count"] or 0,
            "active": counts_row["active_count"] or 0,
            "attention": counts_row["attention_count"] or 0,
        },
    }


@app.get("/api/v1/downloads/{download_id}")
def get_download(download_id: str) -> dict[str, Any]:
    return download_record(download_id, include_metadata=True)


@app.get("/api/v1/playlists")
def list_playlists() -> list[dict[str, Any]]:
    with connection() as database:
        rows = database.execute(
            """
            SELECT playlists.id, playlists.title, playlists.source_platform,
                   COUNT(downloads.id) AS item_count
            FROM playlists
            LEFT JOIN downloads ON downloads.playlist_id = playlists.id
            GROUP BY playlists.id
            ORDER BY playlists.title COLLATE NOCASE ASC, playlists.created_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/playlists/{playlist_id}")
def get_playlist(playlist_id: str) -> dict[str, Any]:
    return playlist_record(playlist_id)


@app.get("/api/v1/playlists/{playlist_id}/videos")
def list_playlist_videos(playlist_id: str) -> list[dict[str, Any]]:
    playlist_record(playlist_id)
    with connection() as database:
        rows = database.execute(
            """
            SELECT id
            FROM downloads
            WHERE playlist_id = ? AND status = 'completed'
            ORDER BY playlist_index ASC, created_at ASC
            """,
            (playlist_id,),
        ).fetchall()
    return [download_record(row["id"], include_metadata=True) for row in rows]


@app.put("/api/v1/playlists/{playlist_id}/favorite")
def update_playlist_favorite(
    playlist_id: str,
    request: VideoFavoriteRequest,
) -> dict[str, Any]:
    playlist_record(playlist_id)
    with connection() as database:
        database.execute(
            "UPDATE playlists SET favorite = ?, updated_at = ? WHERE id = ?",
            (int(request.favorite), now(), playlist_id),
        )
    return {"id": playlist_id, "favorite": int(request.favorite)}


@app.delete("/api/v1/playlists/{playlist_id}")
def delete_playlist(
    playlist_id: str, remove_files: bool = Query(default=True)
) -> dict[str, Any]:
    playlist_record(playlist_id)
    with connection() as database:
        rows = database.execute(
            "SELECT id FROM downloads WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()

    videos = [download_record(row["id"]) for row in rows]
    active_videos = [
        video for video in videos
        if video["status"] in {"queued", "running", "processing", "paused"}
    ]
    if active_videos:
        raise HTTPException(status_code=409, detail="合集内仍有未结束的下载，请先取消下载后再删除合集。")

    with connection() as database:
        for video in videos:
            active_denoise_job = database.execute(
                "SELECT id FROM video_denoise_jobs WHERE download_id = ? AND status IN ('queued', 'running')",
                (video["id"],),
            ).fetchone()
            active_media_job = database.execute(
                "SELECT id FROM media_derivative_jobs WHERE download_id = ? AND status IN ('queued', 'running')",
                (video["id"],),
            ).fetchone()
            if active_denoise_job or active_media_job:
                raise HTTPException(status_code=409, detail="合集内仍有正在处理的视频，请完成后再删除合集。")

    trashed_files: list[str] = []
    if remove_files:
        files_to_trash: list[Path] = []
        for video in videos:
            if video["status"] != "completed":
                files_to_trash.extend(incomplete_task_files(video))
                continue
            if not video.get("file_path"):
                continue
            try:
                media_path, download_dir = local_video_path(video)
            except HTTPException as error:
                if error.status_code == 404:
                    continue
                raise
            for file_path in related_video_files(media_path):
                try:
                    file_path.relative_to(download_dir)
                except ValueError as error:
                    raise HTTPException(status_code=409, detail="合集包含不安全的关联文件，未删除合集。") from error
                files_to_trash.append(file_path)

            derivative_dir = (download_dir / ".video-downloader" / "denoise").resolve()
            with connection() as database:
                denoise_rows = database.execute(
                    "SELECT file_path FROM video_denoise_jobs WHERE download_id = ?",
                    (video["id"],),
                ).fetchall()
            for denoise_row in denoise_rows:
                if not denoise_row["file_path"]:
                    continue
                derivative_path = Path(denoise_row["file_path"]).resolve()
                try:
                    derivative_path.relative_to(derivative_dir)
                except ValueError as error:
                    raise HTTPException(status_code=409, detail="合集包含不安全的历史副本，未删除合集。") from error
                if derivative_path.is_file():
                    files_to_trash.append(derivative_path)

        for file_path in dict.fromkeys(files_to_trash):
            try:
                move_to_trash(file_path)
                trashed_files.append(str(file_path))
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=f"部分本地文件未能移入废纸篓，合集记录已保留：{error}") from error

    for video in videos:
        if video["engine"] != "qbittorrent" or not video.get("engine_task_id"):
            continue
        try:
            qbittorrent_client().delete(str(video["engine_task_id"]), delete_files=False)
        except qbittorrent.QbittorrentError as error:
            raise HTTPException(status_code=409, detail=f"qBittorrent 任务尚未移除，合集记录已保留：{error}") from error

    with connection() as database:
        for video in videos:
            database.execute("DELETE FROM download_logs WHERE download_id = ?", (video["id"],))
            database.execute("DELETE FROM video_denoise_jobs WHERE download_id = ?", (video["id"],))
            database.execute("DELETE FROM media_derivative_jobs WHERE download_id = ?", (video["id"],))
            database.execute(
                """
                UPDATE media_derivative_jobs
                SET status = 'failed', file_path = NULL, file_size = NULL,
                    library_video_id = NULL, error = '兼容副本已从媒体库删除。', updated_at = ?
                WHERE library_video_id = ?
                """,
                (now(), video["id"]),
            )
            database.execute("UPDATE downloads SET upgrade_from_id = NULL WHERE upgrade_from_id = ?", (video["id"],))
            database.execute("DELETE FROM downloads WHERE id = ?", (video["id"],))
        database.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    for video in videos:
        if (
            video.get("file_origin") == "local"
            and video.get("thumbnail") == f"/api/v1/videos/{video['id']}/thumbnail"
        ):
            local_video_thumbnail_path(video["id"]).unlink(missing_ok=True)

    events.publish({"type": "playlist_deleted", "playlist_id": playlist_id})
    return {
        "id": playlist_id,
        "deleted_video_count": len(videos),
        "trashed_files": trashed_files,
    }


@app.get("/api/v1/downloads/{download_id}/logs")
def list_download_logs(download_id: str) -> list[dict[str, Any]]:
    download_record(download_id)
    with connection() as database:
        rows = database.execute(
            """
            SELECT id, level, message, created_at
            FROM download_logs
            WHERE download_id = ?
            ORDER BY id ASC
            """,
            (download_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/downloads/{download_id}/outputs")
def list_download_outputs(download_id: str) -> list[dict[str, Any]]:
    return download_output_files(download_id)


@app.get("/api/v1/downloads/{download_id}/outputs/{output_index}/file")
def get_download_output_file(download_id: str, output_index: int) -> FileResponse:
    output, output_path = resolve_download_output(download_id, output_index, playable_only=True)
    return FileResponse(output_path, media_type=output["file_type"])


@app.post("/api/v1/downloads/{download_id}/outputs/{output_index}/reveal")
def reveal_download_output(download_id: str, output_index: int) -> dict[str, Any]:
    _, output_path = resolve_download_output(download_id, output_index)
    reveal_in_finder(output_path)
    return {"id": download_id, "output_index": output_index}


def probe_local_media_file(file_path: Path) -> dict[str, Any]:
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        raise HTTPException(status_code=503, detail="未找到 ffprobe，无法分析本地媒体信息。")
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=500, detail=f"无法分析本地媒体：{error}") from error
    if result.returncode != 0:
        message = normalize_message(result.stderr)
        raise HTTPException(status_code=422, detail=message or "ffprobe 无法读取这个媒体文件。")
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="ffprobe 返回了无效的媒体信息。") from error

    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    video_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and not (stream.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    if video_stream is None and audio_stream is None:
        raise HTTPException(status_code=422, detail="文件中没有可识别的视频或音频流。")

    format_info = probe.get("format") if isinstance(probe.get("format"), dict) else {}

    def positive_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    selected_stream = video_stream or audio_stream
    duration = positive_number(format_info.get("duration"))
    if duration is None and selected_stream is not None:
        duration = positive_number(selected_stream.get("duration"))
    bit_rate = positive_number(
        (audio_stream or {}).get("bit_rate") or format_info.get("bit_rate")
    )

    resolution = None
    if video_stream is not None:
        width = video_stream.get("width")
        height = video_stream.get("height")
        if isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0:
            resolution = f"{width}x{height}"

    return {
        "media_type": "video" if video_stream is not None else "audio",
        "duration": duration,
        "resolution": resolution,
        "codec": (selected_stream or {}).get("codec_name"),
        "video_codec": (video_stream or {}).get("codec_name"),
        "audio_codec": (audio_stream or {}).get("codec_name"),
        "audio_track_count": len(audio_streams),
        "subtitle_track_count": len(subtitle_streams),
        "bit_rate": int(bit_rate) if bit_rate is not None else None,
        "format_name": format_info.get("format_name"),
    }


def local_media_metadata_values(
    file_path: Path, existing_metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    media_info = probe_local_media_file(file_path)
    metadata = dict(existing_metadata or {})
    metadata.update(
        {
            "media_type": media_info["media_type"],
            "codec": media_info.get("codec"),
            "bit_rate": media_info.get("bit_rate"),
            "format_name": media_info.get("format_name"),
        }
    )
    values: dict[str, Any] = {
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    if media_info.get("duration") is not None:
        values["duration"] = media_info["duration"]
    if media_info["media_type"] == "audio":
        values["resolution"] = None
    elif media_info.get("resolution") is not None:
        values["resolution"] = media_info["resolution"]
    return values


@cache
def external_player_availability() -> dict[str, bool]:
    players = {"system": sys.platform == "darwin", "iina": False, "vlc": False}
    if sys.platform != "darwin":
        return players
    for key, application in (("iina", "IINA"), ("vlc", "VLC")):
        try:
            result = subprocess.run(
                ["open", "-Ra", application],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        players[key] = result.returncode == 0
    return players


def video_compatibility(download_id: str) -> dict[str, Any]:
    video = library_video(download_id)
    media_path, _download_dir = local_video_path(video)
    media_info = probe_local_media_file(media_path)
    video_codec = str(media_info.get("video_codec") or "").lower()
    audio_codec = str(media_info.get("audio_codec") or "").lower()
    suffix = media_path.suffix.lower()
    compatible_video_codecs = {"h264", "avc1", "hevc", "h265"}
    compatible_audio_codecs = {"aac", "mp3", "alac", "ac3", "eac3"}
    codec_compatible = (
        video_codec in compatible_video_codecs
        and (not audio_codec or audio_codec in compatible_audio_codecs)
    )
    container_compatible = suffix in {".mp4", ".m4v", ".mov"}
    direct_play_likely = codec_compatible and container_compatible
    remux_available = codec_compatible and not container_compatible
    if direct_play_likely:
        reason = "容器和编码通常可由内置播放器直接播放。"
    elif remux_available:
        reason = "视频编码兼容，但当前封装不适合内置播放器；可快速生成 MP4 兼容副本。"
    elif video_codec:
        reason = f"内置播放器可能不支持 {video_codec.upper()} 视频编码，建议使用外部播放器。"
    else:
        reason = "无法确认视频编码，建议使用外部播放器。"
    return {
        "download_id": download_id,
        "file_name": media_path.name,
        "container": media_info.get("format_name") or suffix.lstrip("."),
        "video_codec": media_info.get("video_codec"),
        "audio_codec": media_info.get("audio_codec"),
        "audio_track_count": media_info.get("audio_track_count", 0),
        "subtitle_track_count": media_info.get("subtitle_track_count", 0),
        "direct_play_likely": direct_play_likely,
        "remux_available": remux_available,
        "reason": reason,
        "players": external_player_availability(),
    }


def open_video_in_external_player(
    download_id: str, request: ExternalPlayerRequest
) -> dict[str, Any]:
    video = library_video(download_id)
    media_path, _download_dir = local_video_path(video)
    if sys.platform != "darwin":
        raise HTTPException(status_code=409, detail="当前只支持在 macOS 中打开外部播放器。")
    commands = {
        "system": ["open", str(media_path)],
        "iina": ["open", "-a", "IINA", str(media_path)],
        "vlc": ["open", "-a", "VLC", str(media_path)],
    }
    if request.player != "system" and not external_player_availability().get(request.player):
        label = "IINA" if request.player == "iina" else "VLC"
        raise HTTPException(status_code=409, detail=f"本机未检测到 {label}。")
    try:
        process = subprocess.Popen(
            commands[request.player],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法打开外部播放器。") from error
    return {"download_id": download_id, "player": request.player, "pid": process.pid}


def media_derivative_job_record(job_id: str) -> dict[str, Any]:
    with connection() as database:
        row = database.execute(
            "SELECT * FROM media_derivative_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到这条兼容处理任务。")
    return dict(row)


def update_media_derivative_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = now()
    assignments = ", ".join(f"{column} = ?" for column in values)
    with connection() as database:
        database.execute(
            f"UPDATE media_derivative_jobs SET {assignments} WHERE id = ?",
            [*values.values(), job_id],
        )
    events.publish({"type": "media-derivative", "job": media_derivative_job_record(job_id)})


def compatibility_output_path(video: dict[str, Any]) -> Path:
    settings = get_download_settings()
    output_dir = Path(settings["download_dir"]) / "兼容版本"
    output_dir.mkdir(parents=True, exist_ok=True)
    title = safe_media_title(video.get("title") or "未命名视频")
    candidate = output_dir / f"{title}（兼容版）.mp4"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{title}（兼容版 {index}）.mp4"
        index += 1
    return candidate


def run_compatibility_remux(job_id: str, download_id: str) -> None:
    video = library_video(download_id)
    media_path, _download_dir = local_video_path(video)
    compatibility = video_compatibility(download_id)
    if not compatibility["remux_available"]:
        update_media_derivative_job(job_id, status="failed", error="当前文件不适合仅换封装处理。")
        return
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        update_media_derivative_job(job_id, status="failed", error="未找到 FFmpeg，无法生成兼容副本。")
        return
    output_path = compatibility_output_path(video)
    temporary_path = output_path.with_suffix(".part.mp4")
    try:
        ensure_disk_capacity(
            str(output_path.parent),
            media_path.stat().st_size,
            minimum_free_space_mb=get_download_settings()["minimum_free_space_mb"],
        )
        update_media_derivative_job(job_id, status="running", file_path=str(output_path), error=None)
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(media_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(temporary_path),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with media_job_processes_lock:
            media_job_processes[job_id] = process
        _stdout, stderr = process.communicate()
        if process.returncode != 0:
            message = normalize_message((stderr or b"").decode(errors="replace"))[-1200:]
            raise RuntimeError(message or "FFmpeg 兼容封装失败。")
        temporary_path.replace(output_path)
        media_info = probe_local_media_file(output_path)
        if media_info.get("media_type") != "video":
            raise RuntimeError("生成的兼容副本中没有可播放视频。")
        registered = register_local_video(
            LocalVideoRequest(
                file_path=str(output_path),
                title=f"{video.get('title') or '未命名视频'}（兼容版）",
            )
        )
        update_media_derivative_job(
            job_id,
            status="completed",
            file_path=str(output_path),
            file_size=output_path.stat().st_size,
            library_video_id=registered["id"],
            error=None,
        )
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        update_media_derivative_job(
            job_id,
            status="failed",
            error=normalize_message(str(error)) or "兼容副本生成失败。",
        )
    finally:
        with media_job_processes_lock:
            media_job_processes.pop(job_id, None)


def create_compatibility_remux(download_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    compatibility = video_compatibility(download_id)
    if not compatibility["remux_available"]:
        raise HTTPException(status_code=409, detail="当前文件不能只通过换封装生成兼容副本。")
    with connection() as database:
        active = database.execute(
            """
            SELECT id FROM media_derivative_jobs
            WHERE download_id = ? AND kind = 'compatibility_remux'
              AND status IN ('queued', 'running')
            """,
            (download_id,),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="这个视频正在生成兼容副本。")
        completed = database.execute(
            """
            SELECT file_path FROM media_derivative_jobs
            WHERE download_id = ? AND kind = 'compatibility_remux'
              AND status = 'completed' AND file_path IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (download_id,),
        ).fetchone()
        if completed is not None and Path(completed["file_path"]).is_file():
            raise HTTPException(status_code=409, detail="这个视频已经生成了兼容副本。")
        job_id = str(uuid.uuid4())
        timestamp = now()
        database.execute(
            """
            INSERT INTO media_derivative_jobs (
                id, download_id, kind, status, created_at, updated_at
            ) VALUES (?, ?, 'compatibility_remux', 'queued', ?, ?)
            """,
            (job_id, download_id, timestamp, timestamp),
        )
    background_tasks.add_task(run_compatibility_remux, job_id, download_id)
    return media_derivative_job_record(job_id)


def register_download_media_outputs(download_id: str) -> list[dict[str, Any]]:
    parent = download_record(download_id, include_metadata=True)
    playable_outputs = [
        output for output in download_output_files(download_id)
        if output["playable"]
    ]
    if not playable_outputs:
        return []
    if len(playable_outputs) == 1:
        output = playable_outputs[0]
        _, file_path = resolve_download_output(download_id, output["index"], playable_only=True)
        try:
            values = local_media_metadata_values(
                file_path,
                parent.get("metadata") if isinstance(parent.get("metadata"), dict) else {},
            )
        except (HTTPException, OSError):
            return []
        values.update(
            {
                "file_path": str(file_path),
                "file_size": file_path.stat().st_size,
                "library_visible": 1,
            }
        )
        update_download(download_id, **values)
        publish_download(download_id)
        return [download_record(download_id, include_metadata=True)]

    indexed_outputs: list[dict[str, Any]] = []
    for output in playable_outputs:
        _, file_path = resolve_download_output(download_id, output["index"], playable_only=True)
        try:
            media_info = probe_local_media_file(file_path)
        except HTTPException:
            media_info = {
                "media_type": "video" if file_path.suffix.lower() in VIDEO_EXTENSIONS else "audio",
                "duration": None,
                "resolution": None,
                "codec": None,
                "bit_rate": None,
                "format_name": file_path.suffix.lower().lstrip(".") or None,
            }
        indexed_outputs.append({"output": output, "file_path": file_path, "media_info": media_info})

    timestamp = now()
    parent_metadata = parent.get("metadata") if isinstance(parent.get("metadata"), dict) else {}
    output_indexes = [item["output"]["index"] for item in indexed_outputs]
    placeholders = ", ".join("?" for _ in output_indexes)
    with connection() as database:
        database.execute(
            f"""
            UPDATE downloads
            SET library_visible = 0, updated_at = ?
            WHERE parent_download_id = ?
              AND parent_output_index NOT IN ({placeholders})
            """,
            [timestamp, download_id, *output_indexes],
        )
        for item in indexed_outputs:
            output = item["output"]
            file_path = item["file_path"]
            media_info = item["media_info"]
            file_size = file_path.stat().st_size
            child_id = f"{download_id}-output-{output['index']}"
            metadata = {
                **parent_metadata,
                "media_type": media_info["media_type"],
                "codec": media_info["codec"],
                "bit_rate": media_info["bit_rate"],
                "format_name": media_info["format_name"],
            }
            database.execute(
                """
                INSERT INTO downloads (
                    id, engine, engine_version, source_url, source_platform, source_type, resolved_url,
                    status, title, uploader, thumbnail, webpage_url, duration, resolution,
                    file_path, file_size, progress, downloaded_bytes, total_bytes, metadata_json,
                    download_dir, library_visible, task_deleted, file_origin,
                    parent_download_id, parent_output_index, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(parent_download_id, parent_output_index) DO UPDATE SET
                    engine = excluded.engine,
                    engine_version = excluded.engine_version,
                    source_url = excluded.source_url,
                    source_platform = excluded.source_platform,
                    source_type = excluded.source_type,
                    resolved_url = excluded.resolved_url,
                    status = excluded.status,
                    title = excluded.title,
                    uploader = excluded.uploader,
                    thumbnail = excluded.thumbnail,
                    webpage_url = excluded.webpage_url,
                    duration = excluded.duration,
                    resolution = excluded.resolution,
                    file_path = excluded.file_path,
                    file_size = excluded.file_size,
                    progress = excluded.progress,
                    downloaded_bytes = excluded.downloaded_bytes,
                    total_bytes = excluded.total_bytes,
                    metadata_json = excluded.metadata_json,
                    download_dir = excluded.download_dir,
                    library_visible = excluded.library_visible,
                    task_deleted = excluded.task_deleted,
                    file_origin = excluded.file_origin,
                    updated_at = excluded.updated_at
                """,
                (
                    child_id,
                    parent["engine"],
                    parent["engine_version"],
                    parent["source_url"],
                    parent.get("source_platform"),
                    parent.get("source_type"),
                    parent.get("resolved_url"),
                    "completed",
                    file_path.stem,
                    parent.get("uploader"),
                    parent.get("thumbnail"),
                    parent.get("webpage_url"),
                    media_info["duration"],
                    media_info["resolution"],
                    str(file_path),
                    file_size,
                    100,
                    file_size,
                    file_size,
                    json.dumps(metadata, ensure_ascii=False),
                    parent.get("download_dir") or str(file_path.parent),
                    True,
                    True,
                    "downloaded",
                    download_id,
                    output["index"],
                    parent["created_at"],
                    timestamp,
                ),
            )

    videos = [
        download_record(f"{download_id}-output-{output['index']}", include_metadata=True)
        for output in playable_outputs
    ]
    for video in videos:
        events.publish({"type": "download", "download": video})
    return videos


def local_video_thumbnail_path(download_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,100}", download_id):
        raise HTTPException(status_code=404, detail="本地封面不存在。")
    return (DATA_DIR / ".video-downloader" / "thumbnails" / f"{download_id}.jpg").resolve()


def generate_local_video_thumbnail(
    download_id: str, file_path: Path, duration: float | None
) -> str | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return None
    output_path = local_video_thumbnail_path(download_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.stem}.tmp.jpg")
    position = min(60.0, max(1.0, float(duration or 10) * 0.1))
    try:
        result = subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{position:.3f}",
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                "-y",
                str(temporary_path),
            ],
            capture_output=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0 or not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            temporary_path.unlink(missing_ok=True)
            return None
        temporary_path.replace(output_path)
    except (OSError, subprocess.SubprocessError):
        temporary_path.unlink(missing_ok=True)
        return None
    thumbnail = f"/api/v1/videos/{download_id}/thumbnail"
    with connection() as database:
        database.execute(
            "UPDATE downloads SET thumbnail = ?, updated_at = ? WHERE id = ? AND file_origin = 'local'",
            (thumbnail, now(), download_id),
        )
    return thumbnail


def refresh_local_media_metadata() -> None:
    with connection() as database:
        rows = database.execute(
            """
            SELECT id, file_path, metadata_json, thumbnail, duration
            FROM downloads
            WHERE status = 'completed'
              AND library_visible = 1
              AND file_origin = 'local'
            """
        ).fetchall()

    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        file_path = Path(row["file_path"] or "")
        if not file_path.is_file():
            continue
        if metadata.get("media_type") not in {"video", "audio"}:
            try:
                media_info = probe_local_media_file(file_path)
                file_size = file_path.stat().st_size
            except (HTTPException, OSError):
                continue
            metadata.update(
                {
                    "media_type": media_info["media_type"],
                    "codec": media_info["codec"],
                    "bit_rate": media_info["bit_rate"],
                    "format_name": media_info["format_name"],
                }
            )
            with connection() as database:
                database.execute(
                    """
                    UPDATE downloads
                    SET duration = ?, resolution = ?, file_size = ?,
                        downloaded_bytes = ?, total_bytes = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        media_info["duration"], media_info["resolution"], file_size,
                        file_size, file_size, json.dumps(metadata, ensure_ascii=False), now(), row["id"],
                    ),
                )
        cached_thumbnail_missing = (
            row["thumbnail"] == f"/api/v1/videos/{row['id']}/thumbnail"
            and not local_video_thumbnail_path(row["id"]).is_file()
        )
        if metadata.get("media_type") == "video" and (not row["thumbnail"] or cached_thumbnail_missing):
            generate_local_video_thumbnail(row["id"], file_path, row["duration"])


def register_local_video(
    request: LocalVideoRequest,
    *,
    publish_event: bool = True,
) -> dict[str, Any]:
    requested_path = Path(request.file_path).expanduser()
    if not requested_path.is_absolute():
        raise HTTPException(status_code=422, detail="本地文件必须填写绝对路径。")

    file_path = requested_path.resolve()
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="本地媒体文件不存在。")
    if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise HTTPException(status_code=422, detail="请选择支持的视频或音频文件。")

    with connection() as database:
        existing = database.execute(
            """
            SELECT id
            FROM downloads
            WHERE file_path = ? AND status = 'completed' AND library_visible = 1
            """,
            (str(file_path),),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="这个本地文件已经在视频库中。")

    media_info = probe_local_media_file(file_path)
    with connection() as database:
        timestamp = now()
        video_id = str(uuid.uuid4())
        file_size = file_path.stat().st_size
        title = request.title.strip() if request.title and request.title.strip() else file_path.stem
        database.execute(
            """
            INSERT INTO downloads (
                id, engine, engine_version, source_url, source_platform, source_type,
                status, title, duration, resolution, file_path, file_size, progress, downloaded_bytes,
                total_bytes, metadata_json, download_dir, library_visible,
                task_deleted, file_origin, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                "local",
                "filesystem",
                file_path.as_uri(),
                "本地文件",
                "local_file",
                "completed",
                title,
                media_info["duration"],
                media_info["resolution"],
                str(file_path),
                file_size,
                100,
                file_size,
                file_size,
                json.dumps(
                    {
                        "media_type": media_info["media_type"],
                        "codec": media_info["codec"],
                        "bit_rate": media_info["bit_rate"],
                        "format_name": media_info["format_name"],
                    },
                    ensure_ascii=False,
                ),
                str(file_path.parent),
                True,
                True,
                "local",
                timestamp,
                timestamp,
            ),
        )

    if media_info["media_type"] == "video":
        generate_local_video_thumbnail(video_id, file_path, media_info["duration"])

    video = download_record(video_id, include_metadata=True)
    if publish_event:
        events.publish({"type": "download", "download": video})
    return video


@app.post("/api/v1/videos/local", status_code=201)
def create_local_video(request: LocalVideoRequest) -> dict[str, Any]:
    return register_local_video(request)


@app.post("/api/v1/videos/local/select-directory")
def select_local_media_directory() -> dict[str, Any]:
    if sys.platform != "darwin":
        raise HTTPException(status_code=409, detail="当前系统不支持选择本地目录。")
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "选择媒体目录")',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"无法打开目录选择器：{error}") from error
    if result.returncode != 0:
        message = normalize_message(result.stderr)
        if "-128" in message:
            return {"cancelled": True}
        raise HTTPException(status_code=500, detail=message or "目录选择器未能打开。")

    directory_path = result.stdout.strip()
    if not directory_path:
        return {"cancelled": True}
    scan = scan_local_media_directory(
        LocalDirectoryScanRequest(directory_path=directory_path)
    )
    return {"cancelled": False, **scan}


@app.post("/api/v1/videos/local/scan")
def scan_local_media_directory(
    request: LocalDirectoryScanRequest,
) -> dict[str, Any]:
    requested_path = Path(request.directory_path).expanduser()
    if not requested_path.is_absolute():
        raise HTTPException(status_code=422, detail="媒体目录必须填写绝对路径。")
    root = requested_path.resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="媒体目录不存在或当前不可访问。")

    with connection() as database:
        existing_paths = {
            row["file_path"]
            for row in database.execute(
                """
                SELECT file_path
                FROM downloads
                WHERE status = 'completed' AND library_visible = 1 AND file_path IS NOT NULL
                """
            ).fetchall()
        }

    files: list[dict[str, Any]] = []
    try:
        candidates = root.rglob("*")
        for candidate in candidates:
            if ".video-downloader" in candidate.parts:
                continue
            try:
                if not candidate.is_file() or candidate.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                resolved = candidate.resolve()
                files.append({
                    "path": str(resolved),
                    "relative_path": str(resolved.relative_to(root)),
                    "name": resolved.name,
                    "size": resolved.stat().st_size,
                    "media_type": "video" if resolved.suffix.lower() in VIDEO_EXTENSIONS else "audio",
                    "already_added": str(resolved) in existing_paths,
                })
            except (OSError, ValueError):
                continue
    except OSError as error:
        raise HTTPException(status_code=409, detail=f"无法读取媒体目录：{error}") from error

    files.sort(key=lambda item: item["relative_path"].casefold())
    return {
        "directory_path": str(root),
        "files": files,
        "total": len(files),
        "available": sum(1 for item in files if not item["already_added"]),
    }


@app.post("/api/v1/videos/local/batch", status_code=201)
def create_local_media_batch(
    request: LocalMediaBatchRequest,
) -> dict[str, Any]:
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for file_path in dict.fromkeys(request.file_paths):
        try:
            video = register_local_video(
                LocalVideoRequest(file_path=file_path),
                publish_event=False,
            )
            successes.append({"path": file_path, "video": video})
        except HTTPException as error:
            failures.append({"path": file_path, "error": str(error.detail)})
        except Exception as error:
            failures.append({"path": file_path, "error": normalize_message(str(error))})
    if successes:
        events.publish({"type": "download", "download": successes[-1]["video"]})
    return {
        "added": len(successes),
        "failed": len(failures),
        "successes": successes,
        "failures": failures,
    }


@app.post("/api/v1/library/scan")
def scan_library_directories() -> dict[str, Any]:
    library_dirs = get_download_settings()["library_dirs"]
    if not library_dirs:
        raise HTTPException(status_code=409, detail="请先在设置中填写至少一个媒体目录。")

    with connection() as database:
        library_rows = [
            dict(row)
            for row in database.execute(
                """
                SELECT id, title, file_path, file_size
                FROM downloads
                WHERE status = 'completed' AND library_visible = 1 AND file_path IS NOT NULL
                """
            ).fetchall()
        ]

    existing_paths: set[str] = set()
    missing_rows: list[dict[str, Any]] = []
    for row in library_rows:
        recorded_path = Path(row["file_path"]).expanduser()
        try:
            resolved_path = recorded_path.resolve()
        except OSError:
            resolved_path = recorded_path
        if resolved_path.is_file():
            existing_paths.add(str(resolved_path))
        else:
            missing_rows.append(row)

    result: dict[str, Any] = {
        "roots": library_dirs,
        "scanned": 0,
        "added": 0,
        "skipped": 0,
        "missing": len(missing_rows),
        "missing_items": [
            {
                "id": row["id"],
                "title": row["title"],
                "old_path": row["file_path"],
            }
            for row in missing_rows
        ],
        "possible_moves": [],
        "failed": [],
    }
    last_added_video: dict[str, Any] | None = None
    possible_move_ids: set[str] = set()
    for directory_value in library_dirs:
        root = Path(directory_value)
        if not root.is_dir():
            result["failed"].append({"path": str(root), "error": "目录不存在或当前不可访问。"})
            continue
        try:
            candidates = root.rglob("*")
            for candidate in candidates:
                if ".video-downloader" in candidate.parts:
                    continue
                try:
                    if not candidate.is_file() or candidate.suffix.lower() not in MEDIA_EXTENSIONS:
                        continue
                    result["scanned"] += 1
                    resolved_candidate = candidate.resolve()
                    if str(resolved_candidate) in existing_paths:
                        result["skipped"] += 1
                        continue
                    candidate_size = resolved_candidate.stat().st_size
                    possible_matches = [
                        row
                        for row in missing_rows
                        if row["id"] not in possible_move_ids
                        and Path(row["file_path"]).name.casefold() == resolved_candidate.name.casefold()
                        and row["file_size"] == candidate_size
                    ]
                    if possible_matches:
                        match = possible_matches[0]
                        possible_move_ids.add(match["id"])
                        result["possible_moves"].append(
                            {
                                "id": match["id"],
                                "title": match["title"],
                                "old_path": match["file_path"],
                                "candidate_path": str(resolved_candidate),
                            }
                        )
                        result["skipped"] += 1
                        continue
                    last_added_video = register_local_video(
                        LocalVideoRequest(file_path=str(resolved_candidate)),
                        publish_event=False,
                    )
                    result["added"] += 1
                except HTTPException as error:
                    if error.status_code == 409:
                        result["skipped"] += 1
                    else:
                        result["failed"].append({"path": str(candidate), "error": str(error.detail)})
                except OSError as error:
                    result["failed"].append({"path": str(candidate), "error": str(error)})
        except OSError as error:
            result["failed"].append({"path": str(root), "error": str(error)})
    if last_added_video is not None:
        events.publish({"type": "download", "download": last_added_video})
    return result


def refresh_local_library_on_startup() -> None:
    refresh_local_media_metadata()
    settings = get_download_settings()
    report: dict[str, Any] = {
        "started_at": now(),
        "finished_at": None,
        "status": "skipped",
        "reason": "startup_scan_disabled" if not settings["scan_library_on_startup"] else "no_library_dirs",
    }
    if settings["scan_library_on_startup"] and settings["library_dirs"]:
        try:
            report = {**scan_library_directories(), "started_at": report["started_at"], "status": "completed"}
        except Exception as error:
            report = {**report, "status": "failed", "error": normalize_message(str(error))}
    report["finished_at"] = now()
    save_settings_values({"last_library_scan": report})


@app.get("/api/v1/videos/{download_id}/thumbnail")
def get_local_video_thumbnail(download_id: str) -> FileResponse:
    video = library_video(download_id)
    expected_url = f"/api/v1/videos/{download_id}/thumbnail"
    thumbnail_path = local_video_thumbnail_path(download_id)
    if video.get("file_origin") != "local" or video.get("thumbnail") != expected_url or not thumbnail_path.is_file():
        raise HTTPException(status_code=404, detail="本地封面不存在。")
    return FileResponse(thumbnail_path, media_type="image/jpeg")


@app.get("/api/v1/videos")
def list_videos(
    q: str | None = Query(default=None, max_length=200),
    platform: list[str] = Query(default=[]),
    file_format: list[str] = Query(default=[]),
    resolution: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM downloads WHERE status = 'completed' AND library_visible = 1"
    values: list[Any] = []
    if q:
        query += " AND (title LIKE ? OR uploader LIKE ? OR source_platform LIKE ? OR webpage_url LIKE ? OR video_id LIKE ? OR file_path LIKE ?)"
        pattern = f"%{q}%"
        values.extend([pattern, pattern, pattern, pattern, pattern, pattern])
    if platform:
        query += f" AND source_platform IN ({', '.join('?' for _ in platform)})"
        values.extend(platform)
    if file_format:
        normalized_formats = [value.lower().lstrip(".") for value in file_format]
        if any(not value.isalnum() for value in normalized_formats):
            raise HTTPException(status_code=422, detail="文件格式筛选无效。")
        query += f" AND ({' OR '.join('LOWER(file_path) LIKE ?' for _ in normalized_formats)})"
        values.extend(f"%.{value}" for value in normalized_formats)
    if resolution:
        query += " AND resolution = ?"
        values.append(resolution)
    query += " ORDER BY created_at DESC LIMIT ?"
    values.append(limit)

    with connection() as database:
        rows = database.execute(query, values).fetchall()

    return [download_record(row["id"]) for row in rows]


@app.get("/api/v1/library/items")
def list_library_items(
    q: str | None = Query(default=None, max_length=200),
    platform: list[str] = Query(default=[]),
    file_format: list[str] = Query(default=[]),
    resolution: str | None = Query(default=None, max_length=100),
    media_type: str | None = None,
    file_status: str = "all",
    include_playlists: bool = False,
    page: int = 1,
    page_size: int = 10,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    favorite_only: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is not None:
        page = 1
        page_size = limit
    if page < 1 or page_size < 1 or page_size > 200:
        raise HTTPException(status_code=422, detail="分页参数无效。")
    if sort_by not in {"created_at", "title", "file_size", "duration"}:
        raise HTTPException(status_code=422, detail="排序字段无效。")
    if sort_order not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="排序方向无效。")
    if media_type not in {None, "video", "audio", "playlist"}:
        raise HTTPException(status_code=422, detail="媒体类型筛选无效。")
    if file_status not in {"all", "available", "missing"}:
        raise HTTPException(status_code=422, detail="文件状态筛选无效。")

    normalized_formats = [value.lower().lstrip(".") for value in file_format]
    if any(not value.isalnum() for value in normalized_formats):
        raise HTTPException(status_code=422, detail="文件格式筛选无效。")

    with connection() as database:
        download_rows = database.execute(
            """
            SELECT *
            FROM downloads
            WHERE status = 'completed' AND library_visible = 1
            ORDER BY created_at DESC
            """
        ).fetchall()
        playlist_rows = {
            row["id"]: dict(row)
            for row in database.execute("SELECT * FROM playlists").fetchall()
        }

    standalone_rows: list[sqlite3.Row] = []
    playlist_downloads: dict[str, list[sqlite3.Row]] = {}
    for row in download_rows:
        playlist_id = row["playlist_id"]
        if playlist_id:
            playlist_downloads.setdefault(playlist_id, []).append(row)
        else:
            standalone_rows.append(row)

    search_term = q.strip().lower() if q else ""

    def row_format(row: sqlite3.Row) -> str:
        file_path = row["file_path"]
        return Path(file_path).suffix.lower().lstrip(".") if file_path else ""

    def row_media_type(row: sqlite3.Row) -> str:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        stored_type = metadata.get("media_type")
        if stored_type in {"video", "audio"}:
            return stored_type
        file_path = row["file_path"]
        if file_path and Path(file_path).suffix.lower() not in VIDEO_EXTENSIONS:
            return "audio"
        return "video"

    def row_matches(row: sqlite3.Row) -> bool:
        if search_term:
            searchable = (
                row["title"],
                row["uploader"],
                row["source_platform"],
                row["webpage_url"],
                row["video_id"],
                row["file_path"],
            )
            if not any(search_term in str(value).lower() for value in searchable if value):
                return False
        if platform and row["source_platform"] not in platform:
            return False
        if normalized_formats and row_format(row) not in normalized_formats:
            return False
        if resolution and row["resolution"] != resolution:
            return False
        if media_type and media_type != row_media_type(row):
            return False
        if favorite_only and not row["favorite"]:
            return False
        file_available = bool(row["file_path"] and Path(row["file_path"]).is_file())
        if file_status == "available" and not file_available:
            return False
        if file_status == "missing" and file_available:
            return False
        return True

    items: list[dict[str, Any]] = []
    for row in standalone_rows:
        if row_matches(row):
            items.append({"kind": "video", **download_record(row["id"], include_metadata=True)})

    for playlist_id, children in playlist_downloads.items():
        playlist = playlist_rows.get(playlist_id)
        if playlist is None:
            continue

        playlist_search_match = not search_term or any(
            search_term in str(value).lower()
            for value in (
                playlist.get("title"),
                playlist.get("uploader"),
                playlist.get("source_platform"),
                playlist.get("source_url"),
                playlist.get("external_id"),
            )
            if value
        )
        child_search_match = not search_term or any(
            any(
                search_term in str(value).lower()
                for value in (
                    child["title"],
                    child["uploader"],
                    child["source_url"],
                    child["webpage_url"],
                    child["video_id"],
                )
                if value
            )
            for child in children
        )
        if not playlist_search_match and not child_search_match:
            continue
        if platform and playlist.get("source_platform") not in platform:
            continue
        if normalized_formats and not any(row_format(child) in normalized_formats for child in children):
            continue
        if resolution and not any(child["resolution"] == resolution for child in children):
            continue
        if media_type and media_type != "playlist" and not (
            media_type == "video" and include_playlists
        ):
            continue
        favorite_count = sum(1 for child in children if child["favorite"])
        if favorite_only and not playlist.get("favorite") and favorite_count == 0:
            continue
        available_children = [child for child in children if child["file_path"] and Path(child["file_path"]).is_file()]
        if file_status == "available" and len(available_children) != len(children):
            continue
        if file_status == "missing" and len(available_children) == len(children):
            continue

        durations = [child["duration"] for child in children if child["duration"] is not None]
        file_sizes = [child["file_size"] for child in children if child["file_size"] is not None]
        resolutions = sorted({child["resolution"] for child in children if child["resolution"]})
        file_formats = sorted({row_format(child) for child in children if row_format(child)})
        items.append(
            {
                "kind": "playlist",
                "id": playlist["id"],
                "external_id": playlist.get("external_id"),
                "source_url": playlist["source_url"],
                "source_platform": playlist.get("source_platform"),
                "title": playlist["title"],
                "uploader": playlist.get("uploader"),
                "thumbnail": playlist.get("thumbnail") or next(
                    (child["thumbnail"] for child in children if child["thumbnail"]),
                    None,
                ),
                "description": playlist.get("description"),
                "total_count": playlist["total_count"],
                "source_total_count": playlist.get("source_total_count") or playlist["total_count"],
                "completed_count": len(children),
                "duration": sum(durations) if durations else None,
                "file_size": sum(file_sizes) if file_sizes else None,
                "resolutions": resolutions,
                "file_formats": file_formats,
                "favorite": int(playlist.get("favorite") or 0),
                "favorite_count": favorite_count,
                "watched_count": sum(1 for child in children if child["watched"]),
                "created_at": playlist["created_at"],
                "updated_at": playlist["updated_at"],
            }
        )

    total = len(items)

    def sort_value(item: dict[str, Any]) -> Any:
        value = item.get(sort_by)
        if sort_by == "title":
            return str(value or "").casefold()
        return value

    present_items = [item for item in items if item.get(sort_by) is not None]
    missing_items = [item for item in items if item.get(sort_by) is None]
    present_items.sort(key=sort_value, reverse=sort_order == "desc")
    items = [*present_items, *missing_items]
    start = (page - 1) * page_size
    total_pages = (total + page_size - 1) // page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def continue_watching_items(limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Return resumable videos and the next unwatched item in engaged playlists."""
    with connection() as database:
        rows = database.execute(
            """
            SELECT id, playlist_id, playlist_index, watch_position, watched,
                   last_watched_at, file_path, metadata_json
            FROM downloads
            WHERE status = 'completed' AND library_visible = 1
            ORDER BY COALESCE(last_watched_at, '') DESC, created_at DESC
            """
        ).fetchall()

    def playable_video(row: sqlite3.Row) -> bool:
        file_path = row["file_path"]
        if not file_path or not Path(file_path).is_file():
            return False
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        media_type = metadata.get("media_type")
        if media_type == "audio":
            return False
        return media_type == "video" or Path(file_path).suffix.lower() in VIDEO_EXTENSIONS

    video_rows = [row for row in rows if playable_video(row)]
    continuing = [
        {
            "reason": "continue",
            "video": download_record(row["id"], include_metadata=True),
        }
        for row in video_rows
        if float(row["watch_position"] or 0) > 0 and not row["watched"]
    ][:limit]

    playlist_rows: dict[str, list[sqlite3.Row]] = {}
    for row in video_rows:
        if row["playlist_id"]:
            playlist_rows.setdefault(row["playlist_id"], []).append(row)

    next_up: list[dict[str, Any]] = []
    for playlist_id, children in playlist_rows.items():
        ordered = sorted(
            children,
            key=lambda row: (int(row["playlist_index"] or 0), row["id"]),
        )
        engaged = [
            row for row in ordered
            if row["watched"] or float(row["watch_position"] or 0) > 0
        ]
        if not engaged:
            continue
        latest = max(
            engaged,
            key=lambda row: (int(row["playlist_index"] or 0), row["last_watched_at"] or ""),
        )
        # An unfinished item belongs in "continue watching"; do not also urge
        # the user to skip it for the next episode.
        if not latest["watched"]:
            continue
        candidate = next(
            (
                row for row in ordered
                if int(row["playlist_index"] or 0) > int(latest["playlist_index"] or 0)
                and not row["watched"]
                and float(row["watch_position"] or 0) == 0
            ),
            None,
        )
        if candidate is None:
            continue
        with connection() as database:
            playlist = database.execute(
                "SELECT title FROM playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
        next_up.append(
            {
                "reason": "next",
                "playlist_title": playlist["title"] if playlist else None,
                "video": download_record(candidate["id"], include_metadata=True),
                "last_watched_at": latest["last_watched_at"],
            }
        )

    next_up.sort(key=lambda item: item.get("last_watched_at") or "", reverse=True)
    for item in next_up:
        item.pop("last_watched_at", None)
    return {"continuing": continuing, "next_up": next_up[:limit]}


@app.get("/api/v1/library/continue-watching")
def get_continue_watching(limit: int = Query(default=8, ge=1, le=24)) -> dict[str, Any]:
    return continue_watching_items(limit)


@app.get("/api/v1/videos/suggestions")
def suggest_videos_by_title(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=12, ge=1, le=30),
) -> list[dict[str, Any]]:
    pattern = f"%{q.strip()}%"
    with connection() as database:
        rows = database.execute(
            """
            SELECT id
            FROM downloads
            WHERE status = 'completed' AND library_visible = 1 AND title LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
    return [download_record(row["id"]) for row in rows]


def library_video(download_id: str) -> dict[str, Any]:
    video = download_record(download_id, include_metadata=True)
    if video["status"] != "completed" or not video["library_visible"]:
        raise HTTPException(status_code=409, detail="只能更新视频库中的已完成视频。")
    return video


def safe_media_title(value: str) -> str:
    title = re.sub(r"[\\/:\x00]", " ", value).strip().strip(".")
    title = re.sub(r"\s+", " ", title)
    title = title[:180].rstrip()
    if not title:
        raise HTTPException(status_code=422, detail="请输入有效的标题。")
    return title


def normalized_video_metadata(request: VideoMetadataRequest) -> dict[str, str | None]:
    uploader = (request.uploader or "").strip() or None
    thumbnail = (request.thumbnail or "").strip() or None
    upload_date = (request.upload_date or "").strip() or None
    playlist_id = (request.playlist_id or "").strip() or None
    if thumbnail:
        parsed_thumbnail = urlparse(thumbnail)
        if parsed_thumbnail.scheme not in {"http", "https"} or not parsed_thumbnail.netloc:
            raise HTTPException(status_code=422, detail="封面必须填写有效的 HTTP 或 HTTPS 图片网址。")
    if upload_date:
        try:
            parsed_date = datetime.strptime(upload_date, "%Y-%m-%d")
        except ValueError as error:
            raise HTTPException(status_code=422, detail="发布日期必须是有效的年月日。") from error
        upload_date = parsed_date.strftime("%Y%m%d")
    return {
        "uploader": uploader,
        "thumbnail": thumbnail,
        "upload_date": upload_date,
        "playlist_id": playlist_id,
    }


@app.put("/api/v1/videos/{download_id}/metadata")
def update_video_metadata(
    download_id: str, request: VideoMetadataRequest
) -> dict[str, Any]:
    video = library_video(download_id)
    values = normalized_video_metadata(request)
    old_playlist_id = video.get("playlist_id")
    new_playlist_id = values.pop("playlist_id")
    timestamp = now()
    with connection() as database:
        if new_playlist_id:
            playlist = database.execute(
                "SELECT id FROM playlists WHERE id = ?", (new_playlist_id,)
            ).fetchone()
            if playlist is None:
                raise HTTPException(status_code=422, detail="选择的合集不存在。")
        playlist_index = video.get("playlist_index")
        if new_playlist_id != old_playlist_id:
            if new_playlist_id:
                row = database.execute(
                    "SELECT COALESCE(MAX(playlist_index), 0) AS last_index FROM downloads WHERE playlist_id = ?",
                    (new_playlist_id,),
                ).fetchone()
                playlist_index = int(row["last_index"] or 0) + 1
            else:
                playlist_index = None
        database.execute(
            """
            UPDATE downloads
            SET uploader = ?, thumbnail = ?, upload_date = ?, playlist_id = ?, playlist_index = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                values["uploader"],
                values["thumbnail"],
                values["upload_date"],
                new_playlist_id,
                playlist_index,
                timestamp,
                download_id,
            ),
        )
        for playlist_id in {old_playlist_id, new_playlist_id} - {None}:
            database.execute(
                """
                UPDATE playlists
                SET total_count = (SELECT COUNT(*) FROM downloads WHERE playlist_id = ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (playlist_id, timestamp, playlist_id),
            )
    publish_download(download_id)
    return download_record(download_id, include_metadata=True)


def replace_output_path(download_id: str, old_path: Path, new_path: Path) -> None:
    with connection() as database:
        row = database.execute(
            "SELECT output_files_json, download_dir FROM downloads WHERE id = ?",
            (download_id,),
        ).fetchone()
        if row is None or not row["output_files_json"] or not row["download_dir"]:
            return
        try:
            outputs = json.loads(row["output_files_json"])
            root = Path(row["download_dir"]).resolve()
            old_relative = str(old_path.relative_to(root))
            new_relative = str(new_path.relative_to(root))
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        changed = False
        for output in outputs if isinstance(outputs, list) else []:
            if isinstance(output, dict) and output.get("relative_path") == old_relative:
                output["relative_path"] = new_relative
                changed = True
        if changed:
            database.execute(
                "UPDATE downloads SET output_files_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(outputs, ensure_ascii=False), now(), download_id),
            )


@app.put("/api/v1/videos/{download_id}/title")
def rename_video(download_id: str, request: VideoRenameRequest) -> dict[str, Any]:
    video = library_video(download_id)
    title = safe_media_title(request.title)
    values: dict[str, Any] = {"title": title}
    if request.rename_file:
        media_path, _download_dir = local_video_path(video)
        new_media_path = media_path.with_name(f"{title}{media_path.suffix}")
        if new_media_path != media_path:
            related_paths = related_video_files(media_path)
            rename_pairs: list[tuple[Path, Path]] = []
            for old_path in related_paths:
                suffix = old_path.name[len(media_path.stem):] if old_path != media_path else media_path.suffix
                target = old_path.with_name(f"{title}{suffix}")
                if target != old_path and target.exists():
                    raise HTTPException(status_code=409, detail=f"同目录已存在文件：{target.name}")
                rename_pairs.append((old_path, target))

            renamed: list[tuple[Path, Path]] = []
            try:
                for old_path, target in rename_pairs:
                    if old_path == target:
                        continue
                    old_path.rename(target)
                    renamed.append((old_path, target))
            except OSError as error:
                for old_path, target in reversed(renamed):
                    try:
                        target.rename(old_path)
                    except OSError:
                        pass
                raise HTTPException(status_code=409, detail=f"文件重命名失败：{normalize_message(str(error))}") from error

            values["file_path"] = str(new_media_path)
            if video.get("file_origin") == "local":
                values["source_url"] = new_media_path.as_uri()
            replace_output_path(download_id, media_path, new_media_path)
            if video.get("parent_download_id"):
                replace_output_path(str(video["parent_download_id"]), media_path, new_media_path)

    update_download(download_id, **values)
    publish_download(download_id)
    return download_record(download_id, include_metadata=True)


@app.put("/api/v1/videos/{download_id}/relink")
def relink_video(download_id: str, request: VideoRelinkRequest) -> dict[str, Any]:
    video = library_video(download_id)
    requested_path = Path(request.file_path).expanduser()
    if not requested_path.is_absolute():
        raise HTTPException(status_code=422, detail="重新定位必须使用绝对文件路径。")
    new_path = requested_path.resolve()
    if not new_path.is_file():
        raise HTTPException(status_code=404, detail="选择的媒体文件不存在。")
    if new_path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise HTTPException(status_code=422, detail="请选择支持的视频或音频文件。")

    with connection() as database:
        duplicate = database.execute(
            """
            SELECT id FROM downloads
            WHERE id != ? AND file_path = ? AND status = 'completed' AND library_visible = 1
            """,
            (download_id, str(new_path)),
        ).fetchone()
        metadata_row = database.execute(
            "SELECT metadata_json FROM downloads WHERE id = ?",
            (download_id,),
        ).fetchone()
    if duplicate:
        raise HTTPException(status_code=409, detail="这个文件已经关联到其他媒体记录。")

    media_info = probe_local_media_file(new_path)
    metadata = json.loads(metadata_row["metadata_json"] or "{}") if metadata_row else {}
    previous_media_type = metadata.get("media_type")
    if previous_media_type in {"video", "audio"} and previous_media_type != media_info["media_type"]:
        raise HTTPException(status_code=409, detail="选择的文件类型与原媒体记录不一致。")
    metadata.update(
        {
            "media_type": media_info["media_type"],
            "codec": media_info["codec"],
            "bit_rate": media_info["bit_rate"],
            "format_name": media_info["format_name"],
        }
    )
    old_path = Path(video["file_path"]).expanduser() if video.get("file_path") else None
    values: dict[str, Any] = {
        "file_path": str(new_path),
        "file_size": new_path.stat().st_size,
        "download_dir": str(new_path.parent),
        "duration": media_info["duration"],
        "resolution": media_info["resolution"],
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    if video.get("file_origin") == "local":
        values["source_url"] = new_path.as_uri()
    update_download(download_id, **values)
    if old_path is not None:
        replace_output_path(download_id, old_path, new_path)
        if video.get("parent_download_id"):
            replace_output_path(str(video["parent_download_id"]), old_path, new_path)
    publish_download(download_id)
    return download_record(download_id, include_metadata=True)


@app.post("/api/v1/videos/{download_id}/relink/select")
def select_and_relink_video(download_id: str) -> dict[str, Any]:
    library_video(download_id)
    if sys.platform != "darwin":
        raise HTTPException(status_code=409, detail="当前系统不支持打开文件选择器。")
    try:
        result = subprocess.run(
            ["osascript", "-e", 'POSIX path of (choose file with prompt "重新定位媒体文件")'],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"无法打开文件选择器：{error}") from error
    if result.returncode != 0:
        message = normalize_message(result.stderr)
        if "-128" in message:
            return {"cancelled": True}
        raise HTTPException(status_code=500, detail=message or "文件选择器未能打开。")
    file_path = result.stdout.strip()
    if not file_path:
        return {"cancelled": True}
    return {"cancelled": False, "video": relink_video(download_id, VideoRelinkRequest(file_path=file_path))}


@app.put("/api/v1/videos/{download_id}/favorite")
def update_video_favorite(
    download_id: str, request: VideoFavoriteRequest
) -> dict[str, Any]:
    library_video(download_id)
    update_download(download_id, favorite=int(request.favorite))
    publish_download(download_id)
    return download_record(download_id, include_metadata=True)


@app.get("/api/v1/videos/{download_id}/compatibility")
def get_video_compatibility(download_id: str) -> dict[str, Any]:
    return video_compatibility(download_id)


@app.post("/api/v1/videos/{download_id}/open")
def open_video_external(
    download_id: str, request: ExternalPlayerRequest
) -> dict[str, Any]:
    return open_video_in_external_player(download_id, request)


@app.post("/api/v1/videos/{download_id}/compatibility/remux", status_code=202)
def start_video_compatibility_remux(
    download_id: str, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    return create_compatibility_remux(download_id, background_tasks)


@app.get("/api/v1/videos/{download_id}/compatibility/jobs")
def list_video_compatibility_jobs(download_id: str) -> list[dict[str, Any]]:
    library_video(download_id)
    with connection() as database:
        ids = [
            row["id"]
            for row in database.execute(
                """
                SELECT id FROM media_derivative_jobs
                WHERE download_id = ? AND kind = 'compatibility_remux'
                ORDER BY created_at DESC
                """,
                (download_id,),
            ).fetchall()
        ]
    return [media_derivative_job_record(job_id) for job_id in ids]


@app.get("/api/v1/media-derivative-jobs/{job_id}")
def get_media_derivative_job(job_id: str) -> dict[str, Any]:
    return media_derivative_job_record(job_id)


def resolution_height(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:x|^)(\d{3,5})p?$", value.strip().lower())
    return int(match.group(1)) if match else None


def video_upgrade_options(download_id: str) -> dict[str, Any]:
    video = library_video(download_id)
    if video.get("file_origin") != "downloaded" or video.get("engine") != "yt-dlp":
        raise HTTPException(status_code=409, detail="本地导入的视频没有可检查的在线画质来源。")
    if not video.get("file_exists"):
        raise HTTPException(status_code=409, detail="原视频文件不存在，无法安全升级画质。")
    with connection() as database:
        pending_upgrades = database.execute(
            """
            SELECT file_path FROM downloads
            WHERE upgrade_from_id = ? AND status = 'completed'
              AND library_visible = 1 AND file_path IS NOT NULL
            """,
            (download_id,),
        ).fetchall()
    if any(Path(row["file_path"]).is_file() for row in pending_upgrades):
        raise HTTPException(status_code=409, detail="已有更高画质版本等待确认，请先处理新旧版本。")
    source_url = str(video.get("webpage_url") or video.get("source_url") or "")
    if not source_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=409, detail="这条视频没有可重新检查的原页面地址。")
    try:
        media = inspect_url(source_url)
    except DownloadError as error:
        raise HTTPException(status_code=422, detail=friendly_inspect_error(error)) from error
    except InputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if media.get("kind") != "video":
        raise HTTPException(status_code=409, detail="原页面现在不再指向单个视频。")

    current_resolution = video.get("resolution")
    current_height = resolution_height(current_resolution)
    if current_height is None:
        try:
            media_path, _ = local_video_path(video)
            current_resolution = probe_local_media_file(media_path).get("resolution")
            current_height = resolution_height(current_resolution)
        except HTTPException:
            current_height = None
    candidates = []
    preference_order: dict[str, int] = {}
    for preference_index, item in enumerate(media.get("formats") or []):
        height = resolution_height(item.get("resolution"))
        if height is None or (current_height is not None and height <= current_height):
            continue
        preference_order[str(item.get("format_id") or "")] = preference_index
        candidates.append({**item, "height": height})
    # The download dialog exposes yt-dlp's best format first. Upgrade choices
    # are shown from lower to higher resolution, with the preferred format last
    # at the same height so the UI's default remains the best available choice.
    candidates.sort(
        key=lambda item: (
            item["height"],
            -preference_order.get(str(item.get("format_id") or ""), 0),
        )
    )
    return {
        "download_id": download_id,
        "source_url": source_url,
        "current_resolution": current_resolution,
        "current_height": current_height,
        "candidates": candidates,
    }


@app.get("/api/v1/videos/{download_id}/upgrade-options")
def get_video_upgrade_options(download_id: str) -> dict[str, Any]:
    return video_upgrade_options(download_id)


@app.post("/api/v1/videos/{download_id}/upgrade", status_code=201)
def start_video_upgrade(
    download_id: str, request: VideoUpgradeRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    options = video_upgrade_options(download_id)
    allowed_formats = {item["format_id"] for item in options["candidates"]}
    if request.format_id not in allowed_formats:
        raise HTTPException(status_code=409, detail="所选画质不高于当前文件，或已不再可用。")
    return create_download(
        DownloadRequest(
            url=options["source_url"],
            format_id=request.format_id,
            priority=request.priority,
            upgrade_from_id=download_id,
        ),
        background_tasks,
    )


@app.post("/api/v1/videos/{download_id}/upgrade-finalize", response_model=None)
def finalize_video_upgrade(
    download_id: str, request: VideoUpgradeFinalizeRequest
) -> dict[str, Any] | JSONResponse:
    upgraded = library_video(download_id)
    original_id = upgraded.get("upgrade_from_id")
    if not original_id:
        raise HTTPException(status_code=409, detail="这条视频没有待确认的旧版本。")
    if not request.confirm:
        raise HTTPException(status_code=409, detail="请明确确认后再处理旧版本。")
    original = library_video(str(original_id))
    if not upgraded.get("file_exists"):
        raise HTTPException(status_code=409, detail="新版本文件不存在，旧版本已保留。")
    if request.remove_original_file:
        preserved_values = {
            key: original.get(key)
            for key in (
                "title",
                "uploader",
                "thumbnail",
                "upload_date",
                "playlist_id",
                "playlist_index",
                "favorite",
                "watched",
                "watch_position",
                "last_watched_at",
            )
        }
        result = delete_video(str(original_id), remove_file=True)
        if isinstance(result, JSONResponse):
            return result
        update_download(download_id, **preserved_values)
    else:
        result = {"id": original["id"], "kept": True, "trashed_files": []}
    with connection() as database:
        database.execute(
            "UPDATE downloads SET upgrade_from_id = NULL, updated_at = ? WHERE id = ?",
            (now(), download_id),
        )
    publish_download(download_id)
    return {"video": download_record(download_id, include_metadata=True), "original": result}


@app.put("/api/v1/videos/{download_id}/progress")
def update_video_progress(
    download_id: str, request: PlaybackProgressRequest
) -> dict[str, Any]:
    video = library_video(download_id)
    duration = request.duration or video.get("duration")
    position = request.position
    if duration and duration > 0:
        position = min(position, duration)
        watched_threshold = max(duration * 0.9, duration - 30)
        watched = bool(video.get("watched")) or position >= watched_threshold
    else:
        watched = bool(video.get("watched"))
    values: dict[str, Any] = {
        "watch_position": position,
        "watched": int(watched),
    }
    if position > 0:
        values["last_watched_at"] = now()
    update_download(download_id, **values)
    return download_record(download_id, include_metadata=True)


@app.put("/api/v1/videos/{download_id}/watched")
def update_video_watched(
    download_id: str, request: VideoWatchedRequest
) -> dict[str, Any]:
    video = library_video(download_id)
    if request.watched:
        position = video.get("duration") or video.get("watch_position") or 0
    else:
        position = 0
    update_download(
        download_id,
        watched=int(request.watched),
        watch_position=position,
        last_watched_at=now() if request.watched else video.get("last_watched_at"),
    )
    publish_download(download_id)
    return download_record(download_id, include_metadata=True)


@app.get("/api/v1/videos/{download_id}/subtitles")
def list_video_subtitles(download_id: str) -> dict[str, Any]:
    video = library_video(download_id)
    media_path, _download_dir = local_video_path(video)
    tracks = []
    for index, subtitle_path in enumerate(playable_subtitle_files(media_path)):
        label, language = subtitle_track_label(media_path, subtitle_path)
        tracks.append(
            {
                "index": index,
                "filename": subtitle_path.name,
                "label": label,
                "language": language,
                "format": subtitle_path.suffix.lower().lstrip("."),
                "url": f"/api/v1/videos/{download_id}/subtitles/{index}/file",
            }
        )
    metadata = video.get("metadata") or {}
    preferred = metadata.get("preferred_subtitle")
    if preferred not in {track["filename"] for track in tracks}:
        preferred = None
    return {"tracks": tracks, "preferred_filename": preferred}


@app.get("/api/v1/videos/{download_id}/subtitles/{subtitle_index}/file")
def get_video_subtitle_file(download_id: str, subtitle_index: int) -> Any:
    video = library_video(download_id)
    media_path, _download_dir = local_video_path(video)
    subtitles = playable_subtitle_files(media_path)
    if subtitle_index < 0 or subtitle_index >= len(subtitles):
        raise HTTPException(status_code=404, detail="找不到这个字幕文件。")
    subtitle_path = subtitles[subtitle_index]
    if subtitle_path.suffix.lower() == ".srt":
        return StreamingResponse(iter([srt_to_webvtt(subtitle_path)]), media_type="text/vtt; charset=utf-8")
    return FileResponse(subtitle_path, media_type="text/vtt; charset=utf-8", filename=subtitle_path.name)


@app.put("/api/v1/videos/{download_id}/subtitle-preference")
def update_subtitle_preference(
    download_id: str, request: SubtitlePreferenceRequest
) -> dict[str, Any]:
    video = library_video(download_id)
    media_path, _download_dir = local_video_path(video)
    available = {path.name for path in playable_subtitle_files(media_path)}
    if request.filename is not None and request.filename not in available:
        raise HTTPException(status_code=422, detail="选择的字幕文件不存在或不受支持。")
    with connection() as database:
        row = database.execute(
            "SELECT metadata_json FROM downloads WHERE id = ?",
            (download_id,),
        ).fetchone()
    metadata = json.loads(row["metadata_json"] or "{}") if row else {}
    if request.filename is None:
        metadata.pop("preferred_subtitle", None)
    else:
        metadata["preferred_subtitle"] = request.filename
    update_download(download_id, metadata_json=json.dumps(metadata, ensure_ascii=False))
    return list_video_subtitles(download_id)


@app.get("/api/v1/videos/{download_id}/delete-preview")
def preview_video_delete(download_id: str) -> list[dict[str, str]]:
    video = download_record(download_id)
    if video["status"] != "completed":
        raise HTTPException(status_code=409, detail="只能删除已完成的视频。")
    media_path, download_dir = local_video_path(video)
    preview: list[dict[str, str]] = []
    for path in related_video_files(media_path):
        try:
            path.relative_to(download_dir)
        except ValueError:
            continue
        lower_name = path.name.lower()
        if path == media_path:
            file_kind = "主视频"
        elif lower_name.endswith(".info.json"):
            file_kind = "info JSON"
        elif path.suffix.lower() in {".vtt", ".srt", ".ass", ".ssa", ".lrc"}:
            file_kind = "字幕"
        else:
            file_kind = "本地封面"
        preview.append({"kind": file_kind, "path": str(path)})

    derivative_dir = (download_dir / ".video-downloader" / "denoise").resolve()
    with connection() as database:
        rows = database.execute(
            "SELECT file_path FROM video_denoise_jobs WHERE download_id = ?",
            (download_id,),
        ).fetchall()
    for row in rows:
        if not row["file_path"]:
            continue
        derivative_path = Path(row["file_path"]).resolve()
        try:
            derivative_path.relative_to(derivative_dir)
        except ValueError:
            continue
        if derivative_path.is_file():
            preview.append({"kind": "历史副本", "path": str(derivative_path)})
    return preview


@app.get("/api/v1/videos/{download_id}/denoise")
def list_denoise_jobs(download_id: str) -> list[dict[str, Any]]:
    download_record(download_id)
    with connection() as database:
        rows = database.execute(
            """
            SELECT id
            FROM video_denoise_jobs
            WHERE download_id = ?
            ORDER BY created_at DESC
            """,
            (download_id,),
        ).fetchall()
    return [denoise_job_record(row["id"]) for row in rows]


@app.post("/api/v1/videos/{download_id}/denoise", status_code=202)
def create_denoise_job(
    download_id: str, request: DenoiseRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    preset = request.preset.strip().lower()
    if preset not in DENOISE_PRESETS:
        raise HTTPException(status_code=422, detail="降噪强度只能选择轻柔、平衡或强力。")
    if not ffmpeg_version():
        raise HTTPException(status_code=409, detail="未找到 FFmpeg，无法生成降噪副本。")
    filter_config = denoise_filter_config(request, preset)
    filter_expression = denoise_filter_expression(filter_config)

    video = download_record(download_id)
    source_path, download_dir = local_video_path(video)
    with connection() as database:
        active_job = database.execute(
            """
            SELECT id FROM video_denoise_jobs
            WHERE download_id = ? AND status IN ('queued', 'running')
            """,
            (download_id,),
        ).fetchone()
        if active_job:
            raise HTTPException(status_code=409, detail="这个视频正在生成降噪副本。")

        job_id = str(uuid.uuid4())
        timestamp = now()
        database.execute(
            """
            INSERT INTO video_denoise_jobs (
                id, download_id, preset, status, progress, filter_config, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, download_id, preset, "queued", 0, json.dumps(filter_config), timestamp, timestamp),
        )

    output_path = download_dir / ".video-downloader" / "denoise" / (
        f"{source_path.stem}.denoise-{preset}-{job_id[:8]}.mp4"
    )
    background_tasks.add_task(
        run_denoise,
        job_id,
        str(source_path),
        str(output_path),
        video.get("duration"),
        filter_expression,
    )
    publish_denoise_job(job_id)
    return denoise_job_record(job_id)


@app.get("/api/v1/videos/{download_id}/denoise/{job_id}/file")
def get_denoised_video_file(download_id: str, job_id: str) -> FileResponse:
    video = download_record(download_id)
    _, download_dir = local_video_path(video)
    job = denoise_job_record(job_id)
    if job["download_id"] != download_id or job["status"] != "completed" or not job["file_path"]:
        raise HTTPException(status_code=404, detail="降噪副本不存在。")

    output_path = Path(job["file_path"]).resolve()
    derivative_dir = (download_dir / ".video-downloader" / "denoise").resolve()
    try:
        output_path.relative_to(derivative_dir)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="降噪副本不在受管理的位置中。") from error
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="降噪副本不存在。")
    return FileResponse(output_path)


@app.get("/api/v1/videos/{download_id}/file")
def get_video_file(download_id: str) -> FileResponse:
    video = download_record(download_id)
    media_path, _ = local_video_path(video)
    return FileResponse(media_path)


@app.post("/api/v1/videos/{download_id}/reveal")
def reveal_video(download_id: str) -> dict[str, str]:
    video = download_record(download_id)
    media_path, _ = local_video_path(video)
    reveal_in_finder(media_path)
    return {"id": download_id}


@app.delete("/api/v1/videos/{download_id}", response_model=None)
def delete_video(
    download_id: str, remove_file: bool = Query(default=True)
) -> dict[str, Any] | JSONResponse:
    video = download_record(download_id)
    if video["status"] != "completed":
        raise HTTPException(status_code=409, detail="只能删除已完成的视频。")

    with connection() as database:
        active_denoise_job = database.execute(
            """
            SELECT id FROM video_denoise_jobs
            WHERE download_id = ? AND status IN ('queued', 'running')
            """,
            (download_id,),
        ).fetchone()
        if active_denoise_job:
            raise HTTPException(status_code=409, detail="正在生成降噪副本，请完成后再删除此视频。")
        active_media_job = database.execute(
            """
            SELECT id FROM media_derivative_jobs
            WHERE download_id = ? AND status IN ('queued', 'running')
            """,
            (download_id,),
        ).fetchone()
        if active_media_job:
            raise HTTPException(status_code=409, detail="正在生成兼容副本，请完成后再删除此视频。")
        denoise_rows = database.execute(
            "SELECT file_path FROM video_denoise_jobs WHERE download_id = ?",
            (download_id,),
        ).fetchall()

    local_thumbnail = (
        local_video_thumbnail_path(download_id)
        if video.get("file_origin") == "local"
        and video.get("thumbnail") == f"/api/v1/videos/{download_id}/thumbnail"
        else None
    )

    trashed_files: list[str] = []
    if remove_file:
        media_path, download_dir = local_video_path(video)
        files_to_trash = related_video_files(media_path)
        derivative_dir = (download_dir / ".video-downloader" / "denoise").resolve()
        invalid_files: list[dict[str, str]] = []
        for row in denoise_rows:
            if not row["file_path"]:
                continue
            derivative_path = Path(row["file_path"]).resolve()
            try:
                derivative_path.relative_to(derivative_dir)
            except ValueError:
                invalid_files.append({"path": str(derivative_path), "error": "历史副本不在受管理的位置中。"})
                continue
            if derivative_path.is_file():
                files_to_trash.append(derivative_path)

        safe_files: list[Path] = []
        for file_path in files_to_trash:
            try:
                file_path.relative_to(download_dir)
            except ValueError:
                invalid_files.append({"path": str(file_path), "error": "文件不在记录的保存位置中。"})
                continue
            safe_files.append(file_path)

        failed_files = list(invalid_files)
        if failed_files:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "存在不安全的历史文件记录，未移动任何文件，视频记录已保留。",
                    "id": download_id,
                    "trashed_files": [],
                    "failed_files": failed_files,
                },
            )

        unique_files = list(dict.fromkeys(safe_files))
        unique_files.sort(key=lambda path: path == media_path)
        for file_path in unique_files:
            try:
                move_to_trash(file_path)
                trashed_files.append(str(file_path))
            except RuntimeError as error:
                failed_files.append({"path": str(file_path), "error": normalize_message(str(error))})
                break

        if failed_files:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "部分文件未能移入废纸篓，视频记录已保留。",
                    "id": download_id,
                    "trashed_files": trashed_files,
                    "failed_files": failed_files,
                },
            )

    with connection() as database:
        database.execute("DELETE FROM download_logs WHERE download_id = ?", (download_id,))
        database.execute("DELETE FROM video_denoise_jobs WHERE download_id = ?", (download_id,))
        database.execute("DELETE FROM media_derivative_jobs WHERE download_id = ?", (download_id,))
        database.execute(
            """
            UPDATE media_derivative_jobs
            SET status = 'failed', file_path = NULL, file_size = NULL,
                library_video_id = NULL, error = '兼容副本已从媒体库删除。', updated_at = ?
            WHERE library_video_id = ?
            """,
            (now(), download_id),
        )
        database.execute("UPDATE downloads SET upgrade_from_id = NULL WHERE upgrade_from_id = ?", (download_id,))
        database.execute("DELETE FROM downloads WHERE id = ?", (download_id,))
    if local_thumbnail is not None:
        local_thumbnail.unlink(missing_ok=True)

    events.publish({"type": "video_deleted", "download_id": download_id})
    return {"id": download_id, "trashed_files": trashed_files, "failed_files": []}


@app.get("/api/v1/events")
def event_stream() -> StreamingResponse:
    def stream() -> Generator[str, None, None]:
        subscriber = events.subscribe()
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")


FRONTEND_DIST = Path(
    os.environ.get("VIDEO_DOWNLOADER_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist")
)
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")
