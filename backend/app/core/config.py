from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("VIDEO_DOWNLOADER_DATA_DIR", PROJECT_ROOT / "data"))
DOWNLOAD_DIR = Path(os.environ.get("VIDEO_DOWNLOADER_DOWNLOAD_DIR", DATA_DIR / "downloads"))
DATABASE_PATH = DATA_DIR / "video-downloader.sqlite3"

DEFAULT_DOWNLOAD_SETTINGS = {
    "download_dir": str(DOWNLOAD_DIR),
    "directory_pattern": "{platform}/{year}-{month}/{title}",
    "library_dirs": [],
    "write_thumbnail": True,
    "write_info_json": True,
    "max_concurrent_downloads": 5,
    "download_rate_limit_kbps": 0,
    "minimum_free_space_mb": 1024,
    "system_notifications": False,
    "maintenance": {
        "auto_cleanup_enabled": False,
        "retention_days": 30,
        "clean_download_logs": True,
        "clean_download_tasks": True,
    },
    "yt_dlp_config": "",
    "yt_dlp_simple": {
        "concurrent_fragments": None,
        "retries": None,
        "write_subs": False,
        "write_auto_subs": False,
        "sub_langs": "zh.*,en",
        "extract_audio": False,
        "audio_format": None,
    },
    "ffmpeg_config": "",
    "aria2": {
        "split": 5,
        "max_tries": 3,
        "retry_wait": 2,
        "bt_stall_timeout": 90,
    },
    "qbittorrent": {
        "enabled": False,
        "base_url": "http://127.0.0.1:8080",
        "username": "admin",
        "password": "",
        "bt_stall_timeout": 90,
    },
}

DENOISE_PRESETS = {
    "gentle": {"luma_spatial": 2, "chroma_spatial": 1, "luma_temporal": 2, "chroma_temporal": 1},
    "balanced": {"luma_spatial": 4, "chroma_spatial": 3, "luma_temporal": 6, "chroma_temporal": 4},
    "strong": {"luma_spatial": 7, "chroma_spatial": 5, "luma_temporal": 10, "chroma_temporal": 6},
}

VIDEO_EXTENSIONS = {".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ogv", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".alac", ".flac", ".m4a", ".mka", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
DIRECT_FILE_EXTENSIONS = MEDIA_EXTENSIONS

DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
