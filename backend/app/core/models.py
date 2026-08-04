from __future__ import annotations

from pydantic import BaseModel, Field


class InspectRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    engine_hint: str = Field(default="auto", max_length=30)
    torrent_name: str | None = Field(default=None, max_length=255)
    torrent_base64: str | None = Field(default=None, max_length=2_000_000)


class ResourceSearchRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


class SourceFollowRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    check_on_startup: bool = True


class SourceFollowUpdateRequest(BaseModel):
    check_on_startup: bool


class SourceFollowDownloadRequest(BaseModel):
    entry_urls: list[str] = Field(min_length=1, max_length=50)
    format_id: str | None = Field(default=None, max_length=200)
    priority: int = Field(default=0, ge=-1, le=1)


class DownloadRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    inspect_id: str | None = Field(default=None, min_length=1, max_length=100)
    engine_hint: str = Field(default="auto", max_length=30)
    format_id: str | None = Field(default=None, max_length=200)
    selected_file_indexes: list[int] | None = Field(default=None, max_length=1000)
    replace_existing: bool = False
    download_dir: str | None = Field(default=None, min_length=1, max_length=4096)
    write_thumbnail: bool | None = None
    write_info_json: bool | None = None
    yt_dlp_config: str | None = Field(default=None, max_length=30000)
    ffmpeg_config: str | None = Field(default=None, max_length=30000)
    priority: int = Field(default=0, ge=-1, le=1)
    upgrade_from_id: str | None = Field(default=None, min_length=1, max_length=100)


class BatchDownloadRequest(BaseModel):
    items: list[DownloadRequest] = Field(min_length=1, max_length=50)


class DownloadProgressRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class PlaylistDownloadRequest(BaseModel):
    inspect_id: str = Field(min_length=1, max_length=100)
    entry_urls: list[str] | None = Field(default=None, max_length=500)
    format_id: str | None = Field(default=None, max_length=200)
    replace_existing: bool = False
    priority: int = Field(default=0, ge=-1, le=1)


class DownloadSettingsRequest(BaseModel):
    download_dir: str = Field(min_length=1, max_length=4096)
    directory_pattern: str = Field(default="{platform}/{year}-{month}/{title}", max_length=500)
    library_dirs: list[str] = Field(default_factory=list, max_length=20)
    scan_library_on_startup: bool = True
    write_thumbnail: bool = True
    write_info_json: bool = True
    max_concurrent_downloads: int = Field(default=5, ge=1, le=10)
    download_rate_limit_kbps: int = Field(default=0, ge=0, le=1_000_000)
    minimum_free_space_mb: int = Field(default=1024, ge=0, le=1_000_000)
    system_notifications: bool = False
    yt_dlp_config: str = Field(default="", max_length=30000)
    ffmpeg_config: str = Field(default="", max_length=30000)


class GeneralSettingsRequest(BaseModel):
    download_dir: str = Field(min_length=1, max_length=4096)
    directory_pattern: str = Field(default="{platform}/{year}-{month}/{title}", max_length=500)
    library_dirs: list[str] = Field(default_factory=list, max_length=20)
    scan_library_on_startup: bool = True
    write_thumbnail: bool = True
    write_info_json: bool = True
    max_concurrent_downloads: int = Field(default=5, ge=1, le=10)
    download_rate_limit_kbps: int = Field(default=0, ge=0, le=1_000_000)
    minimum_free_space_mb: int = Field(default=1024, ge=0, le=1_000_000)
    system_notifications: bool = False


class MaintenanceSettings(BaseModel):
    auto_cleanup_enabled: bool = False
    retention_days: int = Field(default=30, ge=1, le=3650)
    clean_download_logs: bool = True
    clean_download_tasks: bool = True


class MaintenanceCleanupRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=3650)
    clean_download_logs: bool = True
    clean_download_tasks: bool = True


class IncompleteCleanupRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=1000)


class DuplicateCleanupRequest(BaseModel):
    download_ids: list[str] = Field(min_length=1, max_length=1000)
    confirm: bool = False


class BackupRestoreRequest(BaseModel):
    preview_token: str = Field(min_length=1, max_length=100)
    confirm: bool = False


class PlaybackProgressRequest(BaseModel):
    position: float = Field(ge=0)
    duration: float | None = Field(default=None, ge=0)


class VideoFavoriteRequest(BaseModel):
    favorite: bool


class VideoWatchedRequest(BaseModel):
    watched: bool


class ExternalPlayerRequest(BaseModel):
    player: str = Field(default="system", pattern=r"^(system|iina|vlc)$")


class VideoUpgradeRequest(BaseModel):
    format_id: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=0, ge=-1, le=1)


class VideoUpgradeFinalizeRequest(BaseModel):
    confirm: bool = False
    remove_original_file: bool = False


class EngineSettingsRequest(BaseModel):
    config: str = Field(default="", max_length=30000)


class YtDlpSimpleSettings(BaseModel):
    concurrent_fragments: int | None = Field(default=None, ge=1, le=32)
    retries: int | None = Field(default=None, ge=0, le=100)
    write_subs: bool = False
    write_auto_subs: bool = False
    sub_langs: str = Field(default="zh.*,en", max_length=200)
    extract_audio: bool = False
    audio_format: str | None = Field(default=None, max_length=20)


class YtDlpSettingsRequest(EngineSettingsRequest):
    simple: YtDlpSimpleSettings = Field(default_factory=YtDlpSimpleSettings)


class Aria2Settings(BaseModel):
    split: int = Field(default=5, ge=1, le=16)
    max_tries: int = Field(default=3, ge=1, le=20)
    retry_wait: int = Field(default=2, ge=0, le=60)
    bt_stall_timeout: int = Field(default=90, ge=30, le=600)


class QbittorrentSettings(BaseModel):
    enabled: bool = False
    base_url: str = Field(default="http://127.0.0.1:8080", min_length=1, max_length=2048)
    username: str = Field(default="admin", max_length=255)
    password: str = Field(default="", max_length=1024)
    bt_stall_timeout: int = Field(default=90, ge=30, le=600)


class DenoiseRequest(BaseModel):
    preset: str = Field(default="balanced", max_length=20)
    luma_spatial: float | None = Field(default=None, ge=0, le=20)
    chroma_spatial: float | None = Field(default=None, ge=0, le=20)
    luma_temporal: float | None = Field(default=None, ge=0, le=30)
    chroma_temporal: float | None = Field(default=None, ge=0, le=30)


class LocalVideoRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=500)


class LocalDirectoryScanRequest(BaseModel):
    directory_path: str = Field(min_length=1, max_length=4096)


class LocalMediaBatchRequest(BaseModel):
    file_paths: list[str] = Field(min_length=1, max_length=1000)


class VideoRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    rename_file: bool = False


class VideoRelinkRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=4096)


class SubtitlePreferenceRequest(BaseModel):
    filename: str | None = Field(default=None, max_length=500)


class VideoMetadataRequest(BaseModel):
    uploader: str | None = Field(default=None, max_length=500)
    upload_date: str | None = Field(default=None, max_length=10)
    thumbnail: str | None = Field(default=None, max_length=2048)
    playlist_id: str | None = Field(default=None, max_length=100)
