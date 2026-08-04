import { FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
// Plyr's package exports a default ESM build but declares CommonJS types.
// @ts-expect-error The Vite runtime resolves the default ESM export.
import Plyr from 'plyr'
import 'plyr/dist/plyr.css'
import type WaveSurfer from 'wavesurfer.js'
import videoPlaceholder from './assets/video-placeholder.webp'

const torrentMediaExtensions = new Set(['.3gp', '.aac', '.alac', '.avi', '.flac', '.m4a', '.m4v', '.mka', '.mkv', '.mov', '.mp3', '.mp4', '.mpeg', '.mpg', '.ogg', '.ogv', '.opus', '.wav', '.webm', '.wma'])
const audioMediaExtensions = new Set(['.aac', '.alac', '.flac', '.m4a', '.mka', '.mp3', '.ogg', '.opus', '.wav', '.wma'])
const torrentVisibleFileStep = 200

function isTorrentMediaFile(path: string) {
  const extensionStart = path.lastIndexOf('.')
  return extensionStart >= 0 && torrentMediaExtensions.has(path.slice(extensionStart).toLowerCase())
}

function torrentFolderLabel(path: string) {
  const parts = path.split('/').filter(Boolean)
  return parts.length > 1 ? parts[0] : '根目录'
}

function torrentFileLabel(path: string, folder: string) {
  const prefix = folder === '根目录' ? '' : `${folder}/`
  return prefix && path.startsWith(prefix) ? path.slice(prefix.length) : path
}

type Health = {
  status: string
  engine: string
  engine_version: string
  ffmpeg_available: boolean
  ffmpeg_version: string | null
  application?: {
    version: string
    fastapi_version: string
    uvicorn_version: string
  }
  engines?: {
    'yt-dlp': { available: boolean, version: string }
    aria2: { available: boolean, version: string | null }
    qbittorrent: { available: boolean, version: string | null, configured: boolean }
  }
}

type DownloadFormat = {
  format_id: string
  label: string
  resolution: string | null
  extension: string | null
  file_size: number | null
  file_size_label: string | null
  fps: number | null
}

type VideoUpgradeOptions = {
  download_id: string
  source_url: string
  current_resolution: string | null
  current_height: number | null
  candidates: Array<DownloadFormat & { height: number }>
}

type InspectedVideo = {
  kind: 'video'
  title: string
  uploader: string | null
  thumbnail: string | null
  duration: number | null
  upload_date: string | null
  resolution: string | null
  formats: DownloadFormat[]
}

type InspectedPlaylistEntry = {
  title: string
  uploader: string | null
  thumbnail: string | null
  duration: number | null
  webpage_url: string
  playlist_index: number
}

type InspectedPlaylist = {
  kind: 'playlist'
  external_id: string | null
  title: string
  uploader: string | null
  thumbnail: string | null
  description: string | null
  source_url: string
  source_platform: string
  entry_count: number
  source_total_count: number
  entries: InspectedPlaylistEntry[]
}

type SourceFollow = {
  id: string
  source_url: string
  source_platform: string | null
  title: string
  uploader: string | null
  thumbnail: string | null
  external_id: string | null
  check_on_startup: boolean
  last_checked_at: string | null
  last_error: string | null
  entries: InspectedPlaylistEntry[]
  new_count: number
}

type InspectedFile = {
  kind: 'file'
  title: string
  file_name: string
  file_size: number | null
  content_type: string | null
  engine: 'aria2'
  source_type: 'direct' | 'thunder'
  resolved_host: string | null
  message: string
}

type InspectedTorrent = {
  kind: 'torrent'
  title: string
  engine: 'aria2' | 'qbittorrent'
  source_type: 'magnet' | 'torrent_url' | 'torrent_file' | 'thunder_bt'
  file_count: number | null
  file_size: number | null
  files: Array<{ index: number, path: string, size: number | null }>
  message?: string
}

type InspectedMedia = InspectedVideo | InspectedPlaylist | InspectedFile | InspectedTorrent

type ResourceSearchProvider = {
  key: string
  label: string
}

type ResourceSearchResult = {
  id: string
  title: string
  uploader: string | null
  thumbnail: string | null
  duration: number | null
  upload_date: string | null
  view_count: number | null
  webpage_url: string
  provider: string
  provider_label: string
}

type ResourceSearchResponse = {
  provider: string
  provider_label: string
  query: string
  results: ResourceSearchResult[]
}

type BatchDownloadResponse = {
  created: number
  failed: number
  successes: Array<{
    index: number
    url: string
    download: Download
  }>
  failures: Array<{
    index: number
    url: string
    error: string
  }>
}

type InspectJob = {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  logs: TaskLog[]
  media: InspectedMedia | null
  error: string | null
  started_at: string
  last_activity_at: string
}

type DownloadStatus = 'queued' | 'running' | 'processing' | 'paused' | 'completed' | 'failed' | 'interrupted' | 'cancelled'

type Download = {
  id: string
  engine: 'yt-dlp' | 'aria2' | 'qbittorrent' | 'local'
  engine_version: string
  video_id: string | null
  source_url: string
  source_platform: string | null
  source_type: string | null
  resolved_url: string | null
  requested_format: string | null
  status: DownloadStatus
  priority: number
  queue_position: number | null
  title: string | null
  uploader: string | null
  thumbnail: string | null
  webpage_url: string | null
  duration: number | null
  upload_date: string | null
  resolution: string | null
  file_path: string | null
  file_size: number | null
  progress: number
  downloaded_bytes: number
  total_bytes: number | null
  speed: number | null
  eta: number | null
  error: string | null
  playlist_id: string | null
  playlist_index: number | null
  favorite: number
  watch_position: number
  watched: number
  last_watched_at: string | null
  created_at: string
  file_exists: boolean
  file_origin: 'downloaded' | 'local'
  upgrade_from_id: string | null
  metadata?: {
    chapters?: Array<{ title: string | null; start_time: number | null }>
    description?: string | null
    media_type?: 'video' | 'audio'
    codec?: string | null
    bit_rate?: number | null
    format_name?: string | null
    preferred_subtitle?: string | null
  }
}

type SubtitleTrack = {
  index: number
  filename: string
  label: string
  language: string
  format: 'vtt' | 'srt'
  url: string
}

type SubtitleResponse = {
  tracks: SubtitleTrack[]
  preferred_filename: string | null
}

type VideoCompatibility = {
  download_id: string
  file_name: string
  container: string | null
  video_codec: string | null
  audio_codec: string | null
  audio_track_count: number
  subtitle_track_count: number
  direct_play_likely: boolean
  remux_available: boolean
  reason: string
  players: { system: boolean, iina: boolean, vlc: boolean }
}

type MediaDerivativeJob = {
  id: string
  download_id: string
  kind: 'compatibility_remux'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'
  file_path: string | null
  file_size: number | null
  library_video_id: string | null
  error: string | null
  created_at: string
  updated_at: string
}

type MetadataPlaylistOption = {
  id: string
  title: string
  source_platform: string | null
  item_count: number
}

type LibraryVideo = Download & {
  kind: 'video'
}

type LibraryPlaylist = {
  kind: 'playlist'
  id: string
  external_id: string | null
  source_url: string
  source_platform: string | null
  title: string
  uploader: string | null
  thumbnail: string | null
  description: string | null
  total_count: number
  source_total_count: number
  completed_count: number
  duration: number | null
  file_size: number | null
  resolutions: string[]
  file_formats: string[]
  favorite: number
  favorite_count: number
  watched_count: number
  created_at: string
  updated_at: string
}

type LibraryItem = LibraryVideo | LibraryPlaylist

type LibraryResponse = {
  items: LibraryItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

type ContinueWatchingItem = {
  reason: 'continue' | 'next'
  playlist_title?: string | null
  video: Download
}

type ContinueWatchingResponse = {
  continuing: ContinueWatchingItem[]
  next_up: ContinueWatchingItem[]
}

type TaskPageResponse = {
  items: Download[]
  total: number
  page: number
  page_size: number
  total_pages: number
  counts: {
    all: number
    active: number
    attention: number
  }
}

type TaskProgressResponse = {
  items: Array<Pick<
    Download,
    'id' | 'status' | 'title' | 'thumbnail' | 'progress' | 'downloaded_bytes' |
    'total_bytes' | 'speed' | 'eta' | 'error' | 'resolution'
  >>
  counts: TaskPageResponse['counts']
}

type DownloadOutput = {
  index: number
  relative_path: string
  size: number | null
  file_type: string
  playable: boolean
}

type TaskLog = {
  id: number
  level: 'info' | 'warning' | 'error'
  message: string
  created_at: string
}

type DeleteVideoResult = {
  id: string
  trashed_files: string[]
  failed_files: Array<{ path: string, error: string }>
}

type CleanupReport = {
  trashed_files: string[]
  failed_files: Array<{ path: string, error: string }>
}

type DeletePreviewFile = {
  kind: string
  path: string
}

type LocalMediaFile = {
  path: string
  relative_path: string
  name: string
  size: number
  media_type: 'video' | 'audio'
  already_added: boolean
}

type LocalMediaScanResponse = {
  directory_path: string
  files: LocalMediaFile[]
  total: number
  available: number
}

type LocalMediaDirectorySelectionResponse =
  | ({ cancelled: false } & LocalMediaScanResponse)
  | { cancelled: true }

type LocalMediaBatchResponse = {
  added: number
  failed: number
  successes: Array<{ path: string, video: Download }>
  failures: Array<{ path: string, error: string }>
}

type DownloadSettings = {
  download_dir: string
  directory_pattern: string
  library_dirs: string[]
  scan_library_on_startup: boolean
  last_library_scan: (LibraryScanReport & { status: string, started_at: string, finished_at: string | null }) | null
  write_thumbnail: boolean
  write_info_json: boolean
  max_concurrent_downloads: number
  download_rate_limit_kbps: number
  minimum_free_space_mb: number
  system_notifications: boolean
  maintenance: MaintenanceSettings
  yt_dlp_config: string
  yt_dlp_simple: YtDlpSimpleSettings
  ffmpeg_config: string
  aria2: Aria2Settings
  qbittorrent: QbittorrentSettings
}

type Aria2Settings = {
  split: number
  max_tries: number
  retry_wait: number
  bt_stall_timeout: number
}

type QbittorrentSettings = {
  enabled: boolean
  base_url: string
  username: string
  password: string
  bt_stall_timeout: number
}

type YtDlpSimpleSettings = {
  concurrent_fragments: number | null
  retries: number | null
  write_subs: boolean
  write_auto_subs: boolean
  sub_langs: string
  extract_audio: boolean
  audio_format: string | null
}

type MaintenanceSettings = {
  auto_cleanup_enabled: boolean
  retention_days: number
  clean_download_logs: boolean
  clean_download_tasks: boolean
}

type MaintenanceCleanupPreview = {
  retention_days: number
  cutoff: string
  log_count: number
  log_text_bytes: number
  task_count: number
  tasks: Array<{ id: string, title: string | null, status: DownloadStatus, updated_at: string }>
}

type MaintenanceCleanupResult = MaintenanceCleanupPreview & {
  deleted_logs: number
  cleaned_tasks: number
  cleaned_task_ids: string[]
  completed_at: string
}

type IncompleteResidue = {
  path: string
  kind: string
  size: number
  file_count: number
  download_id: string
  title: string
  status: DownloadStatus
  task_deleted: boolean
  updated_at: string
}

type IncompleteResiduePreview = {
  items: IncompleteResidue[]
  total_items: number
  total_bytes: number
}

type IncompleteCleanupResult = {
  trashed_files: string[]
  failed_files: Array<{ path: string, error: string }>
  freed_bytes: number
}

type DuplicateMediaItem = {
  id: string
  title: string
  path: string
  size: number
  file_origin: 'downloaded' | 'local'
  created_at: string
  media_type: 'video' | 'audio'
}

type DuplicateMediaGroup = {
  fingerprint: string
  size: number
  reclaimable_bytes: number
  items: DuplicateMediaItem[]
}

type DuplicateMediaPreview = {
  groups: DuplicateMediaGroup[]
  total_groups: number
  total_files: number
  scanned_files: number
  skipped_files: number
  potential_reclaim_bytes: number
}

type DuplicateCleanupResult = {
  trashed_download_ids: string[]
  trashed_files: string[]
  failed_items: Array<{ id: string, error: string }>
  freed_bytes: number
}

type BackupPreview = {
  preview_token: string
  expires_in_seconds: number
  created_at: string | null
  application: { api_version?: string, yt_dlp_version?: string }
  counts: Record<'playlists' | 'downloads' | 'download_logs' | 'video_denoise_jobs' | 'media_derivative_jobs' | 'source_follows' | 'app_settings', number>
  missing_media_files: number
  includes_media_files: false
}

type BackupRestoreResult = {
  restored_at: string
  counts: BackupPreview['counts']
  includes_media_files: false
}

type YtDlpUpdateCheck = {
  current_version: string
  latest_version: string
  update_available: boolean
  checked_at: string
  source: string
}

type Page = 'tasks' | 'videos' | 'audio' | 'settings'
type TaskFilter = 'all' | 'active' | 'attention'
type CompletedDownloadNotice = {
  downloadId: string
  title: string
  mediaType: 'video' | 'audio'
}
type TaskActionTarget =
  | { task: Download, action: 'cancel' | 'delete' }
  | { taskIds: string[], action: 'deleteMany' }
type SettingsTab = 'general' | 'yt-dlp' | 'ffmpeg' | 'aria2' | 'qbittorrent' | 'backup' | 'maintenance'
type EngineHint = 'auto' | 'yt-dlp' | 'aria2' | 'qbittorrent'
type VideoMediaType = '' | 'video' | 'audio' | 'playlist'
type VideoFileStatus = 'all' | 'available' | 'missing'
type VideoFilters = {
  title: string
  platforms: string[]
  fileFormats: string[]
  resolution: string
  mediaType: VideoMediaType
  fileStatus?: VideoFileStatus
  favoriteOnly: boolean
  page: number
  pageSize: number
  sortBy: VideoSortBy
  sortOrder: VideoSortOrder
}

type VideoSortBy = 'created_at' | 'title' | 'file_size' | 'duration'
type VideoSortOrder = 'asc' | 'desc'
type AdvancedVideoFilters = {
  platforms: string[]
  fileFormats: string[]
  resolution: string
  fileStatus: VideoFileStatus
  favoriteOnly: boolean
  sortBy: VideoSortBy
  sortOrder: VideoSortOrder
}
type VideoBatchAction = 'favorite' | 'unfavorite' | 'remove'

type LibraryScanReport = {
  roots: string[]
  scanned: number
  added: number
  skipped: number
  missing: number
  missing_items: Array<{ id: string, title: string | null, old_path: string }>
  possible_moves: Array<{ id: string, title: string | null, old_path: string, candidate_path: string }>
  failed: Array<{ path: string, error: string }>
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? ''

class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly body: unknown) {
    super(message)
  }
}

const defaultYtDlpSimpleSettings: YtDlpSimpleSettings = {
  concurrent_fragments: null,
  retries: null,
  write_subs: false,
  write_auto_subs: false,
  sub_langs: 'zh.*,en',
  extract_audio: false,
  audio_format: null,
}

const defaultAria2Settings: Aria2Settings = {
  split: 5,
  max_tries: 3,
  retry_wait: 2,
  bt_stall_timeout: 90,
}

const defaultQbittorrentSettings: QbittorrentSettings = {
  enabled: false,
  base_url: 'http://127.0.0.1:8080',
  username: 'admin',
  password: '',
  bt_stall_timeout: 90,
}

const defaultMaintenanceSettings: MaintenanceSettings = {
  auto_cleanup_enabled: false,
  retention_days: 30,
  clean_download_logs: true,
  clean_download_tasks: true,
}

const downloadPriorityOptions = [
  { value: 1, label: '高', hint: '优先执行' },
  { value: 0, label: '普通', hint: '按序执行' },
  { value: -1, label: '低', hint: '稍后执行' },
]

const playlistQualityOptions = [
  { value: '', label: '自动', hint: '最佳可用' },
  { value: 'bv*[height<=2160]+ba/b[height<=2160]/bv*+ba/b', label: '4K', hint: '≤ 2160p' },
  { value: 'bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b', label: '1080p', hint: '全高清' },
  { value: 'bv*[height<=720]+ba/b[height<=720]/bv*+ba/b', label: '720p', hint: '高清' },
  { value: 'bv*[height<=480]+ba/b[height<=480]/bv*+ba/b', label: '480p', hint: '标清' },
]

function downloadInputLines(value: string): string[] {
  return Array.from(new Set(
    value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => /^(https?|ftp|magnet|thunder):/i.test(line)),
  )).slice(0, 50)
}

function isCompleteHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim())
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && Boolean(parsed.hostname)
  } catch {
    return false
  }
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
    ...options,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(typeof body?.detail === 'string' ? body.detail : '请求未完成，请确认本地服务仍在运行。', response.status, body)
  }
  return body as T
}

function durationLabel(seconds: number | null) {
  if (seconds === null) return '—'
  const value = Math.round(seconds)
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const remaining = value % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${minutes}:${String(remaining).padStart(2, '0')}`
}

function fileSizeLabel(bytes: number | null) {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

function speedLabel(bytes: number | null) {
  return bytes ? `${fileSizeLabel(bytes)}/s` : '—'
}

function etaLabel(seconds: number | null) {
  if (!seconds || seconds < 1) return '—'
  if (seconds < 60) return '不到 1 分钟'
  const minutes = Math.ceil(seconds / 60)
  if (minutes < 60) return `约 ${minutes} 分钟`

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  if (hours < 24) return `约 ${hours} 小时${remainingMinutes ? ` ${remainingMinutes} 分钟` : ''}`

  const days = Math.floor(hours / 24)
  const remainingHours = hours % 24
  return `约 ${days} 天${remainingHours ? ` ${remainingHours} 小时` : ''}`
}

function dateLabel(value: string | null) {
  if (!value) return '—'
  if (/^\d{8}$/.test(value)) return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(new Date(value))
}

function dateInputValue(value: string | null) {
  if (!value) return ''
  if (/^\d{8}$/.test(value)) return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
  return /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : ''
}

function timeLabel(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function elapsedLabel(startedAt: string) {
  const startedAtMilliseconds = new Date(startedAt).getTime()
  if (Number.isNaN(startedAtMilliseconds)) return '正在处理'
  const seconds = Math.max(0, Math.floor((Date.now() - startedAtMilliseconds) / 1000))
  if (seconds < 60) return `已用时 ${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  return `已用时 ${minutes} 分钟${seconds % 60 ? ` ${seconds % 60} 秒` : ''}`
}

function statusLabel(status: DownloadStatus) {
  return {
    queued: '等待中',
    running: '下载中',
    processing: '处理中',
    paused: '已暂停',
    completed: '已完成',
    failed: '下载失败',
    interrupted: '已中断',
    cancelled: '已取消',
  }[status]
}

function taskStatusLabel(task: Download) {
  if (task.status === 'queued' && task.queue_position) return `等待中 · 第 ${task.queue_position} 位`
  const isActiveBt = ['aria2', 'qbittorrent'].includes(task.engine)
    && ['magnet', 'torrent_url', 'torrent_file', 'thunder_bt'].includes(task.source_type || '')
    && ['queued', 'running', 'processing'].includes(task.status)
  if (isActiveBt && task.progress === 0) {
    return task.total_bytes ? '正在寻找节点' : '正在获取文件信息'
  }
  return statusLabel(task.status)
}

function priorityLabel(priority: number) {
  return priority > 0 ? '高' : priority < 0 ? '低' : '普通'
}

function taskProgressGuidance(task: Download) {
  const isActiveBt = ['aria2', 'qbittorrent'].includes(task.engine)
    && ['magnet', 'torrent_url', 'torrent_file', 'thunder_bt'].includes(task.source_type || '')
    && ['queued', 'running', 'processing'].includes(task.status)
  if (!isActiveBt || task.progress > 0) return null
  return task.total_bytes
    ? '文件清单已就绪，正在寻找可用节点。持续没有数据时，任务会自动停止并提示重试。'
    : '正在通过 DHT 获取文件清单。若资源没有可用节点或网络限制了 BT 连接，系统会停止并说明原因。'
}

function inspectStatusLabel(status: InspectJob['status']) {
  return {
    queued: '准备解析',
    running: '正在解析',
    completed: '解析完成',
    failed: '无法解析',
  }[status]
}

function sourceLabel(task: Download) {
  if (task.file_origin === 'local') return '本地文件'
  if (task.uploader) return task.uploader
  try {
    return new URL(task.source_url).hostname.replace(/^www\./, '')
  } catch {
    return '未知上传者'
  }
}

function playlistLabel(task: Download) {
  return task.playlist_id ? `播放列表 · 第 ${task.playlist_index || '—'} 个` : sourceLabel(task)
}

function platformLabel(task: Download) {
  if (task.source_platform) return task.source_platform
  try {
    return new URL(task.source_url).hostname.replace(/^www\./, '')
  } catch {
    return '未知平台'
  }
}

function engineLabel(engine: Download['engine']) {
  return { 'yt-dlp': 'yt-dlp', aria2: 'aria2', qbittorrent: 'qBittorrent', local: '本地文件' }[engine]
}

function sourceTypeLabel(sourceType: string | null) {
  return {
    webpage: '网页媒体',
    direct: '直链文件',
    thunder: '迅雷链接',
    thunder_bt: '迅雷 BT 链接',
    magnet: '磁力链接',
    torrent_url: '种子地址',
    torrent_file: '种子文件',
    local_file: '本地文件',
  }[sourceType || ''] || '下载链接'
}

function videoFormat(task: Download) {
  const match = task.file_path?.match(/\.([a-z0-9]+)$/i)
  return match ? match[1].toUpperCase() : '未知格式'
}

function watchStatusLabel(video: Download) {
  if (video.watched) return '已看完'
  if (video.watch_position > 0 && video.duration) {
    return `已看 ${Math.min(99, Math.round(video.watch_position / video.duration * 100))}%`
  }
  if (video.watch_position > 0) return `看到 ${durationLabel(video.watch_position)}`
  return '未观看'
}

function libraryItemKey(item: LibraryItem) {
  return `${item.kind}:${item.id}`
}

function libraryItemTypeLabel(item: LibraryItem) {
  return {
    video: '视频',
    audio: '音频',
    playlist: '合集',
  }[libraryItemMediaType(item)]
}

function libraryItemMediaType(item: LibraryItem): Exclude<VideoMediaType, ''> {
  if (item.kind === 'playlist') return 'playlist'
  if (item.metadata?.media_type) return item.metadata.media_type
  const extensionStart = item.file_path?.lastIndexOf('.') ?? -1
  return extensionStart >= 0 && audioMediaExtensions.has(item.file_path!.slice(extensionStart).toLowerCase())
    ? 'audio'
    : 'video'
}

function libraryItemTypeIcon(item: LibraryItem): IconName {
  return libraryItemMediaType(item)
}

function libraryPlatformLabel(item: LibraryItem) {
  if (item.source_platform) return item.source_platform
  try {
    return new URL(item.source_url).hostname.replace(/^www\./, '')
  } catch {
    return '未知平台'
  }
}

function libraryResolutionLabel(item: LibraryItem) {
  if (item.kind === 'video') {
    if (libraryItemMediaType(item) === 'audio') {
      return item.metadata?.bit_rate
        ? `${Math.round(item.metadata.bit_rate / 1000)} kbps`
        : '—'
    }
    return item.resolution || '未知清晰度'
  }
  if (item.resolutions.length === 0) return '未知清晰度'
  return item.resolutions.length === 1 ? item.resolutions[0] : '多种清晰度'
}

function configOptionValue(config: string, option: string) {
  const prefix = `${option} `
  const line = config.split('\n').map((value) => value.trim()).find((value) => value.startsWith(prefix))
  return line?.slice(prefix.length).trim() || ''
}

function updateConfigOption(config: string, option: string, value: string) {
  const prefix = `${option} `
  const lines = config.split('\n').map((line) => line.trim()).filter((line) => line && line !== option && !line.startsWith(prefix))
  if (value) lines.push(`${option} ${value}`)
  return lines.join('\n')
}

type SettingSelectOption = {
  value: string
  label: string
}

function SettingSelect({
  ariaLabel,
  value,
  options,
  onChange,
  disabled = false,
}: {
  ariaLabel: string
  value: string | number
  options: SettingSelectOption[]
  onChange: (value: string) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const listboxId = useId()
  const normalizedValue = String(value)
  const selectedOption = options.find((option) => option.value === normalizedValue) || options[0]

  useEffect(() => {
    if (!open) return

    function closeOnOutsidePointer(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }

    document.addEventListener('pointerdown', closeOnOutsidePointer)
    return () => document.removeEventListener('pointerdown', closeOnOutsidePointer)
  }, [open])

  useEffect(() => {
    if (!open) return
    window.requestAnimationFrame(() => {
      const selected = rootRef.current?.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]')
      selected?.focus()
    })
  }, [open])

  function handleTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
    }
  }

  function handleMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const optionButtons = Array.from(rootRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') || [])
    const currentIndex = optionButtons.indexOf(document.activeElement as HTMLButtonElement)

    if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
      triggerRef.current?.focus()
      return
    }
    if (event.key === 'Tab') {
      setOpen(false)
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      optionButtons[event.key === 'Home' ? 0 : optionButtons.length - 1]?.focus()
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const direction = event.key === 'ArrowDown' ? 1 : -1
      const nextIndex = (currentIndex + direction + optionButtons.length) % optionButtons.length
      optionButtons[nextIndex]?.focus()
    }
  }

  return <div className={`setting-select${open ? ' is-open' : ''}${disabled ? ' is-disabled' : ''}`} ref={rootRef}>
    <button
      ref={triggerRef}
      className="setting-select-trigger"
      type="button"
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={listboxId}
      disabled={disabled}
      onClick={() => setOpen((current) => !current)}
      onKeyDown={handleTriggerKeyDown}
    >
      <span>{selectedOption.label}</span>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
    </button>
    {open && <div className="setting-select-menu" id={listboxId} role="listbox" aria-label={ariaLabel} onKeyDown={handleMenuKeyDown}>
      {options.map((option) => <button
        key={option.value}
        type="button"
        role="option"
        aria-selected={option.value === normalizedValue}
        className={option.value === normalizedValue ? 'is-selected' : ''}
        onClick={() => {
          onChange(option.value)
          setOpen(false)
          triggerRef.current?.focus()
        }}
      >
        <span>{option.label}</span>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8 3 3 6-6" /></svg>
      </button>)}
    </div>}
  </div>
}

type IconName = 'add' | 'check' | 'close' | 'download' | 'expand' | 'collapse' | 'play' | 'pause' | 'previous' | 'next' | 'repeat' | 'search' | 'details' | 'trash' | 'library' | 'settings' | 'star' | 'more' | 'video' | 'audio' | 'playlist'

function Icon({ name, size = 16 }: { name: IconName, size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    add: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
    check: <path d="m5 12 4 4 10-10" />,
    close: <><path d="m6 6 12 12" /><path d="m18 6-12 12" /></>,
    download: <><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 20h14" /></>,
    expand: <path d="m6 9 6 6 6-6" />,
    collapse: <path d="m18 15-6-6-6 6" />,
    play: <path d="m9 6 9 6-9 6Z" fill="currentColor" stroke="none" />,
    pause: <><path d="M9 6v12" /><path d="M15 6v12" /></>,
    previous: <><path d="M6 5v14" /><path d="m18 6-9 6 9 6Z" fill="currentColor" stroke="none" /></>,
    next: <><path d="M18 5v14" /><path d="m6 6 9 6-9 6Z" fill="currentColor" stroke="none" /></>,
    repeat: <><path d="m17 2 4 4-4 4" /><path d="M3 11V9a3 3 0 0 1 3-3h15" /><path d="m7 22-4-4 4-4" /><path d="M21 13v2a3 3 0 0 1-3 3H3" /></>,
    search: <><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></>,
    details: <><circle cx="12" cy="12" r="8" /><path d="M12 11v5" /><path d="M12 8h.01" /></>,
    trash: <><path d="M5 7h14" /><path d="M10 11v5M14 11v5" /><path d="M8 7l1-3h6l1 3" /><path d="M7 7l1 13h8l1-13" /></>,
    library: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m10 9 5 3-5 3Z" fill="currentColor" stroke="none" /></>,
    settings: <><path d="M4 7h16M4 12h16M4 17h16" /><circle cx="9" cy="7" r="1.5" fill="currentColor" stroke="none" /><circle cx="15" cy="12" r="1.5" fill="currentColor" stroke="none" /><circle cx="11" cy="17" r="1.5" fill="currentColor" stroke="none" /></>,
    star: <path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z" />,
    more: <><circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" /></>,
    video: <><rect x="3" y="6" width="18" height="12" rx="2" /><path d="m10 9 5 3-5 3Z" fill="currentColor" stroke="none" /></>,
    audio: <><path d="M9 18V7l9-2v11" /><circle cx="6.5" cy="18" r="2.5" /><circle cx="15.5" cy="16" r="2.5" /></>,
    playlist: <><path d="M4 7h10M4 12h10M4 17h7" /><path d="m16 14 5 3-5 3Z" fill="currentColor" stroke="none" /></>,
  }
  return <svg className="ui-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

type AudioWavePlayerProps = {
  src: string
  title: string
  thumbnail: string | null
  initialTime?: number
  autoPlay?: boolean
  repeatMode?: AudioRepeatMode
  onCycleRepeat?: () => void
  onPrevious?: () => void
  onNext?: () => void
  onProgress?: (position: number, duration: number, force: boolean) => void
  onEnded?: () => void
  onError: () => void
}

type AudioRepeatMode = 'off' | 'all' | 'one'

function AudioWavePlayer({ src, title, thumbnail, initialTime = 0, autoPlay = false, repeatMode = 'off', onCycleRepeat, onPrevious, onNext, onProgress, onEnded, onError }: AudioWavePlayerProps) {
  const waveformRef = useRef<HTMLDivElement | null>(null)
  const waveSurferRef = useRef<WaveSurfer | null>(null)
  const onProgressRef = useRef(onProgress)
  const onEndedRef = useRef(onEnded)
  const onErrorRef = useRef(onError)
  const repeatModeRef = useRef(repeatMode)
  const hasNextRef = useRef(Boolean(onNext))
  const [isReady, setIsReady] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [loadingPercent, setLoadingPercent] = useState(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [playbackRate, setPlaybackRate] = useState(1)

  useEffect(() => { onProgressRef.current = onProgress }, [onProgress])
  useEffect(() => { onEndedRef.current = onEnded }, [onEnded])
  useEffect(() => { onErrorRef.current = onError }, [onError])
  useEffect(() => { repeatModeRef.current = repeatMode }, [repeatMode])
  useEffect(() => { hasNextRef.current = Boolean(onNext) }, [onNext])

  useEffect(() => {
    const container = waveformRef.current
    if (!container) return
    let cancelled = false
    let wave: WaveSurfer | null = null

    void import('wavesurfer.js').then(({ default: WaveSurfer }) => {
      if (cancelled) return
      const player = WaveSurfer.create({
        container,
        url: src,
        backend: 'MediaElement',
        autoplay: autoPlay,
        height: 96,
        waveColor: '#526046',
        progressColor: '#d4f65b',
        cursorColor: '#efffae',
        cursorWidth: 2,
        barWidth: 3,
        barGap: 2,
        barRadius: 2,
        barMinHeight: 2,
        dragToSeek: true,
        hideScrollbar: true,
        autoScroll: false,
        autoCenter: false,
        normalize: true,
      })
      wave = player
      waveSurferRef.current = player

      player.on('loading', (percent) => setLoadingPercent(percent))
      player.on('ready', (loadedDuration) => {
        setDuration(loadedDuration)
        setIsReady(true)
        if (initialTime > 0 && initialTime < loadedDuration - 5) {
          player.setTime(initialTime)
        }
      })
      player.on('play', () => setIsPlaying(true))
      player.on('pause', () => {
        setIsPlaying(false)
        onProgressRef.current?.(player.getCurrentTime(), player.getDuration(), true)
      })
      player.on('timeupdate', (time) => {
        setCurrentTime(time)
        onProgressRef.current?.(time, player.getDuration(), false)
      })
      player.on('finish', () => {
        setIsPlaying(false)
        onProgressRef.current?.(player.getDuration(), player.getDuration(), true)
        onEndedRef.current?.()
        if (repeatModeRef.current === 'one' || (repeatModeRef.current === 'all' && !hasNextRef.current)) {
          player.setTime(0)
          void player.play()
        }
      })
      player.on('error', () => onErrorRef.current())
    }).catch(() => {
      if (!cancelled) onErrorRef.current()
    })

    return () => {
      cancelled = true
      if (!wave) return
      onProgressRef.current?.(wave.getCurrentTime(), wave.getDuration(), true)
      wave.destroy()
      if (waveSurferRef.current === wave) waveSurferRef.current = null
    }
  }, [autoPlay, src])

  function seekTo(time: number) {
    const wave = waveSurferRef.current
    if (!wave || !isReady) return
    wave.setTime(Math.max(0, Math.min(duration, time)))
  }

  function changeVolume(nextVolume: number) {
    setVolume(nextVolume)
    waveSurferRef.current?.setVolume(nextVolume)
  }

  function cyclePlaybackRate() {
    const rates = [1, 1.25, 1.5, 2]
    const currentIndex = rates.indexOf(playbackRate)
    const nextRate = rates[(currentIndex + 1) % rates.length]
    setPlaybackRate(nextRate)
    waveSurferRef.current?.setPlaybackRate(nextRate)
  }

  return <section className={`audio-wave-player${isPlaying ? ' is-playing' : ''}`} aria-label={`${title} 音频播放器`}>
    <div className="audio-cover">
      <img src={thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} />
      <span className="audio-equalizer" aria-hidden="true">{[0, 1, 2, 3, 4].map((bar) => <i key={bar} />)}</span>
    </div>
    <div className="audio-wave-content">
      <div className="audio-wave-heading"><span><Icon name="audio" size={18} /> AUDIO WAVE</span><small>{isReady ? '波形已就绪' : `正在读取波形 ${loadingPercent}%`}</small></div>
      <div className="audio-waveform" ref={waveformRef} />
      <label className="audio-seek">
        <span className="sr-only">播放进度</span>
        <input type="range" min="0" max={duration || 0.01} step="0.1" value={Math.min(currentTime, duration || 0)} disabled={!isReady} onChange={(event) => seekTo(Number(event.target.value))} />
      </label>
      <div className="audio-controls">
        <div className="audio-transport">
          <button type="button" onClick={onPrevious} disabled={!onPrevious} aria-label="上一首"><Icon name="previous" size={18} /></button>
          <button type="button" onClick={() => seekTo(currentTime - 10)} disabled={!isReady} aria-label="后退 10 秒">−10</button>
          <button type="button" className="audio-play-button" onClick={() => void waveSurferRef.current?.playPause()} disabled={!isReady} aria-label={isPlaying ? '暂停' : '播放'}><Icon name={isPlaying ? 'pause' : 'play'} size={20} /></button>
          <button type="button" onClick={() => seekTo(currentTime + 10)} disabled={!isReady} aria-label="前进 10 秒">+10</button>
          <button type="button" onClick={onNext} disabled={!onNext} aria-label="下一首"><Icon name="next" size={18} /></button>
          <time>{durationLabel(currentTime)} <span>/</span> {durationLabel(duration)}</time>
        </div>
        <div className="audio-options">
          <label><Icon name="audio" size={16} /><span className="sr-only">音量</span><input type="range" min="0" max="1" step="0.05" value={volume} onChange={(event) => changeVolume(Number(event.target.value))} /></label>
          <button type="button" className={`audio-repeat-button is-${repeatMode}`} onClick={onCycleRepeat} aria-pressed={repeatMode !== 'off'} aria-label={repeatMode === 'one' ? '单曲循环' : repeatMode === 'all' ? '列表循环' : '循环播放已关闭'} title={repeatMode === 'one' ? '单曲循环' : repeatMode === 'all' ? '列表循环' : '循环播放已关闭'}><Icon name="repeat" size={17} />{repeatMode === 'one' && <span>1</span>}</button>
          <button type="button" className="audio-rate-button" onClick={cyclePlaybackRate} aria-label={`播放速度 ${playbackRate} 倍`}>{playbackRate}×</button>
        </div>
      </div>
    </div>
  </section>
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [page, setPage] = useState<Page>('videos')
  const [legalModal, setLegalModal] = useState<'open-source' | 'disclaimer' | null>(null)
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('all')
  const [tasks, setTasks] = useState<Download[]>([])
  const [taskPage, setTaskPage] = useState(1)
  const [taskTotal, setTaskTotal] = useState(0)
  const [taskTotalPages, setTaskTotalPages] = useState(0)
  const [taskCounts, setTaskCounts] = useState({ all: 0, active: 0, attention: 0 })
  const [completedDownloadNotice, setCompletedDownloadNotice] = useState<CompletedDownloadNotice | null>(null)
  const [libraryItems, setLibraryItems] = useState<LibraryItem[]>([])
  const [continueWatching, setContinueWatching] = useState<ContinueWatchingResponse>({ continuing: [], next_up: [] })
  const [sourceFollows, setSourceFollows] = useState<SourceFollow[]>([])
  const [sourceFollowOpen, setSourceFollowOpen] = useState(false)
  const [sourceFollowUrl, setSourceFollowUrl] = useState('')
  const [activeSourceFollowId, setActiveSourceFollowId] = useState<string | null>(null)
  const [selectedFollowEntryUrls, setSelectedFollowEntryUrls] = useState<string[]>([])
  const [sourceFollowWorking, setSourceFollowWorking] = useState<string | null>(null)
  const [sourceFollowNotice, setSourceFollowNotice] = useState<string | null>(null)
  const sourceFollowsStartupCheckedRef = useRef(false)
  const [libraryFilterItems, setLibraryFilterItems] = useState<LibraryItem[]>([])
  const [libraryTotal, setLibraryTotal] = useState(0)
  const [videoLibraryTotal, setVideoLibraryTotal] = useState(0)
  const [audioLibraryTotal, setAudioLibraryTotal] = useState(0)
  const [search, setSearch] = useState('')
  const [videoPlatforms, setVideoPlatforms] = useState<string[]>([])
  const [videoFileFormats, setVideoFileFormats] = useState<string[]>([])
  const [videoResolution, setVideoResolution] = useState('')
  const [videoFileStatus, setVideoFileStatus] = useState<VideoFileStatus>('all')
  const [videoFavoritesOnly, setVideoFavoritesOnly] = useState(false)
  const [videoPage, setVideoPage] = useState(1)
  const [videoPageSize] = useState(10)
  const [videoTotalPages, setVideoTotalPages] = useState(0)
  const [videoSortBy, setVideoSortBy] = useState<VideoSortBy>('created_at')
  const [videoSortOrder, setVideoSortOrder] = useState<VideoSortOrder>('desc')
  const [advancedSearchOpen, setAdvancedSearchOpen] = useState(false)
  const [advancedVideoFilters, setAdvancedVideoFilters] = useState<AdvancedVideoFilters>({
    platforms: [],
    fileFormats: [],
    resolution: '',
    fileStatus: 'all',
    favoriteOnly: false,
    sortBy: 'created_at',
    sortOrder: 'desc',
  })
  const [selectedVideoIds, setSelectedVideoIds] = useState<string[]>([])
  const [videoBatchAction, setVideoBatchAction] = useState<VideoBatchAction | null>(null)
  const [videoBatchWorking, setVideoBatchWorking] = useState(false)
  const [videoBatchNotice, setVideoBatchNotice] = useState<string | null>(null)
  const [settings, setSettings] = useState<DownloadSettings | null>(null)
  const [settingsDraft, setSettingsDraft] = useState<DownloadSettings | null>(null)
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null)
  const [savingSettings, setSavingSettings] = useState<SettingsTab | null>(null)
  const [ytDlpUpdate, setYtDlpUpdate] = useState<YtDlpUpdateCheck | null>(null)
  const [ytDlpUpdateWorking, setYtDlpUpdateWorking] = useState(false)
  const [maintenancePreview, setMaintenancePreview] = useState<MaintenanceCleanupPreview | null>(null)
  const [incompletePreview, setIncompletePreview] = useState<IncompleteResiduePreview | null>(null)
  const [selectedResiduePaths, setSelectedResiduePaths] = useState<string[]>([])
  const [duplicatePreview, setDuplicatePreview] = useState<DuplicateMediaPreview | null>(null)
  const [selectedDuplicateIds, setSelectedDuplicateIds] = useState<string[]>([])
  const [duplicateCleanupConfirmed, setDuplicateCleanupConfirmed] = useState(false)
  const [maintenanceWorking, setMaintenanceWorking] = useState(false)
  const [maintenanceNotice, setMaintenanceNotice] = useState<string | null>(null)
  const [backupPreview, setBackupPreview] = useState<BackupPreview | null>(null)
  const [backupFileName, setBackupFileName] = useState('')
  const [backupRestoreConfirmed, setBackupRestoreConfirmed] = useState(false)
  const [backupWorking, setBackupWorking] = useState(false)
  const [backupNotice, setBackupNotice] = useState<string | null>(null)
  const [libraryScanWorking, setLibraryScanWorking] = useState(false)
  const [libraryScanReport, setLibraryScanReport] = useState<LibraryScanReport | null>(null)
  const [relinkingVideoId, setRelinkingVideoId] = useState<string | null>(null)
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('general')
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [taskDetailDismissed, setTaskDetailDismissed] = useState(false)
  const [mobileTopActionsOpen, setMobileTopActionsOpen] = useState(false)
  const [selectedTask, setSelectedTask] = useState<Download | null>(null)
  const [logs, setLogs] = useState<TaskLog[]>([])
  const [taskOutputs, setTaskOutputs] = useState<DownloadOutput[]>([])
  const [outputPlayer, setOutputPlayer] = useState<{ task: Download, output: DownloadOutput } | null>(null)
  const [outputPlayerNotice, setOutputPlayerNotice] = useState<string | null>(null)
  const [selectedLibraryKey, setSelectedLibraryKey] = useState<string | null>(null)
  const [selectedVideoId, setSelectedVideoId] = useState<string | null>(null)
  const [selectedVideo, setSelectedVideo] = useState<Download | null>(null)
  const [collectionVideos, setCollectionVideos] = useState<Download[]>([])
  const tasksRef = useRef<Download[]>([])
  const selectedTaskIdRef = useRef<string | null>(null)
  const selectedVideoIdRef = useRef<string | null>(null)
  const taskDetailCloseRef = useRef<HTMLButtonElement | null>(null)
  const mobileTopActionsRef = useRef<HTMLDivElement | null>(null)
  const videoDetailCloseRef = useRef<HTMLButtonElement | null>(null)
  const videoDetailMenuRootRef = useRef<HTMLDivElement | null>(null)
  const videoDetailMenuButtonRef = useRef<HTMLButtonElement | null>(null)
  const videoDetailMenuId = useId()
  const [videoDetailMenuOpen, setVideoDetailMenuOpen] = useState(false)
  const [playerVideo, setPlayerVideo] = useState<Download | null>(null)
  const [playlistVideos, setPlaylistVideos] = useState<Download[]>([])
  const [playerNotice, setPlayerNotice] = useState<string | null>(null)
  const [videoCompatibility, setVideoCompatibility] = useState<VideoCompatibility | null>(null)
  const [compatibilityJob, setCompatibilityJob] = useState<MediaDerivativeJob | null>(null)
  const [compatibilityWorking, setCompatibilityWorking] = useState<string | null>(null)
  const [videoSubtitles, setVideoSubtitles] = useState<SubtitleTrack[]>([])
  const [selectedSubtitleFilename, setSelectedSubtitleFilename] = useState('')
  const [audioRepeatMode, setAudioRepeatMode] = useState<AudioRepeatMode>('off')
  const videoPlayerElementRef = useRef<HTMLVideoElement | null>(null)
  const audioLastSavedPositionRef = useRef<{ id: string, position: number } | null>(null)
  const [videoToDelete, setVideoToDelete] = useState<Download | null>(null)
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null)
  const [deleteReport, setDeleteReport] = useState<CleanupReport | null>(null)
  const [deletePreview, setDeletePreview] = useState<DeletePreviewFile[]>([])
  const [deletePreviewLoading, setDeletePreviewLoading] = useState(false)
  const [deletingVideo, setDeletingVideo] = useState(false)
  const [removeVideoFile, setRemoveVideoFile] = useState(true)
  const [renameVideoTarget, setRenameVideoTarget] = useState<Download | null>(null)
  const [renameVideoTitle, setRenameVideoTitle] = useState('')
  const [renameVideoFile, setRenameVideoFile] = useState(false)
  const [renameVideoWorking, setRenameVideoWorking] = useState(false)
  const [renameVideoNotice, setRenameVideoNotice] = useState<string | null>(null)
  const [metadataEditTarget, setMetadataEditTarget] = useState<Download | null>(null)
  const [metadataUploader, setMetadataUploader] = useState('')
  const [metadataUploadDate, setMetadataUploadDate] = useState('')
  const [metadataThumbnail, setMetadataThumbnail] = useState('')
  const [metadataPlaylistId, setMetadataPlaylistId] = useState('')
  const [metadataPlaylistOptions, setMetadataPlaylistOptions] = useState<MetadataPlaylistOption[]>([])
  const [metadataEditLoading, setMetadataEditLoading] = useState(false)
  const [metadataEditWorking, setMetadataEditWorking] = useState(false)
  const [metadataEditNotice, setMetadataEditNotice] = useState<string | null>(null)
  const [upgradeTarget, setUpgradeTarget] = useState<Download | null>(null)
  const [upgradeOptions, setUpgradeOptions] = useState<VideoUpgradeOptions | null>(null)
  const [upgradeFormat, setUpgradeFormat] = useState('')
  const [upgradeWorking, setUpgradeWorking] = useState(false)
  const [upgradeNotice, setUpgradeNotice] = useState<string | null>(null)
  const [localVideoOpen, setLocalVideoOpen] = useState(false)
  const [localDirectoryPath, setLocalDirectoryPath] = useState('')
  const [localMediaFiles, setLocalMediaFiles] = useState<LocalMediaFile[]>([])
  const [selectedLocalMediaPaths, setSelectedLocalMediaPaths] = useState<string[]>([])
  const [localDirectoryScanning, setLocalDirectoryScanning] = useState(false)
  const [localVideoNotice, setLocalVideoNotice] = useState<string | null>(null)
  const [localVideoWorking, setLocalVideoWorking] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [resourceSearchOpen, setResourceSearchOpen] = useState(false)
  const [resourceSearchProviders, setResourceSearchProviders] = useState<ResourceSearchProvider[]>([])
  const [resourceSearchProvider, setResourceSearchProvider] = useState('')
  const [resourceSearchQuery, setResourceSearchQuery] = useState('')
  const [resourceSearchResults, setResourceSearchResults] = useState<ResourceSearchResult[]>([])
  const [selectedResourceUrls, setSelectedResourceUrls] = useState<string[]>([])
  const [resourceSearchWorking, setResourceSearchWorking] = useState(false)
  const [resourceDownloadWorking, setResourceDownloadWorking] = useState(false)
  const [resourceSearchHasSearched, setResourceSearchHasSearched] = useState(false)
  const [resourceSearchNotice, setResourceSearchNotice] = useState<string | null>(null)
  const [duplicateDownloadOpen, setDuplicateDownloadOpen] = useState(false)
  const [retryingDownload, setRetryingDownload] = useState(false)
  const [pausingDownload, setPausingDownload] = useState(false)
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([])
  const [taskActionTarget, setTaskActionTarget] = useState<TaskActionTarget | null>(null)
  const [taskActionNotice, setTaskActionNotice] = useState<string | null>(null)
  const [taskActionWorking, setTaskActionWorking] = useState(false)
  const [removeIncompleteFiles, setRemoveIncompleteFiles] = useState(true)
  const [taskCleanupReport, setTaskCleanupReport] = useState<CleanupReport | null>(null)
  const [videoActionNotice, setVideoActionNotice] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [engineHint, setEngineHint] = useState<EngineHint>('auto')
  const [downloadPriority, setDownloadPriority] = useState(0)
  const [torrentFile, setTorrentFile] = useState<{ name: string, content: string } | null>(null)
  const [media, setMedia] = useState<InspectedMedia | null>(null)
  const [selectedPlaylistUrls, setSelectedPlaylistUrls] = useState<string[]>([])
  const [selectedPlaylistFormat, setSelectedPlaylistFormat] = useState('')
  const [selectedTorrentFileIndexes, setSelectedTorrentFileIndexes] = useState<number[]>([])
  const [torrentFileQuery, setTorrentFileQuery] = useState('')
  const [torrentVisibleFileCount, setTorrentVisibleFileCount] = useState(torrentVisibleFileStep)
  const [selectedFormat, setSelectedFormat] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const [inspectJob, setInspectJob] = useState<InspectJob | null>(null)
  const [inspectProgressOpen, setInspectProgressOpen] = useState(false)
  const inspectLogRef = useRef<HTMLOListElement | null>(null)
  const batchUrls = useMemo(() => downloadInputLines(url), [url])
  const inputLineCount = useMemo(() => url.split(/\r?\n/).filter((line) => line.trim()).length, [url])

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await api<Health>('/health'))
    } catch {
      setHealth(null)
    }
  }, [])

  const refreshTasks = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        page: String(taskPage),
        page_size: '10',
        task_filter: taskFilter,
      })
      const rawResponse = await api<TaskPageResponse | Download[]>(`/api/v1/downloads?${params.toString()}`)
      if (Array.isArray(rawResponse)) {
        const allTasks = rawResponse
        const filteredTasks = allTasks.filter((task) => {
          if (taskFilter === 'active') return ['queued', 'running', 'processing'].includes(task.status)
          if (taskFilter === 'attention') return ['failed', 'interrupted', 'cancelled', 'paused'].includes(task.status)
          return task.status !== 'completed'
        })
        const returnedTasks = allTasks.filter((task) => task.status !== 'completed')
        const start = (taskPage - 1) * 10
        setTasks(filteredTasks.slice(start, start + 10))
        setTaskTotal(filteredTasks.length)
        setTaskTotalPages(Math.ceil(filteredTasks.length / 10))
        setTaskCounts({
          all: returnedTasks.length,
          active: returnedTasks.filter((task) => ['queued', 'running', 'processing'].includes(task.status)).length,
          attention: returnedTasks.filter((task) => ['failed', 'interrupted', 'cancelled', 'paused'].includes(task.status)).length,
        })
        return
      }
      const response = rawResponse
      if (response.total_pages > 0 && taskPage > response.total_pages) {
        setTaskPage(response.total_pages)
        return
      }
      setTasks(response.items)
      setTaskTotal(response.total)
      setTaskTotalPages(response.total_pages)
      setTaskCounts(response.counts)
    } catch {
      setTasks([])
      setTaskTotal(0)
      setTaskTotalPages(0)
      setTaskCounts({ all: 0, active: 0, attention: 0 })
    }
  }, [taskFilter, taskPage])

  const refreshTaskProgress = useCallback(async () => {
    const selectedId = selectedTaskIdRef.current
    const ids = Array.from(new Set([
      ...tasksRef.current.map((task) => task.id),
      ...(selectedId ? [selectedId] : []),
    ]))
    if (ids.length === 0) return
    try {
      const response = await api<TaskProgressResponse>('/api/v1/downloads/progress', {
        method: 'POST',
        body: JSON.stringify({ ids }),
      })
      const progressById = new Map(response.items.map((item) => [item.id, item]))
      setTasks((current) => current.map((task) => {
        const progress = progressById.get(task.id)
        return progress ? { ...task, ...progress } : task
      }))
      setSelectedTask((current) => {
        if (!current) return current
        const progress = progressById.get(current.id)
        return progress ? { ...current, ...progress } : current
      })
      setTaskCounts(response.counts)
    } catch {
      // Keep the last known progress until the next batch refresh.
    }
  }, [])

  const refreshVideos = useCallback(async (filters: VideoFilters) => {
    try {
      const params = new URLSearchParams()
      if (filters.title.trim()) params.set('q', filters.title.trim())
      filters.platforms.forEach((platform) => params.append('platform', platform))
      filters.fileFormats.forEach((format) => params.append('file_format', format))
      if (filters.resolution) params.set('resolution', filters.resolution)
      if (filters.mediaType) params.set('media_type', filters.mediaType)
      const fileStatus = filters.fileStatus ?? videoFileStatus
      if (fileStatus !== 'all') params.set('file_status', fileStatus)
      if (filters.mediaType === 'video') params.set('include_playlists', 'true')
      if (filters.favoriteOnly) params.set('favorite_only', 'true')
      params.set('page', String(filters.page))
      params.set('page_size', String(filters.pageSize))
      params.set('sort_by', filters.sortBy)
      params.set('sort_order', filters.sortOrder)
      const suffix = params.size ? `?${params.toString()}` : ''
      const library = await api<LibraryResponse>(`/api/v1/library/items${suffix}`)
      if (library.total_pages > 0 && filters.page > library.total_pages) {
        setVideoPage(library.total_pages)
        return
      }
      setLibraryItems(library.items)
      setLibraryTotal(library.total)
      setVideoTotalPages(library.total_pages)
    } catch {
      setLibraryItems([])
      setLibraryTotal(0)
      setVideoTotalPages(0)
    }
  }, [videoFileStatus])

  const refreshContinueWatching = useCallback(async () => {
    try {
      setContinueWatching(await api<ContinueWatchingResponse>('/api/v1/library/continue-watching?limit=8'))
    } catch {
      setContinueWatching({ continuing: [], next_up: [] })
    }
  }, [])

  const refreshSourceFollows = useCallback(async (checkStartup = false) => {
    try {
      let follows = await api<SourceFollow[]>('/api/v1/follows')
      setSourceFollows(follows)
      setActiveSourceFollowId((current) => current && follows.some((follow) => follow.id === current)
        ? current
        : follows[0]?.id || null)
      if (checkStartup && !sourceFollowsStartupCheckedRef.current) {
        sourceFollowsStartupCheckedRef.current = true
        await Promise.allSettled(follows
          .filter((follow) => follow.check_on_startup)
          .map((follow) => api<SourceFollow>(`/api/v1/follows/${follow.id}/check`, { method: 'POST' })))
        follows = await api<SourceFollow[]>('/api/v1/follows')
        setSourceFollows(follows)
      }
    } catch {
      setSourceFollows([])
    }
  }, [])

  const refreshLibraryTotals = useCallback(async () => {
    try {
      const [videoLibrary, audioLibrary] = await Promise.all([
        api<LibraryResponse>('/api/v1/library/items?page_size=1&media_type=video&include_playlists=true'),
        api<LibraryResponse>('/api/v1/library/items?page_size=1&media_type=audio'),
      ])
      setVideoLibraryTotal(videoLibrary.total)
      setAudioLibraryTotal(audioLibrary.total)
    } catch {
      // Keep the previous totals until the next successful refresh.
    }
  }, [])

  const refreshVideoFilterItems = useCallback(async () => {
    try {
      const mediaType = page === 'audio' ? 'audio' : 'video'
      const includePlaylists = mediaType === 'video' ? '&include_playlists=true' : ''
      const [library] = await Promise.all([
        api<LibraryResponse>(`/api/v1/library/items?page_size=200&media_type=${mediaType}${includePlaylists}`),
        refreshLibraryTotals(),
      ])
      setLibraryFilterItems(library.items)
    } catch {
      // Keep the previous filter options if the refresh temporarily fails.
    }
  }, [page, refreshLibraryTotals])

  const refreshCollectionVideos = useCallback(async (playlistId: string) => {
    try {
      setCollectionVideos(await api<Download[]>(`/api/v1/playlists/${playlistId}/videos`))
    } catch {
      setCollectionVideos([])
    }
  }, [])

  const refreshSettings = useCallback(async () => {
    try {
      const savedSettings = await api<DownloadSettings>('/api/v1/settings/download')
      const normalizedSettings = {
        ...savedSettings,
        directory_pattern: savedSettings.directory_pattern ?? '{platform}/{year}-{month}/{title}',
        library_dirs: savedSettings.library_dirs || [],
        scan_library_on_startup: savedSettings.scan_library_on_startup ?? true,
        yt_dlp_config: savedSettings.yt_dlp_config || '',
        yt_dlp_simple: savedSettings.yt_dlp_simple || defaultYtDlpSimpleSettings,
        ffmpeg_config: savedSettings.ffmpeg_config || '',
        max_concurrent_downloads: savedSettings.max_concurrent_downloads ?? 5,
        download_rate_limit_kbps: savedSettings.download_rate_limit_kbps ?? 0,
        minimum_free_space_mb: savedSettings.minimum_free_space_mb ?? 1024,
        system_notifications: savedSettings.system_notifications ?? false,
        maintenance: savedSettings.maintenance || defaultMaintenanceSettings,
        aria2: savedSettings.aria2 || defaultAria2Settings,
        qbittorrent: savedSettings.qbittorrent || defaultQbittorrentSettings,
      }
      setSettings(normalizedSettings)
      setSettingsDraft(normalizedSettings)
    } catch {
      setSettings(null)
      setSettingsDraft(null)
    }
  }, [])

  const refreshDetail = useCallback(async (taskId: string) => {
    try {
      const [task, taskLogs, outputs] = await Promise.all([
        api<Download>(`/api/v1/downloads/${taskId}`),
        api<TaskLog[]>(`/api/v1/downloads/${taskId}/logs`),
        api<DownloadOutput[]>(`/api/v1/downloads/${taskId}/outputs`),
      ])
      if (selectedTaskIdRef.current !== taskId) return
      setSelectedTask(task)
      setLogs(taskLogs)
      setTaskOutputs(outputs)
    } catch {
      if (selectedTaskIdRef.current !== taskId) return
      setSelectedTask(null)
      setLogs([])
      setTaskOutputs([])
    }
  }, [])

  const refreshVideoDetail = useCallback(async (videoId: string) => {
    try {
      const video = await api<Download>(`/api/v1/downloads/${videoId}`)
      if (selectedVideoIdRef.current !== videoId) return
      setSelectedVideo(video)
    } catch {
      if (selectedVideoIdRef.current !== videoId) return
      setSelectedVideo(null)
    }
  }, [])

  const selectedLibraryItem = useMemo(
    () => libraryItems.find((item) => libraryItemKey(item) === selectedLibraryKey) || null,
    [libraryItems, selectedLibraryKey],
  )
  const selectedPlaylist = selectedLibraryItem?.kind === 'playlist' ? selectedLibraryItem : null
  const activeSourceFollow = useMemo(
    () => sourceFollows.find((follow) => follow.id === activeSourceFollowId) || null,
    [activeSourceFollowId, sourceFollows],
  )

  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    selectedTaskIdRef.current = selectedTaskId
  }, [selectedTaskId])

  useEffect(() => {
    selectedVideoIdRef.current = selectedVideoId
  }, [selectedVideoId])

  useEffect(() => {
    void refreshHealth()
    void refreshTasks()
    void refreshSettings()
    void refreshSourceFollows(true)
    const timer = window.setInterval(() => {
      void refreshHealth()
    }, 10000)
    return () => window.clearInterval(timer)
  }, [refreshHealth, refreshSettings, refreshSourceFollows, refreshTasks])

  useEffect(() => {
    if (page !== 'tasks') return
    void refreshTaskProgress()
    const timer = window.setInterval(() => {
      void refreshTaskProgress()
    }, 2000)
    return () => window.clearInterval(timer)
  }, [page, refreshTaskProgress])


  useEffect(() => {
    if (page === 'videos' || page === 'audio') void refreshVideos({
      title: search,
      platforms: videoPlatforms,
      fileFormats: videoFileFormats,
      resolution: videoResolution,
      mediaType: page === 'audio' ? 'audio' : 'video',
      fileStatus: videoFileStatus,
      favoriteOnly: videoFavoritesOnly,
      page: videoPage,
      pageSize: videoPageSize,
      sortBy: videoSortBy,
      sortOrder: videoSortOrder,
    })
  }, [page, refreshVideos, search, videoFavoritesOnly, videoFileFormats, videoFileStatus, videoPage, videoPageSize, videoPlatforms, videoResolution, videoSortBy, videoSortOrder])

  useEffect(() => {
    if (page === 'videos') void refreshContinueWatching()
  }, [page, refreshContinueWatching])

  useEffect(() => {
    if (page === 'videos' || page === 'audio') void refreshVideoFilterItems()
  }, [page, refreshVideoFilterItems])

  useEffect(() => {
    if (!selectedTaskId) {
      setSelectedTask(null)
      setLogs([])
      setTaskOutputs([])
      return
    }
    setSelectedTask((current) => current?.id === selectedTaskId ? current : null)
    setLogs([])
    setTaskOutputs([])
    void refreshDetail(selectedTaskId)
  }, [refreshDetail, selectedTaskId])

  useEffect(() => {
    if (!selectedTaskId || !window.matchMedia('(max-width: 1080px)').matches) return
    window.requestAnimationFrame(() => taskDetailCloseRef.current?.focus())
  }, [selectedTaskId])

  useEffect(() => {
    setMobileTopActionsOpen(false)
  }, [page])

  useEffect(() => {
    if (!mobileTopActionsOpen) return

    function closeMobileActions(event: PointerEvent) {
      if (!mobileTopActionsRef.current?.contains(event.target as Node)) setMobileTopActionsOpen(false)
    }

    function closeMobileActionsWithKeyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') setMobileTopActionsOpen(false)
    }

    document.addEventListener('pointerdown', closeMobileActions)
    document.addEventListener('keydown', closeMobileActionsWithKeyboard)
    return () => {
      document.removeEventListener('pointerdown', closeMobileActions)
      document.removeEventListener('keydown', closeMobileActionsWithKeyboard)
    }
  }, [mobileTopActionsOpen])

  useEffect(() => {
    setSelectedTaskIds((current) => current.filter((id) => tasks.some((task) => task.id === id && !['queued', 'running', 'processing'].includes(task.status))))
  }, [tasks])

  useEffect(() => {
    setTaskCleanupReport(null)
  }, [taskActionTarget])

  useEffect(() => {
    if (!selectedVideoId) {
      setSelectedVideo(null)
      return
    }
    setSelectedVideo((current) => current?.id === selectedVideoId ? current : null)
    void refreshVideoDetail(selectedVideoId)
  }, [refreshVideoDetail, selectedVideoId])

  useEffect(() => {
    setVideoDetailMenuOpen(false)
  }, [selectedVideoId])

  useEffect(() => {
    if (!videoDetailMenuOpen) return

    function closeOnOutsidePointer(event: PointerEvent) {
      if (!videoDetailMenuRootRef.current?.contains(event.target as Node)) {
        setVideoDetailMenuOpen(false)
      }
    }

    document.addEventListener('pointerdown', closeOnOutsidePointer)
    window.requestAnimationFrame(() => {
      videoDetailMenuRootRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus()
    })
    return () => document.removeEventListener('pointerdown', closeOnOutsidePointer)
  }, [videoDetailMenuOpen])

  useEffect(() => {
    if (!selectedPlaylist) {
      setCollectionVideos([])
      return
    }
    void refreshCollectionVideos(selectedPlaylist.id)
  }, [refreshCollectionVideos, selectedPlaylist?.id])

  useEffect(() => {
    if (!selectedLibraryKey || !window.matchMedia('(max-width: 800px)').matches) return
    window.requestAnimationFrame(() => videoDetailCloseRef.current?.focus())
  }, [selectedLibraryKey])

  useEffect(() => {
    const stream = new EventSource(`${apiBase}/api/v1/events`)
    stream.addEventListener('download', (event) => {
      let download: Download | null = null
      try {
        download = (JSON.parse(event.data) as { download?: Download }).download || null
      } catch {
        return
      }
      if (!download) return
      setTasks((current) => current.map((task) => task.id === download.id ? download : task))
      setSelectedTask((current) => current?.id === download.id ? download : current)
      if (['completed', 'failed', 'interrupted', 'cancelled'].includes(download.status)) {
        void refreshTasks()
        if (selectedTaskIdRef.current === download.id) void refreshDetail(download.id)
      }
      if (download.status === 'completed') {
        const mediaType = libraryItemMediaType({ kind: 'video', ...download }) === 'audio' ? 'audio' : 'video'
        setCompletedDownloadNotice((current) => current?.downloadId === download.id ? current : {
          downloadId: download.id,
          title: download.title || (mediaType === 'audio' ? '音频' : '视频'),
          mediaType,
        })
        void refreshLibraryTotals()
      }
      if (download.status === 'completed' && (page === 'videos' || page === 'audio')) {
        void refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          fileStatus: videoFileStatus,
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        })
        void refreshVideoFilterItems()
      }
      if (download.status === 'completed' && selectedPlaylist) void refreshCollectionVideos(selectedPlaylist.id)
    })
    stream.addEventListener('log', (event) => {
      let downloadId = ''
      try {
        downloadId = (JSON.parse(event.data) as { download_id?: string }).download_id || ''
      } catch {
        return
      }
      if (!downloadId || selectedTaskIdRef.current !== downloadId) return
      void api<TaskLog[]>(`/api/v1/downloads/${downloadId}/logs`).then((taskLogs) => {
        if (selectedTaskIdRef.current === downloadId) setLogs(taskLogs)
      })
    })
    return () => stream.close()
  }, [page, refreshCollectionVideos, refreshDetail, refreshLibraryTotals, refreshTasks, refreshVideoFilterItems, refreshVideos, search, selectedPlaylist?.id, videoFavoritesOnly, videoFileFormats, videoPage, videoPageSize, videoPlatforms, videoResolution, videoSortBy, videoSortOrder])

  useEffect(() => {
    if (!playerVideo || libraryItemMediaType({ kind: 'video', ...playerVideo }) !== 'video') {
      setVideoSubtitles([])
      setSelectedSubtitleFilename('')
      return
    }
    let cancelled = false
    void api<SubtitleResponse>(`/api/v1/videos/${playerVideo.id}/subtitles`).then((response) => {
      if (cancelled) return
      setVideoSubtitles(response.tracks)
      setSelectedSubtitleFilename(response.preferred_filename || '')
    }).catch(() => {
      if (cancelled) return
      setVideoSubtitles([])
      setSelectedSubtitleFilename('')
    })
    return () => { cancelled = true }
  }, [playerVideo?.id])

  useEffect(() => {
    if (!playerVideo || libraryItemMediaType({ kind: 'video', ...playerVideo }) !== 'video') {
      setVideoCompatibility(null)
      setCompatibilityJob(null)
      return
    }
    let cancelled = false
    setVideoCompatibility(null)
    void Promise.all([
      api<VideoCompatibility>(`/api/v1/videos/${playerVideo.id}/compatibility`),
      api<MediaDerivativeJob[]>(`/api/v1/videos/${playerVideo.id}/compatibility/jobs`),
    ]).then(([compatibility, jobs]) => {
      if (cancelled) return
      setVideoCompatibility(compatibility)
      setCompatibilityJob(jobs[0] || null)
    }).catch(() => {
      if (!cancelled) setVideoCompatibility(null)
    })
    return () => { cancelled = true }
  }, [playerVideo?.id])

  useEffect(() => {
    if (!compatibilityJob || !['queued', 'running'].includes(compatibilityJob.status)) return
    const timer = window.setInterval(() => {
      void api<MediaDerivativeJob>(`/api/v1/media-derivative-jobs/${compatibilityJob.id}`).then((job) => {
        setCompatibilityJob(job)
        if (job.status === 'completed') {
          setPlayerNotice('兼容副本已生成并加入视频管理。')
          void refreshVideos({
            title: search,
            platforms: videoPlatforms,
            fileFormats: videoFileFormats,
            resolution: videoResolution,
            mediaType: 'video',
            fileStatus: videoFileStatus,
            favoriteOnly: videoFavoritesOnly,
            page: videoPage,
            pageSize: videoPageSize,
            sortBy: videoSortBy,
            sortOrder: videoSortOrder,
          })
        }
      }).catch(() => undefined)
    }, 1500)
    return () => window.clearInterval(timer)
  }, [compatibilityJob, refreshVideos, search, videoFavoritesOnly, videoFileFormats, videoFileStatus, videoPage, videoPageSize, videoPlatforms, videoResolution, videoSortBy, videoSortOrder])

  useEffect(() => {
    const videoElement = videoPlayerElementRef.current
    if (!videoElement) return
    const applySelection = () => {
      Array.from(videoElement.textTracks).forEach((track, index) => {
        track.mode = videoSubtitles[index]?.filename === selectedSubtitleFilename ? 'showing' : 'disabled'
      })
    }
    applySelection()
    videoElement.addEventListener('loadedmetadata', applySelection)
    return () => videoElement.removeEventListener('loadedmetadata', applySelection)
  }, [selectedSubtitleFilename, videoSubtitles])

  useEffect(() => {
    if (!playerVideo || libraryItemMediaType({ kind: 'video', ...playerVideo }) !== 'video' || !videoPlayerElementRef.current) return
    const videoElement = videoPlayerElementRef.current
    const resumePosition = playerVideo.watch_position || 0
    let lastSavedPosition = resumePosition

    const restoreProgress = () => {
      if (resumePosition > 0 && resumePosition < videoElement.duration - 5) {
        videoElement.currentTime = resumePosition
        setPlayerNotice(`已从 ${durationLabel(resumePosition)} 继续播放。`)
      }
    }
    const saveProgress = (force = false) => {
      const position = videoElement.currentTime
      if (position <= 0 && resumePosition > 0 && videoElement.readyState < 1) return
      const duration = Number.isFinite(videoElement.duration) ? videoElement.duration : playerVideo.duration
      if (!force && Math.abs(position - lastSavedPosition) < 5) return
      lastSavedPosition = position
      void api<Download>(`/api/v1/videos/${playerVideo.id}/progress`, {
        method: 'PUT',
        body: JSON.stringify({ position, duration }),
      }).then(() => {
        if (force) void refreshContinueWatching()
      }).catch(() => undefined)
    }
    const handleTimeUpdate = () => saveProgress(false)
    const handlePause = () => saveProgress(true)
    const handleEnded = () => {
      saveProgress(true)
      setPlayerVideo((current) => current?.id === playerVideo.id ? {
        ...current,
        watch_position: videoElement.duration,
        watched: 1,
      } : current)
    }

    videoElement.addEventListener('loadedmetadata', restoreProgress)
    videoElement.addEventListener('timeupdate', handleTimeUpdate)
    videoElement.addEventListener('pause', handlePause)
    videoElement.addEventListener('ended', handleEnded)
    return () => {
      saveProgress(true)
      videoElement.removeEventListener('loadedmetadata', restoreProgress)
      videoElement.removeEventListener('timeupdate', handleTimeUpdate)
      videoElement.removeEventListener('pause', handlePause)
      videoElement.removeEventListener('ended', handleEnded)
    }
  }, [playerVideo?.id, refreshContinueWatching])

  useEffect(() => {
    if (!playerVideo || libraryItemMediaType({ kind: 'video', ...playerVideo }) !== 'video' || !videoPlayerElementRef.current) return
    const player = new Plyr(videoPlayerElementRef.current, {
      controls: ['play-large', 'restart', 'rewind', 'play', 'fast-forward', 'progress', 'current-time', 'duration', 'mute', 'volume', 'captions', 'settings', 'pip', 'airplay', 'fullscreen'],
      settings: ['captions', 'speed'],
      seekTime: 10,
      tooltips: { controls: true, seek: true },
      keyboard: { focused: true, global: true },
      fullscreen: {
        enabled: true,
        fallback: true,
        iosNative: true,
      },
    })
    return () => player.destroy()
  }, [playerVideo?.id])

  useEffect(() => {
    if (!inspectJob?.id) return
    const inspectId = inspectJob.id
    let cancelled = false
    let closeTimer: number | undefined

    async function refreshInspect() {
      try {
        const latest = await api<InspectJob>(`/api/v1/downloads/inspect/${inspectId}`)
        if (cancelled) return
        setInspectJob(latest)
        if (latest.status === 'completed') {
          setMedia(latest.media)
          setSelectedFormat(latest.media?.kind === 'video' ? latest.media.formats[0]?.format_id ?? null : null)
          setSelectedPlaylistUrls(latest.media?.kind === 'playlist' ? latest.media.entries.map((entry) => entry.webpage_url) : [])
          setSelectedPlaylistFormat('')
          setSelectedTorrentFileIndexes(latest.media?.kind === 'torrent' ? latest.media.files.filter((file) => isTorrentMediaFile(file.path)).map((file) => file.index) : [])
          setWorking(false)
          window.clearInterval(timer)
          closeTimer = window.setTimeout(() => {
            if (!cancelled) {
              setInspectProgressOpen(false)
            }
          }, 700)
        }
        if (latest.status === 'failed') {
          setNotice(latest.error || '链接暂时无法解析。')
          setWorking(false)
          window.clearInterval(timer)
        }
      } catch (error) {
        if (!cancelled) {
          setNotice(error instanceof Error ? error.message : '解析状态暂时无法读取。')
          setWorking(false)
          setInspectProgressOpen(false)
          setInspectJob(null)
        }
        window.clearInterval(timer)
      }
    }

    const timer = window.setInterval(() => { void refreshInspect() }, 500)
    void refreshInspect()
    return () => {
      cancelled = true
      window.clearInterval(timer)
      if (closeTimer) window.clearTimeout(closeTimer)
    }
  }, [inspectJob?.id])

  useEffect(() => {
    inspectLogRef.current?.scrollTo({ top: inspectLogRef.current.scrollHeight, behavior: 'smooth' })
  }, [inspectJob?.logs.length])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setLegalModal(null)
        setVideoDetailMenuOpen(false)
        setAdvancedSearchOpen(false)
        setVideoBatchAction(null)
        setModalOpen(false)
        setVideoToDelete(null)
        setPlayerVideo(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  const torrentMediaFiles = useMemo(
    () => media?.kind === 'torrent' && Array.isArray(media.files)
      ? media.files.filter((file) => isTorrentMediaFile(file.path))
      : [],
    [media],
  )
  const torrentMediaSize = useMemo(
    () => torrentMediaFiles.length > 0 && torrentMediaFiles.every((file) => file.size !== null)
      ? torrentMediaFiles.reduce((total, file) => total + (file.size || 0), 0)
      : null,
    [torrentMediaFiles],
  )
  const selectedTorrentMediaFiles = useMemo(
    () => torrentMediaFiles.filter((file) => selectedTorrentFileIndexes.includes(file.index)),
    [selectedTorrentFileIndexes, torrentMediaFiles],
  )
  const selectedTorrentMediaSize = useMemo(
    () => selectedTorrentMediaFiles.length > 0 && selectedTorrentMediaFiles.every((file) => file.size !== null)
      ? selectedTorrentMediaFiles.reduce((total, file) => total + (file.size || 0), 0)
      : null,
    [selectedTorrentMediaFiles],
  )
  const filteredTorrentMediaFiles = useMemo(() => {
    const query = torrentFileQuery.trim().toLocaleLowerCase()
    return query
      ? torrentMediaFiles.filter((file) => file.path.toLocaleLowerCase().includes(query))
      : torrentMediaFiles
  }, [torrentFileQuery, torrentMediaFiles])
  const torrentFileGroups = useMemo(() => {
    const groups = new Map<string, typeof torrentMediaFiles>()
    filteredTorrentMediaFiles.forEach((file) => {
      const folder = torrentFolderLabel(file.path)
      groups.set(folder, [...(groups.get(folder) || []), file])
    })
    return Array.from(groups, ([name, files]) => ({ name, files }))
  }, [filteredTorrentMediaFiles, torrentMediaFiles])
  const visibleTorrentFileGroups = useMemo(() => {
    let remaining = torrentVisibleFileCount
    return torrentFileGroups.flatMap((group) => {
      if (remaining <= 0) return []
      const visibleFiles = group.files.slice(0, remaining)
      remaining -= visibleFiles.length
      return visibleFiles.length > 0 ? [{ ...group, visibleFiles }] : []
    })
  }, [torrentFileGroups, torrentVisibleFileCount])
  const availableLocalMediaFiles = useMemo(
    () => Array.isArray(localMediaFiles)
      ? localMediaFiles.filter((file) => !file.already_added)
      : [],
    [localMediaFiles],
  )

  const visibleTasks = useMemo(() => tasks.filter((task) => {
    if (taskFilter === 'active') return ['queued', 'running', 'processing'].includes(task.status)
    if (taskFilter === 'attention') return ['failed', 'interrupted', 'cancelled', 'paused'].includes(task.status)
    return task.status !== 'completed'
  }), [taskFilter, tasks])

  useEffect(() => {
    setSelectedTaskId((current) => (
      current && visibleTasks.some((task) => task.id === current)
        ? current
        : null
    ))
  }, [visibleTasks])

  useEffect(() => {
    if (page !== 'tasks') {
      setTaskDetailDismissed(false)
      return
    }
    if (taskDetailDismissed || selectedTaskId || visibleTasks.length === 0) return
    if (!window.matchMedia('(min-width: 1081px)').matches) return
    setSelectedTaskId(visibleTasks[0].id)
  }, [page, selectedTaskId, taskDetailDismissed, visibleTasks])

  useEffect(() => {
    const selectionExists = selectedLibraryKey
      ? libraryItems.some((item) => libraryItemKey(item) === selectedLibraryKey)
      : false
    if (selectionExists) return
    const firstItem = window.matchMedia('(min-width: 801px)').matches ? libraryItems[0] : null
    setSelectedLibraryKey(firstItem ? libraryItemKey(firstItem) : null)
    setSelectedVideoId(firstItem?.kind === 'video' ? firstItem.id : null)
  }, [libraryItems, selectedLibraryKey])

  const deletableVisibleTaskIds = useMemo(
    () => visibleTasks.filter((task) => !['queued', 'running', 'processing'].includes(task.status)).map((task) => task.id),
    [visibleTasks],
  )
  const selectedVisibleTaskIds = useMemo(
    () => deletableVisibleTaskIds.filter((id) => selectedTaskIds.includes(id)),
    [deletableVisibleTaskIds, selectedTaskIds],
  )
  const allVisibleTasksSelected = deletableVisibleTaskIds.length > 0 && selectedVisibleTaskIds.length === deletableVisibleTaskIds.length

  const videoFilterOptions = useMemo(() => ({
    platforms: Array.from(new Set([...libraryFilterItems.map(libraryPlatformLabel), ...videoPlatforms])).sort(),
    formats: Array.from(new Set([
      ...libraryFilterItems.flatMap((item) => item.kind === 'video' ? [videoFormat(item)] : item.file_formats.map((format) => format.toUpperCase())),
      ...videoFileFormats.map((format) => format.toUpperCase()),
    ])).sort(),
    resolutions: Array.from(new Set([
      ...libraryFilterItems.flatMap((item) => item.kind === 'video' ? [item.resolution || '未知'] : item.resolutions),
      ...(libraryFilterItems.some((item) => item.kind === 'playlist' && item.resolutions.length === 0) ? ['未知'] : []),
    ])).sort(),
  }), [libraryFilterItems, videoFileFormats, videoPlatforms])

  const visibleVideoIds = useMemo(
    () => libraryItems.filter((item) => item.kind === 'video').map((item) => item.id),
    [libraryItems],
  )
  const selectedVisibleVideoIds = useMemo(
    () => visibleVideoIds.filter((id) => selectedVideoIds.includes(id)),
    [selectedVideoIds, visibleVideoIds],
  )
  const allVisibleVideosSelected = visibleVideoIds.length > 0 && selectedVisibleVideoIds.length === visibleVideoIds.length

  useEffect(() => {
    setSelectedVideoIds((current) => current.filter((id) => libraryItems.some((item) => item.kind === 'video' && item.id === id)))
  }, [libraryItems])
  const playerPlaylistPosition = useMemo(
    () => playerVideo ? playlistVideos.findIndex((video) => video.id === playerVideo.id) : -1,
    [playerVideo, playlistVideos],
  )
  const previousPlaylistVideo = playerPlaylistPosition > 0 ? playlistVideos[playerPlaylistPosition - 1] : null
  const nextPlaylistVideo = playerPlaylistPosition >= 0 && playerPlaylistPosition < playlistVideos.length - 1 ? playlistVideos[playerPlaylistPosition + 1] : null
  const currentPlayerMediaType = playerVideo ? libraryItemMediaType({ kind: 'video', ...playerVideo }) : null
  const audioPreviousPlaylistVideo = previousPlaylistVideo || (audioRepeatMode === 'all' && playlistVideos.length > 1 ? playlistVideos[playlistVideos.length - 1] : null)
  const audioNextPlaylistVideo = nextPlaylistVideo || (audioRepeatMode === 'all' && playlistVideos.length > 1 ? playlistVideos[0] : null)
  const outputPlayerIsAudio = outputPlayer?.output.file_type.startsWith('audio/') ?? false

  function closeModal() {
    setModalOpen(false)
    setMedia(null)
    setSelectedPlaylistUrls([])
    setSelectedPlaylistFormat('')
    setSelectedTorrentFileIndexes([])
    setTorrentFileQuery('')
    setTorrentVisibleFileCount(torrentVisibleFileStep)
    setUrl('')
    setEngineHint('auto')
    setDownloadPriority(0)
    setTorrentFile(null)
    setSelectedFormat(null)
    setNotice(null)
    setInspectJob(null)
    setInspectProgressOpen(false)
    setDuplicateDownloadOpen(false)
    setWorking(false)
  }

  function openNewDownload() {
    setModalOpen(true)
    if (url.trim() || torrentFile || !navigator.clipboard?.readText) return
    void navigator.clipboard.readText().then((text) => {
      const clipboardUrls = downloadInputLines(text)
      if (clipboardUrls.length === 0) return
      setUrl(clipboardUrls.join('\n'))
      setEngineHint('auto')
      setNotice(clipboardUrls.length > 1 ? `已从剪贴板识别 ${clipboardUrls.length} 个链接。` : '已从剪贴板识别下载链接。')
    }).catch(() => undefined)
  }

  function openSourceFollows(prefillUrl = '') {
    setSourceFollowUrl(prefillUrl)
    setSourceFollowNotice(null)
    setSelectedFollowEntryUrls([])
    setSourceFollowOpen(true)
    void refreshSourceFollows(false)
  }

  async function addSourceFollow(event: FormEvent) {
    event.preventDefault()
    const followUrl = sourceFollowUrl.trim()
    if (!followUrl) return
    setSourceFollowWorking('add')
    setSourceFollowNotice('正在读取频道或合集，请稍候…')
    try {
      const created = await api<SourceFollow>('/api/v1/follows', {
        method: 'POST',
        body: JSON.stringify({ url: followUrl, check_on_startup: true }),
      })
      await refreshSourceFollows(false)
      setActiveSourceFollowId(created.id)
      setSelectedFollowEntryUrls(created.entries.map((entry) => entry.webpage_url))
      setSourceFollowUrl('')
      setSourceFollowNotice(created.new_count ? `已关注，发现 ${created.new_count} 个尚未下载的视频。` : '已关注，目前没有新增视频。')
    } catch (error) {
      setSourceFollowNotice(error instanceof Error ? error.message : '关注源未添加。')
    } finally {
      setSourceFollowWorking(null)
    }
  }

  async function checkSourceFollow(follow: SourceFollow) {
    setSourceFollowWorking(`check:${follow.id}`)
    setSourceFollowNotice(`正在检查“${follow.title}”…`)
    try {
      const updated = await api<SourceFollow>(`/api/v1/follows/${follow.id}/check`, { method: 'POST' })
      setSourceFollows((current) => current.map((item) => item.id === updated.id ? updated : item))
      setSelectedFollowEntryUrls(updated.entries.map((entry) => entry.webpage_url))
      setSourceFollowNotice(updated.new_count ? `发现 ${updated.new_count} 个尚未下载的视频。` : '检查完成，没有新增视频。')
    } catch (error) {
      await refreshSourceFollows(false)
      setSourceFollowNotice(error instanceof Error ? error.message : '更新检查失败。')
    } finally {
      setSourceFollowWorking(null)
    }
  }

  async function toggleSourceFollowStartup(follow: SourceFollow) {
    setSourceFollowWorking(`setting:${follow.id}`)
    try {
      const updated = await api<SourceFollow>(`/api/v1/follows/${follow.id}`, {
        method: 'PUT',
        body: JSON.stringify({ check_on_startup: !follow.check_on_startup }),
      })
      setSourceFollows((current) => current.map((item) => item.id === updated.id ? updated : item))
    } catch (error) {
      setSourceFollowNotice(error instanceof Error ? error.message : '启动检查设置未保存。')
    } finally {
      setSourceFollowWorking(null)
    }
  }

  async function deleteSourceFollow(follow: SourceFollow) {
    setSourceFollowWorking(`delete:${follow.id}`)
    try {
      await api<{ id: string }>(`/api/v1/follows/${follow.id}`, { method: 'DELETE' })
      setSourceFollows((current) => current.filter((item) => item.id !== follow.id))
      setActiveSourceFollowId((current) => current === follow.id ? null : current)
      setSelectedFollowEntryUrls([])
      setSourceFollowNotice('已取消关注；本地媒体和下载任务没有变化。')
    } catch (error) {
      setSourceFollowNotice(error instanceof Error ? error.message : '无法取消关注。')
    } finally {
      setSourceFollowWorking(null)
    }
  }

  async function downloadFollowEntries(follow: SourceFollow) {
    if (selectedFollowEntryUrls.length === 0) return
    setSourceFollowWorking(`download:${follow.id}`)
    setSourceFollowNotice(`正在创建 ${selectedFollowEntryUrls.length} 个下载任务…`)
    try {
      const response = await api<BatchDownloadResponse & { follow: SourceFollow }>(`/api/v1/follows/${follow.id}/downloads`, {
        method: 'POST',
        body: JSON.stringify({ entry_urls: selectedFollowEntryUrls, priority: downloadPriority }),
      })
      setSourceFollows((current) => current.map((item) => item.id === response.follow.id ? response.follow : item))
      setSelectedFollowEntryUrls([])
      void refreshTasks()
      setSourceFollowNotice(response.failed
        ? `已创建 ${response.created} 个任务，${response.failed} 个未创建；失败项目仍保留在新增列表。`
        : `已创建 ${response.created} 个下载任务。`)
    } catch (error) {
      setSourceFollowNotice(error instanceof Error ? error.message : '下载任务未创建。')
    } finally {
      setSourceFollowWorking(null)
    }
  }

  function closeResourceSearch() {
    if (resourceSearchWorking || resourceDownloadWorking) return
    setResourceSearchOpen(false)
    setResourceSearchResults([])
    setSelectedResourceUrls([])
    setResourceSearchHasSearched(false)
    setResourceSearchNotice(null)
  }

  async function openResourceSearch() {
    setResourceSearchOpen(true)
    setResourceSearchNotice(null)
    if (resourceSearchProviders.length > 0) {
      if (!resourceSearchProvider) setResourceSearchProvider(resourceSearchProviders[0].key)
      return
    }
    setResourceSearchWorking(true)
    try {
      const providers = await api<ResourceSearchProvider[]>('/api/v1/search/providers')
      setResourceSearchProviders(providers)
      setResourceSearchProvider(providers[0]?.key || '')
      if (providers.length === 0) setResourceSearchNotice('当前 yt-dlp 没有可用的关键词搜索平台。')
    } catch (error) {
      setResourceSearchNotice(error instanceof Error ? error.message : '无法读取搜索平台。')
    } finally {
      setResourceSearchWorking(false)
    }
  }

  function selectResourceSearchProvider(provider: string) {
    setResourceSearchProvider(provider)
    setResourceSearchResults([])
    setSelectedResourceUrls([])
    setResourceSearchHasSearched(false)
    setResourceSearchNotice(null)
  }

  async function searchResources(event: FormEvent) {
    event.preventDefault()
    if (!resourceSearchProvider || !resourceSearchQuery.trim()) return
    setResourceSearchWorking(true)
    setResourceSearchHasSearched(false)
    setResourceSearchNotice(null)
    setResourceSearchResults([])
    setSelectedResourceUrls([])
    try {
      const response = await api<ResourceSearchResponse>('/api/v1/search', {
        method: 'POST',
        body: JSON.stringify({
          provider: resourceSearchProvider,
          query: resourceSearchQuery.trim(),
          limit: 20,
        }),
      })
      setResourceSearchResults(response.results)
      setResourceSearchHasSearched(true)
    } catch (error) {
      setResourceSearchHasSearched(true)
      setResourceSearchNotice(error instanceof Error ? error.message : '搜索暂时无法完成。')
    } finally {
      setResourceSearchWorking(false)
    }
  }

  function toggleResourceResult(url: string) {
    setSelectedResourceUrls((current) => current.includes(url)
      ? current.filter((value) => value !== url)
      : [...current, url])
  }

  async function downloadSelectedResources() {
    const selectedResults = resourceSearchResults.filter((result) => selectedResourceUrls.includes(result.webpage_url))
    if (selectedResults.length === 0) return
    setResourceDownloadWorking(true)
    setResourceSearchNotice(`正在批量创建 ${selectedResults.length} 个任务…`)
    try {
      const response = await api<BatchDownloadResponse>('/api/v1/downloads/batch', {
        method: 'POST',
        body: JSON.stringify({
          items: selectedResults.map((result) => ({
            url: result.webpage_url,
            engine_hint: 'yt-dlp',
          })),
        }),
      })
      const succeededUrls = response.successes.map((item) => item.url)
      await refreshTasks()
      if (response.failed === 0) {
        setResourceSearchOpen(false)
        setResourceSearchResults([])
        setSelectedResourceUrls([])
        setResourceSearchHasSearched(false)
        setPage('tasks')
        return
      }
      setSelectedResourceUrls((current) => current.filter((url) => !succeededUrls.includes(url)))
      const firstFailure = response.failures[0]
      const failedResult = selectedResults[firstFailure.index]
      setResourceSearchNotice(
        response.created > 0
          ? `已创建 ${response.created} 个任务，另有 ${response.failed} 个失败。${failedResult?.title || '任务'}：${firstFailure.error}`
          : `${failedResult?.title || '任务'}：${firstFailure.error}`,
      )
    } catch (error) {
      setResourceSearchNotice(error instanceof Error ? error.message : '批量创建下载任务失败。')
    } finally {
      setResourceDownloadWorking(false)
    }
  }

  function chooseTorrentFile(file: File | undefined) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.torrent')) {
      setNotice('请选择扩展名为 .torrent 的种子文件。')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      const content = result.includes(',') ? result.split(',', 2)[1] : ''
      if (!content) {
        setNotice('种子文件读取失败，请重新选择。')
        return
      }
      setTorrentFile({ name: file.name, content })
      setUrl('')
      setEngineHint('auto')
      setMedia(null)
      setNotice(null)
    }
    reader.onerror = () => setNotice('种子文件读取失败，请重新选择。')
    reader.readAsDataURL(file)
  }

  function selectLibraryItem(item: LibraryItem) {
    setSelectedLibraryKey(libraryItemKey(item))
    setSelectedVideoId(item.kind === 'video' ? item.id : null)
    setSelectedVideo(null)
    setVideoActionNotice(null)
  }

  function openCollectionVideoDetail(video: Download) {
    setSelectedVideoId(video.id)
    setSelectedVideo(video)
    setVideoActionNotice(null)
  }

  function closeLibraryDetail() {
    setVideoDetailMenuOpen(false)
    setSelectedLibraryKey(null)
    setSelectedVideoId(null)
    setSelectedVideo(null)
    setCollectionVideos([])
    setVideoActionNotice(null)
  }

  function openLibraryPage(nextPage: 'videos' | 'audio') {
    closeLibraryDetail()
    setCompletedDownloadNotice(null)
    setMobileTopActionsOpen(false)
    setSelectedVideoIds([])
    setVideoResolution('')
    setVideoPage(1)
    setPage(nextPage)
  }

  function closeVideoDetailMenu(restoreFocus = false) {
    setVideoDetailMenuOpen(false)
    if (restoreFocus) {
      window.requestAnimationFrame(() => videoDetailMenuButtonRef.current?.focus())
    }
  }

  function handleVideoDetailMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const menuItems = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)'))
    const currentIndex = menuItems.indexOf(document.activeElement as HTMLButtonElement)

    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeVideoDetailMenu(true)
      return
    }
    if (event.key === 'Tab') {
      setVideoDetailMenuOpen(false)
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      menuItems[event.key === 'Home' ? 0 : menuItems.length - 1]?.focus()
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const direction = event.key === 'ArrowDown' ? 1 : -1
      const nextIndex = (currentIndex + direction + menuItems.length) % menuItems.length
      menuItems[nextIndex]?.focus()
    }
  }

  async function openExternalPlayer(video: Download, player: 'system' | 'iina' | 'vlc') {
    setCompatibilityWorking(`open:${player}`)
    setPlayerNotice(null)
    setVideoActionNotice(null)
    try {
      await api<{ download_id: string, player: string }>(`/api/v1/videos/${video.id}/open`, {
        method: 'POST',
        body: JSON.stringify({ player }),
      })
      const label = player === 'system' ? '系统默认播放器' : player === 'iina' ? 'IINA' : 'VLC'
      const message = `已使用${label}打开。外部播放器中的进度不会自动同步回来。`
      setPlayerNotice(message)
      setVideoActionNotice(message)
    } catch (error) {
      const message = error instanceof Error ? error.message : '无法打开外部播放器。'
      setPlayerNotice(message)
      setVideoActionNotice(message)
    } finally {
      setCompatibilityWorking(null)
    }
  }

  async function startCompatibilityRemux(video: Download) {
    setCompatibilityWorking('remux')
    setPlayerNotice('正在创建兼容封装任务；原始文件保持不变。')
    try {
      const job = await api<MediaDerivativeJob>(`/api/v1/videos/${video.id}/compatibility/remux`, { method: 'POST' })
      setCompatibilityJob(job)
    } catch (error) {
      setPlayerNotice(error instanceof Error ? error.message : '兼容副本任务未创建。')
    } finally {
      setCompatibilityWorking(null)
    }
  }

  function openPlayer(video: Download) {
    if (!video.file_exists) {
      setVideoActionNotice('本地文件不存在，无法播放。')
      return
    }
    setPlayerNotice(null)
    setVideoActionNotice(null)
    const visibleAudioQueue = libraryItemMediaType({ kind: 'video', ...video }) === 'audio'
      ? libraryItems.filter((item): item is LibraryVideo => item.kind === 'video' && libraryItemMediaType(item) === 'audio')
      : []
    setPlaylistVideos(visibleAudioQueue.length > 1 ? visibleAudioQueue : [])
    audioLastSavedPositionRef.current = { id: video.id, position: video.watch_position || 0 }
    const detailedVideo = selectedVideo?.id === video.id ? selectedVideo : video
    setPlayerVideo(detailedVideo)
    void api<Download>(`/api/v1/downloads/${video.id}`).then((detail) => {
      setPlayerVideo((current) => current?.id === video.id ? detail : current)
      if (detail.playlist_id) {
        return api<Download[]>(`/api/v1/playlists/${detail.playlist_id}/videos`).then(setPlaylistVideos)
      }
      return undefined
    }).catch(() => undefined)
  }

  function playPlaylistVideo(video: Download) {
    if (!video.file_exists) {
      setPlayerNotice('这个视频的本地文件不存在。')
      return
    }
    setPlayerNotice(null)
    audioLastSavedPositionRef.current = { id: video.id, position: video.watch_position || 0 }
    setPlayerVideo(video)
  }

  function cycleAudioRepeatMode() {
    setAudioRepeatMode((current) => current === 'off' ? 'all' : current === 'all' ? 'one' : 'off')
  }

  function saveAudioProgress(position: number, duration: number, force: boolean) {
    if (!playerVideo) return
    const saved = audioLastSavedPositionRef.current
    const lastPosition = saved?.id === playerVideo.id ? saved.position : playerVideo.watch_position || 0
    if (!force && Math.abs(position - lastPosition) < 5) return
    audioLastSavedPositionRef.current = { id: playerVideo.id, position }
    void api<Download>(`/api/v1/videos/${playerVideo.id}/progress`, {
      method: 'PUT',
      body: JSON.stringify({ position, duration }),
    }).catch(() => undefined)
  }

  function handleAudioEnded() {
    if (!playerVideo) return
    setPlayerVideo((current) => current?.id === playerVideo.id ? {
      ...current,
      watch_position: current.duration || current.watch_position,
      watched: 1,
    } : current)
    if (audioRepeatMode !== 'one' && audioNextPlaylistVideo) playPlaylistVideo(audioNextPlaylistVideo)
  }

  async function selectVideoSubtitle(filename: string) {
    if (!playerVideo) return
    const previous = selectedSubtitleFilename
    setSelectedSubtitleFilename(filename)
    setPlayerNotice(null)
    try {
      await api<SubtitleResponse>(`/api/v1/videos/${playerVideo.id}/subtitle-preference`, {
        method: 'PUT',
        body: JSON.stringify({ filename: filename || null }),
      })
    } catch (error) {
      setSelectedSubtitleFilename(previous)
      setPlayerNotice(error instanceof Error ? error.message : '字幕选择未能保存。')
    }
  }

  async function revealVideo(video: Download) {
    setVideoActionNotice(null)
    try {
      await api<{ id: string }>(`/api/v1/videos/${video.id}/reveal`, { method: 'POST' })
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '无法打开文件位置。')
    }
  }

  async function selectAndRelinkVideo(video: Download) {
    setRelinkingVideoId(video.id)
    setVideoActionNotice(null)
    try {
      const result = await api<{ cancelled: boolean, video?: Download }>(`/api/v1/videos/${video.id}/relink/select`, { method: 'POST' })
      if (result.cancelled || !result.video) return
      setSelectedVideo(result.video)
      setPlayerVideo((current) => current?.id === result.video?.id ? result.video || current : current)
      setVideoActionNotice('已重新关联本地媒体文件。')
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          fileStatus: videoFileStatus,
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        refreshVideoFilterItems(),
      ])
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '重新定位媒体文件失败。')
    } finally {
      setRelinkingVideoId(null)
    }
  }

  function openAdvancedSearch() {
    setAdvancedVideoFilters({
      platforms: videoPlatforms,
      fileFormats: videoFileFormats,
      resolution: page === 'audio' ? '' : videoResolution,
      fileStatus: videoFileStatus,
      favoriteOnly: videoFavoritesOnly,
      sortBy: videoSortBy,
      sortOrder: videoSortOrder,
    })
    setAdvancedSearchOpen(true)
  }

  function resetAdvancedSearchDraft() {
    setAdvancedVideoFilters({
      platforms: [],
      fileFormats: [],
      resolution: '',
      fileStatus: 'all',
      favoriteOnly: false,
      sortBy: 'created_at',
      sortOrder: 'desc',
    })
  }

  function applyAdvancedSearch() {
    setVideoPlatforms(advancedVideoFilters.platforms)
    setVideoFileFormats(advancedVideoFilters.fileFormats)
    setVideoResolution(page === 'audio' ? '' : advancedVideoFilters.resolution)
    setVideoFileStatus(advancedVideoFilters.fileStatus)
    setVideoFavoritesOnly(advancedVideoFilters.favoriteOnly)
    setVideoSortBy(advancedVideoFilters.sortBy)
    setVideoSortOrder(advancedVideoFilters.sortOrder)
    setVideoPage(1)
    setAdvancedSearchOpen(false)
  }

  function clearVideoFilters() {
    setSearch('')
    setVideoPlatforms([])
    setVideoFileFormats([])
    setVideoResolution('')
    setVideoFileStatus('all')
    setVideoFavoritesOnly(false)
    setVideoSortBy('created_at')
    setVideoSortOrder('desc')
    setVideoPage(1)
  }

  function toggleAdvancedPlatform(platform: string) {
    setAdvancedVideoFilters((current) => ({
      ...current,
      platforms: current.platforms.includes(platform)
        ? current.platforms.filter((value) => value !== platform)
        : [...current.platforms, platform],
    }))
  }

  function toggleAdvancedFileFormat(format: string) {
    setAdvancedVideoFilters((current) => ({
      ...current,
      fileFormats: current.fileFormats.includes(format)
        ? current.fileFormats.filter((value) => value !== format)
        : [...current.fileFormats, format],
    }))
  }

  async function confirmVideoBatchAction() {
    if (!videoBatchAction || selectedVideoIds.length === 0) return
    setVideoBatchWorking(true)
    setVideoBatchNotice(null)
    const action = videoBatchAction
    const selectedIds = [...selectedVideoIds]
    const results = await Promise.allSettled(selectedIds.map((id) => (
      action === 'remove'
        ? api<DeleteVideoResult>(`/api/v1/videos/${id}?remove_file=false`, { method: 'DELETE' })
        : api<Download>(`/api/v1/videos/${id}/favorite`, {
          method: 'PUT',
          body: JSON.stringify({ favorite: action === 'favorite' }),
        })
    )))
    const succeededIds = selectedIds.filter((_, index) => results[index].status === 'fulfilled')
    const failedCount = results.length - succeededIds.length
    if (action === 'remove') {
      if (selectedVideoId && succeededIds.includes(selectedVideoId)) closeLibraryDetail()
      if (playerVideo && succeededIds.includes(playerVideo.id)) setPlayerVideo(null)
    }
    setSelectedVideoIds((current) => current.filter((id) => !succeededIds.includes(id)))
    await Promise.all([
      refreshVideos({
        title: search,
        platforms: videoPlatforms,
        fileFormats: videoFileFormats,
        resolution: videoResolution,
        mediaType: page === 'audio' ? 'audio' : 'video',
        favoriteOnly: videoFavoritesOnly,
        page: videoPage,
        pageSize: videoPageSize,
        sortBy: videoSortBy,
        sortOrder: videoSortOrder,
      }),
      refreshVideoFilterItems(),
      action === 'remove' ? refreshTasks() : Promise.resolve(),
    ])
    setVideoBatchAction(null)
    setVideoBatchWorking(false)
    setVideoBatchNotice(
      failedCount
        ? `已处理 ${succeededIds.length} 项，另有 ${failedCount} 项未完成。`
        : action === 'favorite'
          ? `已收藏 ${succeededIds.length} 项。`
          : action === 'unfavorite'
            ? `已取消收藏 ${succeededIds.length} 项。`
            : `已从${page === 'audio' ? '音频' : '视频'}管理移除 ${succeededIds.length} 项，源文件保持不变。`,
    )
  }

  async function toggleVideoFavorite(video: Download) {
    setVideoActionNotice(null)
    try {
      const updated = await api<Download>(`/api/v1/videos/${video.id}/favorite`, {
        method: 'PUT',
        body: JSON.stringify({ favorite: !video.favorite }),
      })
      setSelectedVideo((current) => current?.id === updated.id ? updated : current)
      setPlayerVideo((current) => current?.id === updated.id ? { ...current, favorite: updated.favorite } : current)
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        updated.playlist_id ? refreshCollectionVideos(updated.playlist_id) : Promise.resolve(),
      ])
      setVideoActionNotice(updated.favorite ? '已加入收藏。' : '已取消收藏。')
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '收藏状态未更新。')
    }
  }

  async function playPlaylist(playlist: LibraryPlaylist) {
    setVideoActionNotice(null)
    try {
      const videos = await api<Download[]>(`/api/v1/playlists/${playlist.id}/videos`)
      const firstPlayableVideo = videos.find((video) => video.file_exists)
      if (!firstPlayableVideo) {
        setVideoActionNotice('这个合集中没有可播放的本地视频。')
        return
      }
      setPlaylistVideos(videos)
      openPlayer(firstPlayableVideo)
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '合集暂时无法播放。')
    }
  }

  async function togglePlaylistFavorite(playlist: LibraryPlaylist) {
    setVideoActionNotice(null)
    try {
      const updated = await api<{ id: string; favorite: number }>(`/api/v1/playlists/${playlist.id}/favorite`, {
        method: 'PUT',
        body: JSON.stringify({ favorite: !playlist.favorite }),
      })
      setLibraryItems((current) => current.map((item) => (
        item.kind === 'playlist' && item.id === updated.id
          ? { ...item, favorite: updated.favorite }
          : item
      )))
      await refreshVideos({
        title: search,
        platforms: videoPlatforms,
        fileFormats: videoFileFormats,
        resolution: videoResolution,
        mediaType: page === 'audio' ? 'audio' : 'video',
        favoriteOnly: videoFavoritesOnly,
        page: videoPage,
        pageSize: videoPageSize,
        sortBy: videoSortBy,
        sortOrder: videoSortOrder,
      })
      setVideoActionNotice(updated.favorite ? '合集已加入收藏。' : '合集已取消收藏。')
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '合集收藏状态未更新。')
    }
  }

  async function toggleVideoWatched(video: Download) {
    setVideoActionNotice(null)
    try {
      const updated = await api<Download>(`/api/v1/videos/${video.id}/watched`, {
        method: 'PUT',
        body: JSON.stringify({ watched: !video.watched }),
      })
      setSelectedVideo(updated)
      if (updated.playlist_id) await refreshCollectionVideos(updated.playlist_id)
      await refreshVideos({
        title: search,
        platforms: videoPlatforms,
        fileFormats: videoFileFormats,
        resolution: videoResolution,
        mediaType: page === 'audio' ? 'audio' : 'video',
        favoriteOnly: videoFavoritesOnly,
        page: videoPage,
        pageSize: videoPageSize,
        sortBy: videoSortBy,
        sortOrder: videoSortOrder,
      })
      setVideoActionNotice(updated.watched ? '已标记为看完。' : '已重置为未看。')
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '观看状态未更新。')
    }
  }

  async function copyVideoFilePath(video: Download) {
    setVideoActionNotice(null)
    if (!video.file_path) {
      setVideoActionNotice('没有可复制的文件路径。')
      return
    }
    try {
      await navigator.clipboard.writeText(video.file_path)
      setVideoActionNotice('文件路径已复制。')
    } catch {
      setVideoActionNotice('无法复制文件路径。')
    }
  }

  function prepareVideoRename(video: Download) {
    setRenameVideoTarget(video)
    setRenameVideoTitle(video.title || '')
    setRenameVideoFile(false)
    setRenameVideoNotice(null)
    setVideoDetailMenuOpen(false)
  }

  function prepareMetadataEdit(video: Download) {
    setMetadataEditTarget(video)
    setMetadataUploader(video.uploader || '')
    setMetadataUploadDate(dateInputValue(video.upload_date))
    setMetadataThumbnail(video.thumbnail || '')
    setMetadataPlaylistId(video.playlist_id || '')
    setMetadataPlaylistOptions([])
    setMetadataEditNotice(null)
    setMetadataEditLoading(true)
    setVideoDetailMenuOpen(false)
    void api<MetadataPlaylistOption[]>('/api/v1/playlists').then(setMetadataPlaylistOptions).catch((error) => {
      setMetadataEditNotice(error instanceof Error ? error.message : '无法读取现有合集。')
    }).finally(() => setMetadataEditLoading(false))
  }

  function prepareVideoUpgrade(video: Download) {
    setUpgradeTarget(video)
    setUpgradeOptions(null)
    setUpgradeFormat('')
    setUpgradeNotice(null)
    setUpgradeWorking(true)
    setVideoDetailMenuOpen(false)
    void api<VideoUpgradeOptions>(`/api/v1/videos/${video.id}/upgrade-options`).then((options) => {
      setUpgradeOptions(options)
      setUpgradeFormat(options.candidates.at(-1)?.format_id || '')
      if (options.candidates.length === 0) setUpgradeNotice('当前文件已经没有可用的更高画质。')
    }).catch((error) => {
      setUpgradeNotice(error instanceof Error ? error.message : '无法检查更高画质。')
    }).finally(() => setUpgradeWorking(false))
  }

  async function startVideoUpgrade() {
    if (!upgradeTarget || !upgradeFormat) return
    setUpgradeWorking(true)
    setUpgradeNotice(null)
    try {
      const task = await api<Download>(`/api/v1/videos/${upgradeTarget.id}/upgrade`, {
        method: 'POST',
        body: JSON.stringify({ format_id: upgradeFormat, priority: 0 }),
      })
      setUpgradeTarget(null)
      await refreshTasks()
      setSelectedTaskId(task.id)
      setTaskDetailDismissed(false)
      setPage('tasks')
    } catch (error) {
      setUpgradeNotice(error instanceof Error ? error.message : '画质升级任务未创建。')
    } finally {
      setUpgradeWorking(false)
    }
  }

  async function finalizeVideoUpgrade(removeOriginalFile: boolean) {
    if (!selectedVideo?.upgrade_from_id) return
    setUpgradeWorking(true)
    setVideoActionNotice(null)
    try {
      const result = await api<{ video: Download }>(`/api/v1/videos/${selectedVideo.id}/upgrade-finalize`, {
        method: 'POST',
        body: JSON.stringify({ confirm: true, remove_original_file: removeOriginalFile }),
      })
      setSelectedVideo(result.video)
      await Promise.all([refreshTasks(), refreshVideos({
        title: search,
        platforms: videoPlatforms,
        fileFormats: videoFileFormats,
        resolution: videoResolution,
        mediaType: 'video',
        favoriteOnly: videoFavoritesOnly,
        page: videoPage,
        pageSize: videoPageSize,
        sortBy: videoSortBy,
        sortOrder: videoSortOrder,
      })])
      setVideoActionNotice(removeOriginalFile ? '新版本已保留，旧版本已移入废纸篓。' : '已保留新旧两个版本。')
    } catch (error) {
      setVideoActionNotice(error instanceof Error ? error.message : '旧版本未处理，两个文件均已保留。')
    } finally {
      setUpgradeWorking(false)
    }
  }

  async function confirmMetadataEdit() {
    if (!metadataEditTarget) return
    setMetadataEditWorking(true)
    setMetadataEditNotice(null)
    try {
      const previousPlaylistId = metadataEditTarget.playlist_id
      const updated = await api<Download>(`/api/v1/videos/${metadataEditTarget.id}/metadata`, {
        method: 'PUT',
        body: JSON.stringify({
          uploader: metadataUploader.trim() || null,
          upload_date: metadataUploadDate || null,
          thumbnail: metadataThumbnail.trim() || null,
          playlist_id: metadataPlaylistId || null,
        }),
      })
      const membershipChanged = previousPlaylistId !== updated.playlist_id
      setPlayerVideo((current) => current?.id === updated.id ? updated : current)
      setMetadataEditTarget(null)
      if (membershipChanged) closeLibraryDetail()
      else setSelectedVideo(updated)
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        refreshVideoFilterItems(),
        selectedPlaylist && !membershipChanged ? refreshCollectionVideos(selectedPlaylist.id) : Promise.resolve(),
      ])
      setVideoActionNotice(membershipChanged ? '媒体信息和合集归属已更新。' : '媒体信息已更新。')
    } catch (error) {
      setMetadataEditNotice(error instanceof Error ? error.message : '媒体信息未能保存。')
    } finally {
      setMetadataEditWorking(false)
    }
  }

  async function confirmVideoRename() {
    if (!renameVideoTarget || !renameVideoTitle.trim()) return
    setRenameVideoWorking(true)
    setRenameVideoNotice(null)
    try {
      const updated = await api<Download>(`/api/v1/videos/${renameVideoTarget.id}/title`, {
        method: 'PUT',
        body: JSON.stringify({ title: renameVideoTitle.trim(), rename_file: renameVideoFile }),
      })
      setSelectedVideo(updated)
      setPlayerVideo((current) => current?.id === updated.id ? updated : current)
      setRenameVideoTarget(null)
      setVideoActionNotice(renameVideoFile ? '标题和本地文件名已更新。' : '显示标题已更新。')
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        updated.playlist_id ? refreshCollectionVideos(updated.playlist_id) : Promise.resolve(),
      ])
    } catch (error) {
      setRenameVideoNotice(error instanceof Error ? error.message : '重命名未完成。')
    } finally {
      setRenameVideoWorking(false)
    }
  }

  async function copyTaskDiagnostics(task: Download) {
    const recentLogs = logs.slice(-20)
    const lines = [
      'Video Downloader 失败诊断',
      `任务 ID：${task.id}`,
      `输入类型：${sourceTypeLabel(task.source_type)}`,
      `下载引擎：${engineLabel(task.engine)} ${task.engine_version || ''}`.trim(),
      `任务状态：${task.status}`,
      `原始输入：${task.source_url}`,
      task.resolved_url && task.resolved_url !== task.source_url ? `实际地址：${task.resolved_url}` : null,
      `错误：${task.error || '无明确错误'}`,
      '',
      `最近日志（${recentLogs.length} 条）：`,
      ...recentLogs.map((log) => `[${log.created_at}] ${log.level.toUpperCase()} ${log.message}`),
    ].filter((line): line is string => line !== null)
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      setTaskActionNotice('失败诊断已复制。')
    } catch {
      setTaskActionNotice('无法复制失败诊断。')
    }
  }

  async function revealTaskOutput(task: Download, output: DownloadOutput) {
    setTaskActionNotice(null)
    try {
      await api<{ id: string }>(`/api/v1/downloads/${task.id}/outputs/${output.index}/reveal`, { method: 'POST' })
    } catch (error) {
      setTaskActionNotice(error instanceof Error ? error.message : '无法打开文件位置。')
    }
  }

  function toggleVideoPlatform(platform: string) {
    setVideoPage(1)
    setVideoPlatforms((current) => current.includes(platform) ? current.filter((value) => value !== platform) : [...current, platform])
  }

  function toggleVideoFileFormat(format: string) {
    setVideoPage(1)
    setVideoFileFormats((current) => current.includes(format) ? current.filter((value) => value !== format) : [...current, format])
  }

  function togglePlaylistEntry(url: string) {
    setSelectedPlaylistUrls((current) => current.includes(url) ? current.filter((value) => value !== url) : [...current, url])
  }

  function toggleTorrentFile(index: number) {
    setSelectedTorrentFileIndexes((current) => current.includes(index) ? current.filter((value) => value !== index) : [...current, index])
  }

  function toggleTorrentGroup(indexes: number[]) {
    setSelectedTorrentFileIndexes((current) => {
      const allSelected = indexes.every((index) => current.includes(index))
      return allSelected
        ? current.filter((index) => !indexes.includes(index))
        : Array.from(new Set([...current, ...indexes]))
    })
  }

  async function inspect(event: FormEvent) {
    event.preventDefault()
    if (!url.trim() && !torrentFile) return
    if (!torrentFile && inputLineCount > 1) {
      if (inputLineCount > 50) {
        setNotice('一次最多创建 50 个下载任务。')
        return
      }
      if (batchUrls.length !== inputLineCount) {
        setNotice('多链接模式下每一行都必须是完整的网页、直链、magnet、thunder:// 或 ftp 链接。')
        return
      }
      await createBatchDownloads()
      return
    }
    setWorking(true)
    setNotice(null)
    setMedia(null)
    setSelectedFormat(null)
    setSelectedPlaylistUrls([])
    setSelectedPlaylistFormat('')
    setSelectedTorrentFileIndexes([])
    setTorrentFileQuery('')
    setTorrentVisibleFileCount(torrentVisibleFileStep)
    try {
      const job = await api<InspectJob>('/api/v1/downloads/inspect/start', {
        method: 'POST',
        body: JSON.stringify({
          url: torrentFile ? null : url.trim(),
          engine_hint: engineHint,
          torrent_name: torrentFile?.name ?? null,
          torrent_base64: torrentFile?.content ?? null,
        }),
      })
      setInspectJob(job)
      setInspectProgressOpen(true)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '链接暂时无法解析。')
      setWorking(false)
    }
  }

  async function createBatchDownloads() {
    if (batchUrls.length < 2) return
    setWorking(true)
    setNotice(null)
    setMedia(null)
    try {
      const response = await api<BatchDownloadResponse>('/api/v1/downloads/batch', {
        method: 'POST',
        body: JSON.stringify({
          items: batchUrls.map((itemUrl) => ({
            url: itemUrl,
            engine_hint: engineHint,
            priority: downloadPriority,
          })),
        }),
      })
      await refreshTasks()
      if (response.failed === 0) {
        setCompletedDownloadNotice(null)
        setPage('tasks')
        setSelectedTaskId(response.successes[0]?.download.id ?? null)
        closeModal()
        return
      }
      const failedDetails = response.failures.slice(0, 3).map((item) => `${item.url}：${item.error}`).join('\n')
      setNotice(`已创建 ${response.created} 个任务，${response.failed} 个链接失败。${failedDetails ? `\n${failedDetails}` : ''}`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '批量下载任务未创建。')
    } finally {
      setWorking(false)
    }
  }

  async function createDownload(replaceExisting = false) {
    if (!media) return
    if (media.kind === 'playlist') {
      await createPlaylistDownload(replaceExisting)
      return
    }
    setWorking(true)
    setNotice(null)
    try {
      const created = await api<Download>('/api/v1/downloads', {
        method: 'POST',
        body: JSON.stringify({
          url: torrentFile ? null : url.trim(),
          inspect_id: inspectJob?.id ?? null,
          engine_hint: engineHint,
          format_id: selectedFormat,
          selected_file_indexes: media.kind === 'torrent' ? selectedTorrentFileIndexes : undefined,
          replace_existing: replaceExisting,
          priority: downloadPriority,
        }),
      })
      await refreshTasks()
      setCompletedDownloadNotice(null)
      setPage('tasks')
      setSelectedTaskId(created.id)
      closeModal()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.message.includes('正在下载')) {
        setDuplicateDownloadOpen(true)
      } else {
        setNotice(error instanceof Error ? error.message : '下载未开始，请稍后重试。')
      }
    } finally {
      setWorking(false)
    }
  }

  async function createPlaylistDownload(replaceExisting = false) {
    if (!media || media.kind !== 'playlist' || !inspectJob?.id) return
    setWorking(true)
    setNotice(null)
    try {
      await api<{ id: string }>('/api/v1/playlists', {
        method: 'POST',
        body: JSON.stringify({ inspect_id: inspectJob.id, entry_urls: selectedPlaylistUrls, format_id: selectedPlaylistFormat || null, replace_existing: replaceExisting, priority: downloadPriority }),
      })
      await refreshTasks()
      setCompletedDownloadNotice(null)
      setPage('tasks')
      closeModal()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.message.includes('正在下载')) {
        setDuplicateDownloadOpen(true)
      } else {
        setNotice(error instanceof Error ? error.message : '播放列表下载未开始，请稍后重试。')
      }
    } finally {
      setWorking(false)
    }
  }

  async function retryDownload(download = selectedTask) {
    if (!download) return
    setRetryingDownload(true)
    try {
      const restarted = await api<Download>(`/api/v1/downloads/${download.id}/retry`, { method: 'POST' })
      setSelectedTask(restarted)
      setSelectedTaskId(restarted.id)
      await refreshTasks()
    } catch (error) {
      setSelectedTask((current) => current ? { ...current, error: error instanceof Error ? error.message : '无法继续下载，请稍后重试。' } : current)
    } finally {
      setRetryingDownload(false)
    }
  }

  async function pauseDownload(download = selectedTask) {
    if (!download) return
    setPausingDownload(true)
    setTaskActionNotice(null)
    try {
      const paused = await api<Download>(`/api/v1/downloads/${download.id}/pause`, { method: 'POST' })
      setSelectedTask(paused)
      setSelectedTaskId(paused.id)
      await refreshTasks()
    } catch (error) {
      setTaskActionNotice(error instanceof Error ? error.message : '无法暂停下载，请稍后重试。')
    } finally {
      setPausingDownload(false)
    }
  }

  async function confirmTaskAction() {
    if (!taskActionTarget) return
    setTaskActionWorking(true)
    setTaskActionNotice(null)
    setTaskCleanupReport(null)
    try {
      if (taskActionTarget.action === 'cancel') {
        const cancelled = await api<Download>(`/api/v1/downloads/${taskActionTarget.task.id}/cancel`, { method: 'POST' })
        setSelectedTask(cancelled)
        await refreshTasks()
      } else if (taskActionTarget.action === 'deleteMany') {
        for (const taskId of taskActionTarget.taskIds) {
          await api<{ id: string }>(`/api/v1/downloads/${taskId}?remove_files=${removeIncompleteFiles}`, { method: 'DELETE' })
        }
        if (selectedTaskId && taskActionTarget.taskIds.includes(selectedTaskId)) {
          const remainingTasks = visibleTasks.filter((task) => !taskActionTarget.taskIds.includes(task.id))
          setSelectedTaskId(remainingTasks[0]?.id ?? null)
          setSelectedTask(null)
          setLogs([])
        }
        setSelectedTaskIds((current) => current.filter((id) => !taskActionTarget.taskIds.includes(id)))
        await refreshTasks()
      } else {
        await api<{ id: string }>(`/api/v1/downloads/${taskActionTarget.task.id}?remove_files=${removeIncompleteFiles}`, { method: 'DELETE' })
        const removedIndex = visibleTasks.findIndex((task) => task.id === taskActionTarget.task.id)
        const nextTask = visibleTasks[removedIndex + 1] || visibleTasks[removedIndex - 1]
        setSelectedTaskId(nextTask?.id ?? null)
        setSelectedTask(null)
        setLogs([])
        await refreshTasks()
      }
      setTaskActionTarget(null)
    } catch (error) {
      setTaskActionNotice(error instanceof Error ? error.message : '操作没有完成，请稍后重试。')
      if (error instanceof ApiError && error.body && typeof error.body === 'object') {
        const report = error.body as Partial<CleanupReport>
        if (Array.isArray(report.trashed_files) && Array.isArray(report.failed_files)) {
          setTaskCleanupReport({ trashed_files: report.trashed_files, failed_files: report.failed_files })
        }
      }
    } finally {
      setTaskActionWorking(false)
    }
  }


  async function saveSettings(tab: SettingsTab) {
    if (!settingsDraft) return
    setSavingSettings(tab)
    setSettingsNotice(null)
    try {
      const request = tab === 'general'
        ? { path: '/api/v1/settings/general', body: { download_dir: settingsDraft.download_dir, directory_pattern: settingsDraft.directory_pattern, library_dirs: settingsDraft.library_dirs, scan_library_on_startup: settingsDraft.scan_library_on_startup, write_thumbnail: settingsDraft.write_thumbnail, write_info_json: settingsDraft.write_info_json, max_concurrent_downloads: settingsDraft.max_concurrent_downloads, download_rate_limit_kbps: settingsDraft.download_rate_limit_kbps, minimum_free_space_mb: settingsDraft.minimum_free_space_mb, system_notifications: settingsDraft.system_notifications } }
        : tab === 'yt-dlp'
          ? { path: '/api/v1/settings/yt-dlp', body: { config: settingsDraft.yt_dlp_config, simple: settingsDraft.yt_dlp_simple } }
          : tab === 'ffmpeg'
            ? { path: '/api/v1/settings/ffmpeg', body: { config: settingsDraft.ffmpeg_config } }
            : tab === 'aria2'
              ? { path: '/api/v1/settings/aria2', body: settingsDraft.aria2 }
              : tab === 'qbittorrent'
                ? { path: '/api/v1/settings/qbittorrent', body: settingsDraft.qbittorrent }
                : { path: '/api/v1/settings/maintenance', body: settingsDraft.maintenance }
      const savedSettings = await api<DownloadSettings>(request.path, {
        method: 'PUT',
        body: JSON.stringify(request.body),
      })
      setSettings(savedSettings)
      setSettingsDraft((current) => current ? {
        ...current,
        ...(tab === 'general' ? {
          download_dir: savedSettings.download_dir,
          directory_pattern: savedSettings.directory_pattern,
          library_dirs: savedSettings.library_dirs,
          scan_library_on_startup: savedSettings.scan_library_on_startup,
          write_thumbnail: savedSettings.write_thumbnail,
          write_info_json: savedSettings.write_info_json,
          max_concurrent_downloads: savedSettings.max_concurrent_downloads,
          download_rate_limit_kbps: savedSettings.download_rate_limit_kbps,
          minimum_free_space_mb: savedSettings.minimum_free_space_mb,
          system_notifications: savedSettings.system_notifications,
        } : tab === 'yt-dlp' ? {
          yt_dlp_config: savedSettings.yt_dlp_config,
          yt_dlp_simple: savedSettings.yt_dlp_simple,
        } : tab === 'ffmpeg' ? {
          ffmpeg_config: savedSettings.ffmpeg_config,
        } : tab === 'aria2' ? {
          aria2: savedSettings.aria2,
        } : tab === 'qbittorrent' ? {
          qbittorrent: savedSettings.qbittorrent,
        } : {
          maintenance: savedSettings.maintenance,
        }),
      } : savedSettings)
      setSettingsNotice(`${tab === 'general' ? '通用设置' : tab === 'yt-dlp' ? 'yt-dlp 参数' : tab === 'ffmpeg' ? 'ffmpeg 参数' : tab === 'aria2' ? 'aria2 设置' : tab === 'qbittorrent' ? 'qBittorrent 设置' : '清理设置'}已保存。`)
    } catch (error) {
      setSettingsNotice(error instanceof Error ? error.message : '设置未保存，请稍后重试。')
    } finally {
      setSavingSettings(null)
    }
  }

  async function checkYtDlpUpdate() {
    setYtDlpUpdateWorking(true)
    setSettingsNotice(null)
    try {
      const result = await api<YtDlpUpdateCheck>('/api/v1/settings/yt-dlp/update-check?refresh=true')
      setYtDlpUpdate(result)
    } catch (error) {
      setSettingsNotice(error instanceof Error ? error.message : '无法检查 yt-dlp 更新。')
    } finally {
      setYtDlpUpdateWorking(false)
    }
  }

  async function refreshMaintenanceData() {
    if (!settingsDraft) return
    setMaintenanceWorking(true)
    setMaintenanceNotice(null)
    try {
      const [history, residues, duplicates] = await Promise.all([
        api<MaintenanceCleanupPreview>(`/api/v1/maintenance/cleanup-preview?retention_days=${settingsDraft.maintenance.retention_days}`),
        api<IncompleteResiduePreview>('/api/v1/maintenance/incomplete'),
        api<DuplicateMediaPreview>('/api/v1/maintenance/duplicates'),
      ])
      setMaintenancePreview(history)
      setIncompletePreview(residues)
      setDuplicatePreview(duplicates)
      setSelectedResiduePaths((current) => current.filter((path) => residues.items.some((item) => item.path === path)))
      setSelectedDuplicateIds((current) => current.filter((id) => duplicates.groups.some((group) => group.items.some((item) => item.id === id))))
      setDuplicateCleanupConfirmed(false)
    } catch (error) {
      setMaintenanceNotice(error instanceof Error ? error.message : '无法读取清理项目。')
    } finally {
      setMaintenanceWorking(false)
    }
  }

  async function runHistoryCleanup() {
    if (!settingsDraft) return
    setMaintenanceWorking(true)
    setMaintenanceNotice(null)
    try {
      const result = await api<MaintenanceCleanupResult>('/api/v1/maintenance/cleanup', {
        method: 'POST',
        body: JSON.stringify({
          retention_days: settingsDraft.maintenance.retention_days,
          clean_download_logs: settingsDraft.maintenance.clean_download_logs,
          clean_download_tasks: settingsDraft.maintenance.clean_download_tasks,
        }),
      })
      setMaintenanceNotice(`已清理 ${result.deleted_logs} 条日志和 ${result.cleaned_tasks} 个旧任务记录；已完成媒体和进行中任务未受影响。`)
      await Promise.all([refreshTasks(), refreshMaintenanceData()])
    } catch (error) {
      setMaintenanceNotice(error instanceof Error ? error.message : '历史记录清理失败。')
      setMaintenanceWorking(false)
    }
  }

  async function cleanupSelectedResidues() {
    if (selectedResiduePaths.length === 0) return
    setMaintenanceWorking(true)
    setMaintenanceNotice(null)
    try {
      const result = await api<IncompleteCleanupResult>('/api/v1/maintenance/incomplete/cleanup', {
        method: 'POST',
        body: JSON.stringify({ paths: selectedResiduePaths }),
      })
      setMaintenanceNotice(`已将 ${result.trashed_files.length} 项移入废纸篓，释放约 ${fileSizeLabel(result.freed_bytes)}${result.failed_files.length ? `；${result.failed_files.length} 项失败` : ''}。`)
      setSelectedResiduePaths([])
      await refreshMaintenanceData()
    } catch (error) {
      setMaintenanceNotice(error instanceof Error ? error.message : '未完成文件清理失败。')
      setMaintenanceWorking(false)
    }
  }

  async function cleanupSelectedDuplicates() {
    if (selectedDuplicateIds.length === 0 || !duplicateCleanupConfirmed) return
    setMaintenanceWorking(true)
    setMaintenanceNotice(null)
    try {
      const result = await api<DuplicateCleanupResult>('/api/v1/maintenance/duplicates/cleanup', {
        method: 'POST',
        body: JSON.stringify({ download_ids: selectedDuplicateIds, confirm: true }),
      })
      if (selectedVideoId && result.trashed_download_ids.includes(selectedVideoId)) closeLibraryDetail()
      setSelectedVideoIds((current) => current.filter((id) => !result.trashed_download_ids.includes(id)))
      setSelectedDuplicateIds([])
      setDuplicateCleanupConfirmed(false)
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        refreshVideoFilterItems(),
        refreshMaintenanceData(),
      ])
      setMaintenanceNotice(`已将 ${result.trashed_download_ids.length} 个重复媒体移入废纸篓，释放约 ${fileSizeLabel(result.freed_bytes)}${result.failed_items.length ? `；${result.failed_items.length} 项失败` : ''}。`)
    } catch (error) {
      setMaintenanceNotice(error instanceof Error ? error.message : '重复媒体清理失败。')
      setMaintenanceWorking(false)
    }
  }

  async function previewBackupFile(file: File) {
    setBackupWorking(true)
    setBackupNotice(null)
    setBackupPreview(null)
    setBackupRestoreConfirmed(false)
    setBackupFileName(file.name)
    try {
      if (file.size > 25 * 1024 * 1024) throw new Error('备份文件不能超过 25 MB。')
      const content = await file.text()
      const preview = await api<BackupPreview>('/api/v1/backup/preview', {
        method: 'POST',
        body: content,
      })
      setBackupPreview(preview)
      setBackupNotice('备份文件已通过校验。请核对预览后再确认恢复。')
    } catch (error) {
      setBackupFileName('')
      setBackupNotice(error instanceof Error ? error.message : '备份文件预览失败。')
    } finally {
      setBackupWorking(false)
    }
  }

  async function restoreBackup() {
    if (!backupPreview || !backupRestoreConfirmed) return
    setBackupWorking(true)
    setBackupNotice(null)
    try {
      const result = await api<BackupRestoreResult>('/api/v1/backup/restore', {
        method: 'POST',
        body: JSON.stringify({ preview_token: backupPreview.preview_token, confirm: true }),
      })
      setBackupNotice(`恢复完成：已恢复 ${result.counts.downloads} 条媒体与任务记录、${result.counts.playlists} 个合集。媒体文件没有被复制或移动。`)
      setBackupPreview(null)
      setBackupFileName('')
      setBackupRestoreConfirmed(false)
      setSelectedTaskId(null)
      setSelectedLibraryKey(null)
      setSelectedVideoId(null)
      await Promise.all([refreshSettings(), refreshTasks(), refreshVideoFilterItems()])
    } catch (error) {
      setBackupNotice(error instanceof Error ? error.message : '备份恢复失败，现有数据未被替换。')
    } finally {
      setBackupWorking(false)
    }
  }

  async function scanLibrary() {
    if (!settingsDraft) return
    setLibraryScanWorking(true)
    setSettingsNotice(null)
    try {
      const savedSettings = await api<DownloadSettings>('/api/v1/settings/general', {
        method: 'PUT',
        body: JSON.stringify({
          download_dir: settingsDraft.download_dir,
          directory_pattern: settingsDraft.directory_pattern,
          library_dirs: settingsDraft.library_dirs,
          scan_library_on_startup: settingsDraft.scan_library_on_startup,
          write_thumbnail: settingsDraft.write_thumbnail,
          write_info_json: settingsDraft.write_info_json,
          max_concurrent_downloads: settingsDraft.max_concurrent_downloads,
          download_rate_limit_kbps: settingsDraft.download_rate_limit_kbps,
          minimum_free_space_mb: settingsDraft.minimum_free_space_mb,
          system_notifications: settingsDraft.system_notifications,
        }),
      })
      setSettings(savedSettings)
      setSettingsDraft(savedSettings)
      const report = await api<LibraryScanReport>('/api/v1/library/scan', { method: 'POST' })
      setLibraryScanReport(report)
      const failureText = report.failed.length ? `，${report.failed.length} 项失败` : ''
      const missingText = report.missing ? `，${report.missing} 条记录文件失效` : ''
      const moveText = report.possible_moves.length ? `，发现 ${report.possible_moves.length} 个疑似移动文件` : ''
      setSettingsNotice(`扫描完成：发现 ${report.scanned} 个媒体文件，新增 ${report.added} 个，跳过 ${report.skipped} 个${missingText}${moveText}${failureText}。`)
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: 1,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        refreshVideoFilterItems(),
      ])
      setVideoPage(1)
    } catch (error) {
      setLibraryScanReport(null)
      setSettingsNotice(error instanceof Error ? error.message : '媒体目录扫描未完成。')
    } finally {
      setLibraryScanWorking(false)
    }
  }

  function openLocalVideo() {
    setLocalDirectoryPath('')
    setLocalMediaFiles([])
    setSelectedLocalMediaPaths([])
    setLocalVideoNotice(null)
    setLocalVideoOpen(true)
  }

  async function selectAndScanLocalMediaDirectory() {
    setLocalDirectoryScanning(true)
    setLocalVideoNotice(null)
    try {
      const response = await api<LocalMediaDirectorySelectionResponse>('/api/v1/videos/local/select-directory', {
        method: 'POST',
      })
      if (response.cancelled) return
      setLocalDirectoryPath(response.directory_path)
      setLocalMediaFiles(response.files)
      setSelectedLocalMediaPaths(
        response.files.filter((file) => !file.already_added).map((file) => file.path),
      )
      setLocalVideoNotice(
        response.total === 0
          ? '目录中没有找到支持的视频或音频文件。'
          : `找到 ${response.total} 个媒体文件，其中 ${response.available} 个可以添加。`,
      )
    } catch (error) {
      setLocalMediaFiles([])
      setSelectedLocalMediaPaths([])
      setLocalVideoNotice(error instanceof Error ? error.message : '媒体目录扫描失败。')
    } finally {
      setLocalDirectoryScanning(false)
    }
  }

  async function addSelectedLocalMedia() {
    if (selectedLocalMediaPaths.length === 0) return
    setLocalVideoWorking(true)
    setLocalVideoNotice(null)
    try {
      const response = await api<LocalMediaBatchResponse>('/api/v1/videos/local/batch', {
        method: 'POST',
        body: JSON.stringify({ file_paths: selectedLocalMediaPaths }),
      })
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        refreshVideoFilterItems(),
      ])
      if (response.failed === 0) {
        setLocalVideoOpen(false)
        setVideoPage(1)
        return
      }
      const addedPaths = new Set(response.successes.map((item) => item.path))
      setLocalMediaFiles((current) => current.map((file) => (
        addedPaths.has(file.path) ? { ...file, already_added: true } : file
      )))
      setSelectedLocalMediaPaths((current) => current.filter((path) => !addedPaths.has(path)))
      setLocalVideoNotice(
        `已添加 ${response.added} 个文件，另有 ${response.failed} 个失败。${response.failures[0]?.error || ''}`,
      )
    } catch (error) {
      setLocalVideoNotice(error instanceof Error ? error.message : '本地媒体文件未添加。')
    } finally {
      setLocalVideoWorking(false)
    }
  }

  function toggleLocalMedia(path: string) {
    setSelectedLocalMediaPaths((current) => current.includes(path)
      ? current.filter((value) => value !== path)
      : [...current, path])
  }

  async function deleteVideo() {
    if (!videoToDelete) return
    setDeletingVideo(true)
    setDeleteNotice(null)
    setDeleteReport(null)
    try {
      await api<DeleteVideoResult>(`/api/v1/videos/${videoToDelete.id}?remove_file=${removeVideoFile}`, { method: 'DELETE' })
      if (selectedVideoId === videoToDelete.id) {
        setSelectedVideoId(null)
        setSelectedVideo(null)
      }
      setCollectionVideos((current) => current.filter((video) => video.id !== videoToDelete.id))
      if (playerVideo?.id === videoToDelete.id) {
        setPlayerVideo(null)
      }
      setVideoToDelete(null)
      await Promise.all([
        refreshVideos({
          title: search,
          platforms: videoPlatforms,
          fileFormats: videoFileFormats,
          resolution: videoResolution,
          mediaType: page === 'audio' ? 'audio' : 'video',
          favoriteOnly: videoFavoritesOnly,
          page: videoPage,
          pageSize: videoPageSize,
          sortBy: videoSortBy,
          sortOrder: videoSortOrder,
        }),
        refreshTasks(),
      ])
    } catch (error) {
      setDeleteNotice(error instanceof Error ? error.message : '视频未删除，请稍后重试。')
      if (error instanceof ApiError && error.body && typeof error.body === 'object') {
        const report = error.body as Partial<CleanupReport>
        if (Array.isArray(report.trashed_files) && Array.isArray(report.failed_files)) {
          setDeleteReport({ trashed_files: report.trashed_files, failed_files: report.failed_files })
        }
      }
    } finally {
      setDeletingVideo(false)
    }
  }

  function loadVideoDeletePreview(video: Download) {
    setDeletePreviewLoading(true)
    setDeleteNotice(null)
    void api<DeletePreviewFile[]>(`/api/v1/videos/${video.id}/delete-preview`)
      .then(setDeletePreview)
      .catch((error) => setDeleteNotice(error instanceof Error ? error.message : '无法读取待删除文件。'))
      .finally(() => setDeletePreviewLoading(false))
  }

  function prepareVideoDelete(video: Download) {
    const shouldRemoveFile = video.file_origin !== 'local'
    setDeleteNotice(null)
    setDeleteReport(null)
    setDeletePreview([])
    setRemoveVideoFile(shouldRemoveFile)
    setVideoToDelete(video)
    setDeletePreviewLoading(false)
    if (shouldRemoveFile) loadVideoDeletePreview(video)
  }

  return (
    <main className="app-shell">
      <a className="skip-link" href="#workspace-main">跳到主要内容</a>
      <aside className="sidebar">
        <button className="brand" onClick={() => openLibraryPage('videos')} aria-label="返回视频管理">
          <span className="brand-mark"><Icon name="download" size={18} /></span>
          <span><strong>Video</strong><small>Downloader</small></span>
        </button>

        <nav aria-label="主导航">
          <button className={page === 'videos' ? 'nav-item is-active' : 'nav-item'} onClick={() => openLibraryPage('videos')}>
            <span className="nav-item-label"><Icon name="video" size={17} /><span>视频管理</span></span><b>{videoLibraryTotal}</b>
          </button>
          <button className={page === 'audio' ? 'nav-item is-active' : 'nav-item'} onClick={() => openLibraryPage('audio')}>
            <span className="nav-item-label"><Icon name="audio" size={17} /><span>音频管理</span></span><b>{audioLibraryTotal}</b>
          </button>
          <button className={page === 'tasks' ? 'nav-item is-active' : 'nav-item'} onClick={() => setPage('tasks')}>
            <span className="nav-item-label"><Icon name="download" size={17} /><span>下载任务</span></span><b>{taskCounts.active}</b>
          </button>
          <button className={page === 'settings' ? 'nav-item is-active' : 'nav-item'} onClick={() => setPage('settings')}>
            <span className="nav-item-label"><Icon name="settings" size={17} /><span>系统设置</span></span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-legal">
            <small>本地工具 · 不内置媒体链接</small>
            <div><button type="button" onClick={() => setLegalModal('open-source')}>开源与致谢</button><span>·</span><button type="button" onClick={() => setLegalModal('disclaimer')}>使用与免责</button></div>
          </div>
          <div className="engine-status" aria-live="polite">
            <span className={health ? 'dot is-online' : 'dot'} />
            <div><b>{health ? '下载服务已就绪' : '正在连接本地服务'}</b><small>{health ? `${health.engine} · ${health.engines?.qbittorrent?.available ? `qBittorrent ${health.engines.qbittorrent.version}` : health.engines?.aria2.version || 'aria2'}` : '请确认本地服务已启动'}</small></div>
          </div>
        </div>
      </aside>

      <section className={page === 'tasks' && selectedTaskId ? 'workspace has-task-detail' : (page === 'videos' || page === 'audio') && selectedLibraryKey ? 'workspace has-video-detail' : 'workspace'} id="workspace-main">
        <header className="topbar topbar-compact">
          <div className="page-heading">
            <h1>{page === 'tasks' ? '下载任务' : page === 'videos' ? '视频管理' : page === 'audio' ? '音频管理' : '设置'}</h1>
          </div>
          {page !== 'settings' && <div className="top-actions">
            <div className="mobile-top-actions" ref={mobileTopActionsRef}>
              <button className="mobile-top-actions-trigger" type="button" aria-label="更多页面操作" aria-haspopup="menu" aria-expanded={mobileTopActionsOpen} onClick={() => setMobileTopActionsOpen((current) => !current)}><Icon name="more" size={18} /></button>
              {mobileTopActionsOpen && <div className="mobile-top-actions-menu" role="menu">
                {(page === 'videos' || page === 'audio') && <button type="button" role="menuitem" onClick={() => { setMobileTopActionsOpen(false); openLocalVideo() }}><Icon name="library" size={16} />添加本地媒体</button>}
                {page === 'videos' && <button type="button" role="menuitem" onClick={() => { setMobileTopActionsOpen(false); openSourceFollows() }}><Icon name="repeat" size={16} />关注更新</button>}
                <button type="button" role="menuitem" onClick={() => { setMobileTopActionsOpen(false); void openResourceSearch() }}><Icon name="search" size={16} />搜索资源</button>
              </div>}
            </div>
            {(page === 'videos' || page === 'audio') && <button className="resource-search-button" onClick={openLocalVideo} aria-label="添加本地媒体"><Icon name="library" size={17} /><span>添加本地媒体</span></button>}
            {page === 'videos' && <button className="resource-search-button" onClick={() => openSourceFollows()} aria-label="关注更新"><Icon name="repeat" size={17} /><span>关注更新{sourceFollows.some((follow) => follow.new_count > 0) ? ` · ${sourceFollows.reduce((total, follow) => total + follow.new_count, 0)}` : ''}</span></button>}
            <button className="resource-search-button" onClick={() => void openResourceSearch()} aria-label="搜索资源"><Icon name="search" size={17} /><span>搜索资源</span></button>
            <button className="new-task-button compact-new-task" onClick={openNewDownload} aria-label="新建下载"><Icon name="add" size={17} /><span>新建下载</span></button>
          </div>}
        </header>

        {page === 'tasks' ? (
          <section className={selectedTaskId ? 'task-workspace has-detail' : 'task-workspace'}>
            <div className="table-area">
              {completedDownloadNotice && <div className="download-completion" role="status">
                <span className="download-completion-icon"><Icon name="check" size={18} /></span>
                <div><b>下载完成</b><span>{completedDownloadNotice.title} 已加入{completedDownloadNotice.mediaType === 'audio' ? '音频' : '视频'}管理。</span></div>
                <button className="download-completion-action" type="button" onClick={() => openLibraryPage(completedDownloadNotice.mediaType === 'audio' ? 'audio' : 'videos')}>查看{completedDownloadNotice.mediaType === 'audio' ? '音频' : '视频'}</button>
                <button className="download-completion-close" type="button" aria-label="关闭下载完成提示" onClick={() => setCompletedDownloadNotice(null)}><Icon name="close" size={17} /></button>
              </div>}
              <div className="table-toolbar">
                <div className="task-toolbar-main">
                  <div className="filters" aria-label="下载筛选">
                    {([
                      ['all', '全部', taskCounts.all],
                      ['active', '进行中', taskCounts.active],
                      ['attention', '需处理', taskCounts.attention],
                    ] as Array<[TaskFilter, string, number]>).map(([value, label, count]) => (
                      <button key={value} className={taskFilter === value ? 'filter is-active' : 'filter'} onClick={() => { setTaskFilter(value); setTaskPage(1); setSelectedTaskIds([]) }}>
                        {label}<span>{count}</span>
                      </button>
                    ))}
                  </div>
                  {selectedVisibleTaskIds.length > 0 && <div className="task-selection-actions" role="status">
                    <span>已选 {selectedVisibleTaskIds.length} 项</span>
                    <button className="delete-selected-tasks" onClick={() => { setTaskActionNotice(null); setTaskActionTarget({ taskIds: selectedVisibleTaskIds, action: 'deleteMany' }) }}>
                      <Icon name="trash" size={14} />删除已选
                    </button>
                  </div>}
                  {visibleTasks.length > 0 && <p className="task-list-count">{visibleTasks.length} 条下载</p>}
                </div>
              </div>

              {visibleTasks.length === 0 ? (
                <div className="empty-state empty-state-compact">
                  <span><Icon name="download" size={24} /></span>
                  <h2>{taskFilter === 'all' ? '没有待处理的下载任务' : '这里暂时没有下载任务'}</h2>
                  <p>{taskFilter === 'all' ? '新建任务后，进行中和需要处理的任务会显示在这里。' : '切换筛选条件查看其他下载。'}</p>
                </div>
              ) : (<>
                <div className="table-wrap">
                  <table className="data-table task-table master-task-table">
                    <colgroup><col /><col /><col /><col /><col /></colgroup>
                    <thead><tr><th className="task-select-column"><input type="checkbox" aria-label="全选可删除任务" checked={allVisibleTasksSelected} disabled={deletableVisibleTaskIds.length === 0} onChange={(event) => setSelectedTaskIds(event.target.checked ? deletableVisibleTaskIds : [])} /></th><th>任务</th><th>状态与进度</th><th>传输</th><th>开始时间</th></tr></thead>
                    <tbody>
                      {visibleTasks.map((task) => (
                        <tr key={task.id} className={[selectedTaskId === task.id ? 'is-selected' : '', selectedTaskIds.includes(task.id) ? 'is-batch-selected' : ''].filter(Boolean).join(' ')} tabIndex={0} aria-label={`查看任务详情：${task.title || '正在解析链接'}`} onClick={() => { setTaskDetailDismissed(false); setSelectedTaskId(task.id) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setTaskDetailDismissed(false); setSelectedTaskId(task.id) } }}>
                          <td className="task-select-cell"><input type="checkbox" aria-label={`选择任务：${task.title || '正在解析链接'}`} title={['queued', 'running', 'processing'].includes(task.status) ? '下载进行中，需先取消后删除' : '选择任务'} checked={selectedTaskIds.includes(task.id)} disabled={['queued', 'running', 'processing'].includes(task.status)} onClick={(event) => event.stopPropagation()} onChange={(event) => { event.stopPropagation(); setSelectedTaskIds((current) => event.target.checked ? [...current, task.id] : current.filter((id) => id !== task.id)) }} /></td>
                          <td className="title-cell">
                            <div className="title-cell-content">
                              <div className="mini-cover">{task.thumbnail ? <img src={task.thumbnail} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /> : <img src={videoPlaceholder} alt="" />}</div>
                              <div><b>{task.title || '正在解析链接…'}</b><small>{task.playlist_id ? playlistLabel(task) : `${sourceTypeLabel(task.source_type)} · ${engineLabel(task.engine)}`}</small></div>
                            </div>
                          </td>
                          <td className="task-state-cell"><div><span className={`status status-${task.status}`}>{taskStatusLabel(task)}</span><b>{Math.round(task.progress)}%</b></div><span className="progress-track"><i style={{ width: `${Math.max(task.progress, task.status === 'queued' ? 2 : 0)}%` }} /></span></td>
                          <td className="task-transfer-cell"><b>{speedLabel(task.speed)}</b><small>{task.eta ? `剩余 ${etaLabel(task.eta)}` : task.total_bytes ? `${fileSizeLabel(task.downloaded_bytes)} / ${fileSizeLabel(task.total_bytes)}` : '等待传输信息'}</small></td>
                          <td className="task-time-cell">{dateLabel(task.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <nav className="library-pagination" aria-label="下载任务分页">
                  <span>共 {taskTotal} 项{taskTotalPages > 1 ? ` · 第 ${taskPage} / ${taskTotalPages} 页` : ''}</span>
                  {taskTotalPages > 1 && <div>
                    <button type="button" disabled={taskPage <= 1} onClick={() => setTaskPage((current) => Math.max(1, current - 1))}>上一页</button>
                    <button type="button" disabled={taskTotalPages === 0 || taskPage >= taskTotalPages} onClick={() => setTaskPage((current) => current + 1)}>下一页</button>
                  </div>}
                </nav>
              </>)}
            </div>

            {selectedTaskId && (
              <aside className="detail-panel task-detail-drawer" aria-label="下载详情">
                <header className="detail-header detail-identity">
                  <div className="detail-cover"><img src={selectedTask?.thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></div>
                  <div><p className="eyebrow">任务详情</p><h2>{selectedTask?.title || '正在读取下载信息…'}</h2>{selectedTask && <p className="detail-subtitle">{platformLabel(selectedTask)} · {sourceTypeLabel(selectedTask.source_type)} · {engineLabel(selectedTask.engine)}</p>}</div>
                  <button ref={taskDetailCloseRef} className="icon-button task-detail-close" type="button" onClick={() => { setTaskDetailDismissed(true); setSelectedTaskId(null) }} aria-label="关闭任务详情" title="关闭任务详情"><Icon name="close" size={18} /></button>
                </header>

                {selectedTask && <>
                  <div className="detail-summary task-detail-summary">
                    <div><span className={`status status-${selectedTask.status}`}>{taskStatusLabel(selectedTask)}</span><b>{Math.round(selectedTask.progress)}%</b></div>
                    <span className="progress-track"><i style={{ width: `${Math.max(selectedTask.progress, selectedTask.status === 'queued' ? 2 : 0)}%` }} /></span>
                    <p>{fileSizeLabel(selectedTask.downloaded_bytes)} / {fileSizeLabel(selectedTask.total_bytes)} · {speedLabel(selectedTask.speed)} · 剩余 {etaLabel(selectedTask.eta)}</p>
                  </div>
                  <div className="detail-action-bar task-detail-actions">
                    <div>
                      {['queued', 'running', 'processing'].includes(selectedTask.status) && <button className="detail-primary-action action-with-icon" type="button" onClick={() => void pauseDownload(selectedTask)} disabled={pausingDownload}><Icon name="pause" size={15} />{pausingDownload ? '正在暂停…' : '暂停下载'}</button>}
                      {selectedTask.status !== 'completed' && !['queued', 'running', 'processing'].includes(selectedTask.status) && <button className="detail-primary-action action-with-icon" type="button" onClick={() => void retryDownload(selectedTask)} disabled={retryingDownload}><Icon name="download" size={15} />{retryingDownload ? '正在继续…' : ['cancelled', 'interrupted', 'paused'].includes(selectedTask.status) ? '继续下载' : '重试下载'}</button>}
                      {['failed', 'interrupted'].includes(selectedTask.status) && <button className="detail-secondary-action action-with-icon" type="button" onClick={() => void copyTaskDiagnostics(selectedTask)}><Icon name="details" size={15} />复制失败诊断</button>}
                      {['queued', 'running', 'processing', 'paused'].includes(selectedTask.status) && <button className="detail-secondary-action task-cancel-action action-with-icon" type="button" onClick={() => { setTaskActionNotice(null); setTaskActionTarget({ task: selectedTask, action: 'cancel' }) }}><Icon name="close" size={15} />取消下载</button>}
                      {!['queued', 'running', 'processing'].includes(selectedTask.status) && <button className="detail-danger-action action-with-icon" type="button" onClick={() => { setTaskActionNotice(null); setTaskActionTarget({ task: selectedTask, action: 'delete' }) }}><Icon name="trash" size={15} />删除任务</button>}
                    </div>
                  </div>
                  <section className="detail-section">
                    <div className="detail-section-heading"><h3>任务信息</h3><span>{dateLabel(selectedTask.created_at)} 开始</span></div>
                    <dl className="detail-grid">
                      <div><dt>下载引擎</dt><dd>{engineLabel(selectedTask.engine)}</dd></div>
                      <div><dt>输入类型</dt><dd>{sourceTypeLabel(selectedTask.source_type)}</dd></div>
                      <div><dt>任务优先级</dt><dd>{priorityLabel(selectedTask.priority)}</dd></div>
                      <div><dt>队列位置</dt><dd>{selectedTask.queue_position ? `第 ${selectedTask.queue_position} 位` : '—'}</dd></div>
                      <div><dt>保存格式</dt><dd>{selectedTask.resolution || '下载后确定'}</dd></div>
                      <div><dt>来源平台</dt><dd>{platformLabel(selectedTask)}</dd></div>
                    </dl>
                  </section>
                  {taskProgressGuidance(selectedTask) && <p className="task-progress-guidance" role="status">{taskProgressGuidance(selectedTask)}</p>}
                  {selectedTask.error && <p className="error-message">{selectedTask.error}</p>}
                  {taskActionNotice && !taskActionTarget && <p className="notice" role="status">{taskActionNotice}</p>}
                  <details className="detail-section detail-disclosure">
                    <summary><span>来源与保存</span><small>输入链接与本地路径</small></summary>
                    <div className="detail-source-section">
                      <div className="detail-link"><span>原始输入</span>{selectedTask.source_url.startsWith('http') ? <a href={selectedTask.source_url} target="_blank" rel="noreferrer">打开输入链接 ↗</a> : <code>{selectedTask.source_url}</code>}</div>
                      {selectedTask.resolved_url && selectedTask.resolved_url !== selectedTask.source_url && <div className="detail-link"><span>实际下载地址</span><code>{selectedTask.resolved_url}</code></div>}
                      {selectedTask.file_path && <div className="detail-path"><span>保存位置</span><code>{selectedTask.file_path}</code></div>}
                    </div>
                  </details>
                  {taskOutputs.length > 0 && <section className="detail-section">
                    <div className="detail-section-heading"><h3>输出文件</h3><span>{taskOutputs.length} 个</span></div>
                    <ol className="output-file-list">
                      {taskOutputs.map((output) => <li key={output.index}>
                        <div><b>{output.relative_path}</b><small>{fileSizeLabel(output.size)} · {output.file_type}</small></div>
                        <span>{output.playable && <button type="button" onClick={() => { setOutputPlayerNotice(null); setOutputPlayer({ task: selectedTask, output }) }}>播放</button>}<button type="button" onClick={() => void revealTaskOutput(selectedTask, output)}>打开文件位置</button></span>
                      </li>)}
                    </ol>
                  </section>}
                  <section className="log-section">
                    <div className="log-heading"><h3>下载日志</h3><span>{logs.length} 条</span></div>
                    {logs.length === 0 ? <p className="muted">开始下载后，这里会记录关键步骤和失败原因。</p> : (
                      <ol className="log-list">
                        {logs.map((log) => <li key={log.id} className={`log-${log.level}`}><time>{timeLabel(log.created_at)}</time><p>{log.message}</p></li>)}
                      </ol>
                    )}
                  </section>
                </>}
              </aside>
            )}
          </section>
        ) : page === 'videos' || page === 'audio' ? (
          <section className={selectedLibraryKey ? 'video-workspace has-detail' : 'video-workspace'}>
            <div className="table-area">
              {page === 'videos' && (continueWatching.continuing.length > 0 || continueWatching.next_up.length > 0) && <section className="continue-watching" aria-labelledby="continue-watching-title">
                <div className="continue-watching-heading">
                  <div><p className="eyebrow">个人播放进度</p><h2 id="continue-watching-title">继续观看</h2></div>
                  <span>{continueWatching.continuing.length > 0 ? `${continueWatching.continuing.length} 个看到一半` : '接着看下一集'}</span>
                </div>
                <div className="continue-watching-list">
                  {[...continueWatching.continuing, ...continueWatching.next_up].slice(0, 8).map((item) => {
                    const progress = item.video.duration && item.video.watch_position > 0
                      ? Math.min(100, Math.round(item.video.watch_position / item.video.duration * 100))
                      : 0
                    return <button key={`${item.reason}-${item.video.id}`} type="button" onClick={() => openPlayer(item.video)} disabled={!item.video.file_exists}>
                      <span className="continue-cover"><img src={item.video.thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /><i><Icon name="play" size={16} /></i></span>
                      <span className="continue-copy"><small>{item.reason === 'next' ? `${item.playlist_title || '当前合集'} · 下一集` : `已看 ${progress}%`}</small><b title={item.video.title || undefined}>{item.video.title || '未命名视频'}</b>{item.reason === 'continue' && <span className="continue-progress"><i style={{ width: `${progress}%` }} /></span>}</span>
                    </button>
                  })}
                </div>
              </section>}
              <div className="video-library-toolbar">
                <label className="title-search-field">
                  <span className="sr-only">按标题搜索{page === 'audio' ? '音频' : '视频或合集'}</span>
                  <Icon name="search" size={20} />
                  <input value={search} onChange={(event) => { setSearch(event.target.value); setVideoPage(1) }} placeholder={`按标题搜索${page === 'audio' ? '音频' : '视频或合集'}`} autoComplete="off" />
                  {search && <button type="button" className="icon-button" onClick={() => { setSearch(''); setVideoPage(1) }} aria-label="清除搜索"><Icon name="close" size={16} /></button>}
                </label>
                <button className="advanced-search-button" type="button" onClick={openAdvancedSearch} aria-haspopup="dialog">
                  <Icon name="settings" size={16} />
                  高级搜索
                  {(videoPlatforms.length + videoFileFormats.length + (page === 'videos' && videoResolution ? 1 : 0) + (videoFileStatus !== 'all' ? 1 : 0) + (videoFavoritesOnly ? 1 : 0) + (videoSortBy !== 'created_at' || videoSortOrder !== 'desc' ? 1 : 0)) > 0 && <b>{videoPlatforms.length + videoFileFormats.length + (page === 'videos' && videoResolution ? 1 : 0) + (videoFileStatus !== 'all' ? 1 : 0) + (videoFavoritesOnly ? 1 : 0) + (videoSortBy !== 'created_at' || videoSortOrder !== 'desc' ? 1 : 0)}</b>}
                </button>
              </div>
              <div className="active-filter-bar" aria-label="已应用筛选">
                <span>已筛选</span>
                {!search && videoPlatforms.length === 0 && videoFileFormats.length === 0 && (page === 'audio' || !videoResolution) && videoFileStatus === 'all' && !videoFavoritesOnly && <span className="active-filter-empty">全部内容</span>}
                {videoPlatforms.map((platform) => <button type="button" key={`platform-${platform}`} onClick={() => toggleVideoPlatform(platform)}><small>平台</small>{platform}<Icon name="close" size={13} /></button>)}
                {videoFileFormats.map((format) => <button type="button" key={`format-${format}`} onClick={() => toggleVideoFileFormat(format)}><small>格式</small>{format.toUpperCase()}<Icon name="close" size={13} /></button>)}
                {page === 'videos' && videoResolution && <button type="button" onClick={() => { setVideoResolution(''); setVideoPage(1) }}><small>清晰度</small>{videoResolution}<Icon name="close" size={13} /></button>}
                {videoFileStatus !== 'all' && <button type="button" onClick={() => { setVideoFileStatus('all'); setVideoPage(1) }}><small>文件状态</small>{videoFileStatus === 'missing' ? '文件不存在' : '文件可用'}<Icon name="close" size={13} /></button>}
                {videoFavoritesOnly && <button type="button" onClick={() => { setVideoFavoritesOnly(false); setVideoPage(1) }}><small>收藏</small>只看收藏<Icon name="close" size={13} /></button>}
                {(search || videoPlatforms.length > 0 || videoFileFormats.length > 0 || (page === 'videos' && videoResolution) || videoFileStatus !== 'all' || videoFavoritesOnly || videoSortBy !== 'created_at' || videoSortOrder !== 'desc') && <button type="button" className="clear-video-filters" onClick={clearVideoFilters}>全部重置</button>}
              </div>
              {videoBatchNotice && <p className="video-batch-notice" role="status">{videoBatchNotice}</p>}
              {selectedVisibleVideoIds.length > 0 && <div className="video-selection-actions" role="status">
                <span>已选 {selectedVisibleVideoIds.length} 项</span>
                <div>
                  <button type="button" onClick={() => setVideoBatchAction('favorite')}><Icon name="star" size={14} />收藏</button>
                  <button type="button" onClick={() => setVideoBatchAction('unfavorite')}>取消收藏</button>
                  <button type="button" className="is-danger" onClick={() => setVideoBatchAction('remove')}><Icon name="trash" size={14} />移除记录</button>
                </div>
              </div>}
              {libraryItems.length === 0 ? (
                <div className="empty-state empty-state-compact">
                  <span><Icon name="library" size={25} /></span>
                  <h2>{search || videoPlatforms.length || videoFileFormats.length || (page === 'videos' && videoResolution) || videoFileStatus !== 'all' || videoFavoritesOnly ? '没有符合筛选条件的内容' : `${page === 'audio' ? '音频' : '视频'}管理还是空的`}</h2>
                  <p>{search || videoPlatforms.length || videoFileFormats.length || (page === 'videos' && videoResolution) || videoFileStatus !== 'all' || videoFavoritesOnly ? '调整搜索词或筛选条件后再试。' : `新建下载，或添加已有的本地${page === 'audio' ? '音频' : '视频'}文件。`}</p>
                  {(search || videoPlatforms.length || videoFileFormats.length || (page === 'videos' && videoResolution) || videoFileStatus !== 'all' || videoFavoritesOnly) && <button className="text-button" onClick={clearVideoFilters}>清除筛选</button>}
                </div>
              ) : (<>
                <div className="table-wrap">
                  <table className="data-table video-table master-video-table">
                    <colgroup><col /><col /><col /><col /><col /><col /></colgroup>
                    <thead><tr><th className="video-select-column"><input type="checkbox" aria-label={`全选本页${page === 'audio' ? '音频' : '视频'}`} checked={allVisibleVideosSelected} disabled={visibleVideoIds.length === 0} onChange={(event) => setSelectedVideoIds(event.target.checked ? visibleVideoIds : [])} /></th><th>内容</th><th>{page === 'audio' ? '格式' : '类型'}</th><th>时长</th><th>{page === 'audio' ? '码率与大小' : '清晰度与大小'}</th><th className="video-row-action-heading">操作</th></tr></thead>
                    <tbody>{libraryItems.map((item) => (
                      <tr key={libraryItemKey(item)} className={[selectedLibraryKey === libraryItemKey(item) ? 'is-selected' : '', item.kind === 'video' && selectedVideoIds.includes(item.id) ? 'is-batch-selected' : ''].filter(Boolean).join(' ')} tabIndex={0} aria-label={`查看${libraryItemTypeLabel(item)}详情：${item.title || '未命名内容'}`} onClick={() => selectLibraryItem(item)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectLibraryItem(item) } }}>
                        <td className="video-select-cell"><input type="checkbox" aria-label={item.kind === 'video' ? `选择${page === 'audio' ? '音频' : '视频'}：${item.title || `未命名${page === 'audio' ? '音频' : '视频'}`}` : '合集暂不支持批量操作'} disabled={item.kind !== 'video'} checked={item.kind === 'video' && selectedVideoIds.includes(item.id)} onClick={(event) => event.stopPropagation()} onChange={(event) => { event.stopPropagation(); if (item.kind !== 'video') return; setSelectedVideoIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id)) }} /></td>
                        <td className="title-cell">
                          <div className="title-cell-content">
                            <div className={item.kind === 'playlist' ? 'mini-cover is-playlist' : 'mini-cover'}>
                              {item.thumbnail ? <img src={item.thumbnail} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /> : <img src={videoPlaceholder} alt="" />}
                              {item.kind === 'playlist' && <span aria-hidden="true">{item.completed_count}</span>}
                            </div>
                            <div>
                              <b title={item.title || '未命名内容'}>{item.title || '未命名内容'}</b>
                              <small>{item.kind === 'playlist' ? `${libraryPlatformLabel(item)} · ${item.uploader || '未知作者'} · 共 ${item.total_count} 个${item.favorite_count ? ` · ${item.favorite_count} 个收藏` : ''}` : item.file_exists ? `${libraryPlatformLabel(item)}${page === 'videos' ? ` · ${watchStatusLabel(item)}` : ''}` : '本地文件不存在'}</small>
                            </div>
                          </div>
                        </td>
                        <td className="library-type-cell">{page === 'audio' && item.kind === 'video' ? videoFormat(item) : <span className={`library-type is-icon-only is-${libraryItemMediaType(item)}`} role="img" aria-label={libraryItemTypeLabel(item)} title={libraryItemTypeLabel(item)}><Icon name={libraryItemTypeIcon(item)} size={18} /></span>}</td>
                        <td>{durationLabel(item.duration)}</td>
                        <td className="video-quality-cell"><b>{libraryResolutionLabel(item)}</b><small>{fileSizeLabel(item.file_size)}</small></td>
                        <td className="video-row-action-cell">
                          <div className="video-row-actions">
                            {item.kind === 'video' ? <>
                              <button type="button" className="play-row-button" aria-label={`播放：${item.title || '未命名视频'}`} disabled={!item.file_exists} onClick={(event) => { event.stopPropagation(); openPlayer(item) }} onKeyDown={(event) => event.stopPropagation()}><Icon name="play" size={26} /></button>
                              <button type="button" className={item.favorite ? 'favorite-row-button is-active' : 'favorite-row-button'} aria-label={item.favorite ? `取消收藏：${item.title || '未命名视频'}` : `收藏：${item.title || '未命名视频'}`} aria-pressed={Boolean(item.favorite)} onClick={(event) => { event.stopPropagation(); void toggleVideoFavorite(item) }} onKeyDown={(event) => event.stopPropagation()}><Icon name="star" size={18} /></button>
                            </> : <>
                              <button type="button" className="play-row-button" aria-label={`播放合集：${item.title || '未命名合集'}`} disabled={item.completed_count === 0} onClick={(event) => { event.stopPropagation(); void playPlaylist(item) }} onKeyDown={(event) => event.stopPropagation()}><Icon name="play" size={26} /></button>
                              <button type="button" className={item.favorite ? 'favorite-row-button is-active' : 'favorite-row-button'} aria-label={item.favorite ? `取消收藏合集：${item.title || '未命名合集'}` : `收藏合集：${item.title || '未命名合集'}`} aria-pressed={Boolean(item.favorite)} onClick={(event) => { event.stopPropagation(); void togglePlaylistFavorite(item) }} onKeyDown={(event) => event.stopPropagation()}><Icon name="star" size={18} /></button>
                            </>}
                          </div>
                        </td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <nav className="library-pagination" aria-label={`${page === 'audio' ? '音频' : '视频'}管理分页`}>
                  <span>共 {libraryTotal} 项{videoTotalPages > 1 ? ` · 第 ${videoPage} / ${videoTotalPages} 页` : ''}</span>
                  {videoTotalPages > 1 && <div>
                    <button type="button" disabled={videoPage <= 1} onClick={() => setVideoPage((current) => Math.max(1, current - 1))}>上一页</button>
                    <button type="button" disabled={videoTotalPages === 0 || videoPage >= videoTotalPages} onClick={() => setVideoPage((current) => current + 1)}>下一页</button>
                  </div>}
                </nav>
              </>)}
            </div>

            {selectedLibraryItem && (
              <aside className="video-detail-panel" aria-label={selectedVideoId ? `${page === 'audio' ? '音频' : '视频'}详情` : '合集详情'}>
                {selectedVideoId ? <>
                  {selectedPlaylist && <button className="collection-back-button" type="button" onClick={() => { setSelectedVideoId(null); setSelectedVideo(null); setVideoActionNotice(null) }}>← 返回合集</button>}
                  <header className="detail-header detail-identity">
                    <div className="detail-cover"><img src={selectedVideo?.thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></div>
                    <div><h2 title={selectedVideo?.title || undefined}>{selectedVideo?.title || `正在读取${page === 'audio' ? '音频' : '视频'}…`}</h2>{selectedVideo && <p className="detail-subtitle">{platformLabel(selectedVideo)} · {durationLabel(selectedVideo.duration)} · {libraryResolutionLabel({ kind: 'video', ...selectedVideo })}</p>}</div>
                    <div className="video-detail-header-actions" ref={videoDetailMenuRootRef}>
                      <button
                        ref={videoDetailMenuButtonRef}
                        className="video-detail-more icon-button"
                        type="button"
                        aria-label={`更多${page === 'audio' ? '音频' : '视频'}操作`}
                        aria-haspopup="menu"
                        aria-expanded={videoDetailMenuOpen}
                        aria-controls={videoDetailMenuOpen ? videoDetailMenuId : undefined}
                        disabled={!selectedVideo}
                        onClick={() => setVideoDetailMenuOpen((current) => !current)}
                      >
                        <Icon name="more" size={19} />
                      </button>
                      {videoDetailMenuOpen && selectedVideo && <div className="video-detail-menu" id={videoDetailMenuId} role="menu" aria-label={`${page === 'audio' ? '音频' : '视频'}操作`} onKeyDown={handleVideoDetailMenuKeyDown}>
                        {page === 'videos' && <button type="button" role="menuitem" onClick={() => { closeVideoDetailMenu(true); void toggleVideoWatched(selectedVideo) }}>{selectedVideo.watched ? '标记为未看完' : '标记为已看完'}</button>}
                        <button type="button" role="menuitem" onClick={() => prepareMetadataEdit(selectedVideo)}>编辑信息</button>
                        <button type="button" role="menuitem" onClick={() => prepareVideoRename(selectedVideo)}>重命名</button>
                        <button type="button" role="menuitem" disabled={!selectedVideo.file_path} onClick={() => { closeVideoDetailMenu(true); void copyVideoFilePath(selectedVideo) }}>复制文件路径</button>
                        <button type="button" role="menuitem" disabled={!selectedVideo.file_exists} onClick={() => { closeVideoDetailMenu(true); void revealVideo(selectedVideo) }}>打开文件位置</button>
                        {page === 'videos' && <button type="button" role="menuitem" disabled={!selectedVideo.file_exists} onClick={() => { closeVideoDetailMenu(true); void openExternalPlayer(selectedVideo, 'system') }}>用系统播放器打开</button>}
                        {page === 'videos' && <button type="button" role="menuitem" disabled={!selectedVideo.file_exists} onClick={() => { closeVideoDetailMenu(true); void openExternalPlayer(selectedVideo, 'iina') }}>用 IINA 打开</button>}
                        {page === 'videos' && selectedVideo.file_origin === 'downloaded' && <button type="button" role="menuitem" disabled={!selectedVideo.file_exists} onClick={() => prepareVideoUpgrade(selectedVideo)}>检查更高画质</button>}
                        <button type="button" role="menuitem" className="is-danger" onClick={() => { closeVideoDetailMenu(false); prepareVideoDelete(selectedVideo) }}><Icon name="trash" size={15} />删除{page === 'audio' ? '音频' : '视频'}</button>
                      </div>}
                      <button ref={videoDetailCloseRef} className="video-detail-close icon-button" type="button" onClick={closeLibraryDetail} aria-label={`关闭${page === 'audio' ? '音频' : '视频'}详情`}><Icon name="close" size={18} /></button>
                    </div>
                  </header>
                  {selectedVideo && <>
                    {!selectedVideo.file_exists && <div className="missing-file-notice" role="status"><span>本地文件不存在。文件可能已被外部移动或删除。</span><button type="button" className="cancel-button" onClick={() => void selectAndRelinkVideo(selectedVideo)} disabled={relinkingVideoId === selectedVideo.id}>{relinkingVideoId === selectedVideo.id ? '正在重新定位…' : '重新定位文件'}</button></div>}
                    {selectedVideo.upgrade_from_id && <div className="missing-file-notice upgrade-finalize-notice" role="status"><span>更高画质版本已下载完成。确认新文件可用后，再决定是否保留旧版。</span><div><button type="button" className="cancel-button" disabled={upgradeWorking} onClick={() => void finalizeVideoUpgrade(false)}>保留两份</button><button type="button" className="delete-button" disabled={upgradeWorking} onClick={() => void finalizeVideoUpgrade(true)}>{upgradeWorking ? '正在处理…' : '用新版替换旧版'}</button></div></div>}
                    {videoActionNotice && <p className="notice" role="status">{videoActionNotice}</p>}
                    <section className="detail-section">
                      <div className="detail-section-heading"><h3>媒体信息</h3><span>{dateLabel(selectedVideo.created_at)} 保存</span></div>
                      <dl className="video-detail-grid">
                        <div><dt>平台</dt><dd>{platformLabel(selectedVideo)}</dd></div>
                        <div><dt>作者</dt><dd>{selectedVideo.uploader || '—'}</dd></div>
                        <div><dt>文件格式</dt><dd>{videoFormat(selectedVideo)}</dd></div>
                        <div><dt>{libraryItemMediaType({ kind: 'video', ...selectedVideo }) === 'audio' ? '音频码率' : '清晰度'}</dt><dd>{libraryResolutionLabel({ kind: 'video', ...selectedVideo })}</dd></div>
                        <div><dt>文件大小</dt><dd>{fileSizeLabel(selectedVideo.file_size)}</dd></div>
                        <div><dt>发布日期</dt><dd>{dateLabel(selectedVideo.upload_date)}</dd></div>
                        {page === 'videos' && <div><dt>视频分段</dt><dd>{selectedVideo.metadata?.chapters?.length ? `${selectedVideo.metadata.chapters.length} 段` : '无分段信息'}</dd></div>}
                        {page === 'videos' && <div><dt>观看状态</dt><dd>{watchStatusLabel(selectedVideo)}</dd></div>}
                        {page === 'videos' && <div><dt>最近观看</dt><dd>{selectedVideo.last_watched_at ? dateLabel(selectedVideo.last_watched_at) : '—'}</dd></div>}
                      </dl>
                    </section>
                    <section className="detail-section detail-source-section">
                      <div className="detail-section-heading"><h3>来源与文件</h3></div>
                      <div className="detail-link"><span>内容来源</span>{selectedVideo.file_origin === 'local' ? <code>本地源文件</code> : <a href={selectedVideo.source_url} target="_blank" rel="noreferrer">打开原页面 ↗</a>}</div>
                      {selectedVideo.file_path && <div className="detail-path"><span>本地文件</span><code title={selectedVideo.file_path}>{selectedVideo.file_path}</code></div>}
                    </section>
                  </>}
                </> : selectedPlaylist && <>
                  <header className="detail-header detail-identity">
                    <div className="detail-cover"><img src={selectedPlaylist.thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></div>
                    <div><p className="eyebrow">当前合集</p><h2 title={selectedPlaylist.title}>{selectedPlaylist.title}</h2><p className="detail-subtitle">{libraryPlatformLabel(selectedPlaylist)} · 共 {selectedPlaylist.total_count} 个</p></div>
                    <button ref={videoDetailCloseRef} className="video-detail-close icon-button" type="button" onClick={closeLibraryDetail} aria-label="关闭合集详情"><Icon name="close" size={18} /></button>
                  </header>
                  <section className="detail-section">
                    <div className="detail-section-heading"><h3>合集信息</h3><span>{dateLabel(selectedPlaylist.created_at)} 保存</span></div>
                    <dl className="video-detail-grid">
                      <div><dt>平台</dt><dd>{libraryPlatformLabel(selectedPlaylist)}</dd></div>
                      <div><dt>作者</dt><dd>{selectedPlaylist.uploader || '—'}</dd></div>
                      <div><dt>已下载</dt><dd>{selectedPlaylist.completed_count} / {selectedPlaylist.total_count}</dd></div>
                      <div><dt>原合集数量</dt><dd>{selectedPlaylist.source_total_count || '—'}</dd></div>
                      <div><dt>总时长</dt><dd>{durationLabel(selectedPlaylist.duration)}</dd></div>
                      <div><dt>总大小</dt><dd>{fileSizeLabel(selectedPlaylist.file_size)}</dd></div>
                    </dl>
                  </section>
                  {selectedPlaylist.description && <section className="detail-section collection-description"><div className="detail-section-heading"><h3>合集说明</h3></div><p>{selectedPlaylist.description}</p></section>}
                  <section className="detail-section collection-video-section">
                    <div className="detail-section-heading"><h3>合集视频</h3><span>{collectionVideos.length} 个可播放</span></div>
                    {collectionVideos.length > 0 ? <ol>{collectionVideos.map((video, index) => <li key={video.id}><button className="collection-video-link" type="button" onClick={() => openCollectionVideoDetail(video)}><span>{video.playlist_index || index + 1}</span><span><b>{video.title || `第 ${index + 1} 个视频`}</b><small>{durationLabel(video.duration)} · {video.resolution || '未知清晰度'}</small></span></button><button className="collection-video-play" type="button" onClick={() => openPlayer(video)} disabled={!video.file_exists} aria-label={`播放${video.title || `第 ${index + 1} 个视频`}`}><Icon name="play" size={13} />播放</button></li>)}</ol> : <p className="muted">正在读取已完成的视频…</p>}
                  </section>
                  <section className="detail-section detail-source-section">
                    <div className="detail-section-heading"><h3>来源</h3></div>
                    <div className="detail-link"><span>原始链接</span><a href={selectedPlaylist.source_url} target="_blank" rel="noreferrer">打开原合集 ↗</a></div>
                    <div className="detail-link"><span>平台合集 ID</span><code>{selectedPlaylist.external_id || '—'}</code></div>
                    <button className="cancel-button follow-collection-button" type="button" onClick={() => openSourceFollows(selectedPlaylist.source_url)}>关注此合集的更新</button>
                  </section>
                </>}
              </aside>
            )}
          </section>
        ) : (
          <section className="settings-workspace">
            {settingsDraft ? (
              <div className="settings-tabs">
                <div className="settings-tab-list" role="tablist" aria-label="下载配置类别">
                  {([['general', '下载设置'], ['yt-dlp', 'yt-dlp'], ['ffmpeg', 'FFmpeg'], ['aria2', 'aria2'], ['qbittorrent', 'qBittorrent'], ['backup', '数据备份'], ['maintenance', '清理维护']] as Array<[SettingsTab, string]>).map(([tab, label]) => <button key={tab} type="button" role="tab" aria-selected={settingsTab === tab} className={settingsTab === tab ? 'is-active' : ''} onClick={() => { setSettingsTab(tab); setSettingsNotice(null); if (tab === 'maintenance') void refreshMaintenanceData() }}>{label}</button>)}
                </div>

                {settingsTab === 'general' && <section className="general-settings" role="tabpanel">
                  <header><h2>下载与保存</h2><p>设置默认保存位置和随视频保留的文件。</p></header>
                  <div className="friendly-form">
                    <label><span><b>默认下载位置</b><small>下载完成后的文件保存目录。</small></span><input value={settingsDraft.download_dir} onChange={(event) => setSettingsDraft({ ...settingsDraft, download_dir: event.target.value })} placeholder="/Users/你的用户名/Movies" /></label>
                    <label><span><b>文件存放目录格式</b><small>新任务按此格式创建子目录。支持 platform、uploader、title、year、month、day 变量；留空则全部保存在默认位置。</small></span><input value={settingsDraft.directory_pattern} onChange={(event) => setSettingsDraft({ ...settingsDraft, directory_pattern: event.target.value })} placeholder={'{platform}/{year}-{month}/{title}'} /></label>
                    <div className="friendly-control-row"><span><b>同时下载任务数</b><small>高优先级任务会先进入这些并发位置。</small></span><SettingSelect ariaLabel="同时下载任务数" value={settingsDraft.max_concurrent_downloads} options={[{ value: '1', label: '1 个' }, { value: '2', label: '2 个' }, { value: '3', label: '3 个' }, { value: '5', label: '5 个' }, { value: '8', label: '8 个' }, { value: '10', label: '10 个' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, max_concurrent_downloads: Number(value) })} /></div>
                    <label><span><b>单任务下载限速（KB/s）</b><small>对三个下载引擎生效；填写 0 表示不限速。</small></span><input type="number" min="0" max="1000000" value={settingsDraft.download_rate_limit_kbps} onChange={(event) => setSettingsDraft({ ...settingsDraft, download_rate_limit_kbps: Math.max(0, Number(event.target.value) || 0) })} /></label>
                    <label><span><b>完成后保留空间（MB）</b><small>创建任务和真正开始前都会检查；预计文件大小之外还需保留这部分空间。</small></span><input type="number" min="0" max="1000000" value={settingsDraft.minimum_free_space_mb} onChange={(event) => setSettingsDraft({ ...settingsDraft, minimum_free_space_mb: Math.max(0, Number(event.target.value) || 0) })} /></label>
                    <label><span><b>媒体目录</b><small>每行填写一个绝对路径。点击扫描后递归添加其中尚未入库的视频和音频。</small></span><textarea value={settingsDraft.library_dirs.join('\n')} onChange={(event) => setSettingsDraft({ ...settingsDraft, library_dirs: event.target.value.split('\n') })} placeholder={'/Users/你的用户名/Movies\n/Volumes/Video'} rows={3} /></label>
                    <label className="friendly-switch"><span><b>启动时增量扫描媒体目录</b><small>在后台只添加尚未入库的媒体，不复制源文件；目录不可访问时只记录失败。</small></span><input type="checkbox" checked={settingsDraft.scan_library_on_startup} onChange={(event) => setSettingsDraft({ ...settingsDraft, scan_library_on_startup: event.target.checked })} /></label>
                    <label className="friendly-switch"><span><b>保存封面</b><small>同时保存视频封面图片。</small></span><input type="checkbox" checked={settingsDraft.write_thumbnail} onChange={(event) => setSettingsDraft({ ...settingsDraft, write_thumbnail: event.target.checked })} /></label>
                    <label className="friendly-switch"><span><b>保存媒体信息</b><small>同时保存媒体信息 JSON 文件。</small></span><input type="checkbox" checked={settingsDraft.write_info_json} onChange={(event) => setSettingsDraft({ ...settingsDraft, write_info_json: event.target.checked })} /></label>
                    <label className="friendly-switch"><span><b>macOS 系统通知</b><small>下载完成或失败时发送本机通知；关闭应用网页后仍可通知。</small></span><input type="checkbox" checked={settingsDraft.system_notifications} onChange={(event) => setSettingsDraft({ ...settingsDraft, system_notifications: event.target.checked })} /></label>
                  </div>
                  {libraryScanReport && (libraryScanReport.missing > 0 || libraryScanReport.possible_moves.length > 0 || libraryScanReport.failed.length > 0) && <div className="cleanup-report library-scan-report" role="status">
                    {libraryScanReport.missing_items.length > 0 && <section><b>文件不存在 · {libraryScanReport.missing_items.length}</b><ul>{libraryScanReport.missing_items.map((item) => <li key={item.id}>{item.title || '未命名媒体'}<small>{item.old_path}</small></li>)}</ul></section>}
                    {libraryScanReport.possible_moves.length > 0 && <section><b>疑似移动 · {libraryScanReport.possible_moves.length}</b><ul>{libraryScanReport.possible_moves.map((item) => <li key={item.id}>{item.title || '未命名媒体'}<small>{item.old_path} → {item.candidate_path}</small></li>)}</ul></section>}
                    {libraryScanReport.failed.length > 0 && <section><b>扫描失败 · {libraryScanReport.failed.length}</b><ul>{libraryScanReport.failed.map((item) => <li key={item.path}>{item.path}<small>{item.error}</small></li>)}</ul></section>}
                  </div>}
                  <footer className="tab-save-footer">{settingsNotice ? <p className="settings-notice" role="status">{settingsNotice}</p> : <span>{settingsDraft.scan_library_on_startup ? '下次启动会在后台增量扫描媒体目录。' : '媒体目录只会在手动扫描时读取。'}</span>}<div className="settings-footer-actions"><button className="cancel-button" type="button" onClick={() => void scanLibrary()} disabled={libraryScanWorking || savingSettings !== null || !settingsDraft.download_dir.trim()}>{libraryScanWorking ? '正在扫描…' : '保存并扫描媒体目录'}</button><button className="primary-button" type="button" onClick={() => saveSettings('general')} disabled={libraryScanWorking || savingSettings !== null || !settingsDraft.download_dir.trim()}>{savingSettings === 'general' ? '正在保存…' : '保存通用设置'}</button></div></footer>
                </section>}


                {settingsTab === 'yt-dlp' && <section className="general-settings engine-simple-settings" role="tabpanel">
                  <header><p className="eyebrow">下载引擎</p><h2>yt-dlp 下载设置</h2><p>仅保留当前网页视频下载会用到的选项。</p></header>
                  <div className="friendly-form">
                    <div className="settings-version-check"><span><b>版本更新</b><small>{ytDlpUpdate ? `当前 ${ytDlpUpdate.current_version} · 最新 ${ytDlpUpdate.latest_version}` : `当前 ${health?.engine_version || '未知版本'}，仅检查更新，不会自动安装。`}</small>{ytDlpUpdate && <em className={ytDlpUpdate.update_available ? 'has-update' : ''}>{ytDlpUpdate.update_available ? '发现新版本' : '已经是最新版本'}</em>}</span><button className="cancel-button" type="button" onClick={() => void checkYtDlpUpdate()} disabled={ytDlpUpdateWorking}>{ytDlpUpdateWorking ? '正在检查…' : '检查更新'}</button></div>
                    <div className="friendly-control-row"><span><b>同时下载分片</b><small>多个小片段可同时下载；网络不稳定时保持“自动”。</small></span><SettingSelect ariaLabel="同时下载分片" value={settingsDraft.yt_dlp_simple.concurrent_fragments ?? ''} options={[{ value: '', label: '自动' }, { value: '2', label: '2 个' }, { value: '4', label: '4 个' }, { value: '8', label: '8 个' }, { value: '16', label: '16 个' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, concurrent_fragments: value ? Number(value) : null } })} /></div>
                    <div className="friendly-control-row"><span><b>失败后重试</b><small>网络中断或临时错误时自动重试下载。</small></span><SettingSelect ariaLabel="失败后重试" value={settingsDraft.yt_dlp_simple.retries ?? ''} options={[{ value: '', label: '使用默认值' }, { value: '0', label: '不重试' }, { value: '3', label: '重试 3 次' }, { value: '10', label: '重试 10 次' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, retries: value ? Number(value) : null } })} /></div>
                    <label className="friendly-switch"><span><b>保存字幕</b><small>下载视频提供的人工字幕文件。</small></span><input type="checkbox" checked={settingsDraft.yt_dlp_simple.write_subs} onChange={(event) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, write_subs: event.target.checked } })} /></label>
                    <label className="friendly-switch"><span><b>保存自动字幕</b><small>没有人工字幕时，可同时保存平台生成的自动字幕。</small></span><input type="checkbox" checked={settingsDraft.yt_dlp_simple.write_auto_subs} onChange={(event) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, write_auto_subs: event.target.checked } })} /></label>
                    <label><span><b>字幕语言</b><small>例如“zh.*,en”表示中文和英文。</small></span><input value={settingsDraft.yt_dlp_simple.sub_langs} onChange={(event) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, sub_langs: event.target.value } })} placeholder="zh.*,en" /></label>
                    <label className="friendly-switch"><span><b>只保存音频</b><small>下载后提取音频，不保留视频文件。</small></span><input type="checkbox" checked={settingsDraft.yt_dlp_simple.extract_audio} onChange={(event) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, extract_audio: event.target.checked } })} /></label>
                    <div className="friendly-control-row"><span><b>音频格式</b><small>仅在“只保存音频”开启时使用。</small></span><SettingSelect ariaLabel="音频格式" value={settingsDraft.yt_dlp_simple.audio_format || ''} disabled={!settingsDraft.yt_dlp_simple.extract_audio} options={[{ value: '', label: '保持原格式' }, { value: 'mp3', label: 'MP3' }, { value: 'm4a', label: 'M4A' }, { value: 'opus', label: 'Opus' }, { value: 'wav', label: 'WAV' }, { value: 'flac', label: 'FLAC' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, yt_dlp_simple: { ...settingsDraft.yt_dlp_simple, audio_format: value || null } })} /></div>
                    <div className="friendly-control-row"><span><b>使用浏览器登录状态</b><small>下载需要登录的内容时读取本机浏览器 cookies。</small></span><SettingSelect ariaLabel="使用浏览器登录状态" value={configOptionValue(settingsDraft.yt_dlp_config, '--cookies-from-browser')} options={[{ value: '', label: '不使用' }, { value: 'chrome', label: 'Chrome' }, { value: 'firefox', label: 'Firefox' }, { value: 'safari', label: 'Safari' }, { value: 'edge', label: 'Edge' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, yt_dlp_config: updateConfigOption(settingsDraft.yt_dlp_config, '--cookies-from-browser', value) })} /></div>
                    <label><span><b>代理地址</b><small>仅在需要通过本机代理访问下载地址时填写。</small></span><input value={configOptionValue(settingsDraft.yt_dlp_config, '--proxy')} onChange={(event) => setSettingsDraft({ ...settingsDraft, yt_dlp_config: updateConfigOption(settingsDraft.yt_dlp_config, '--proxy', event.target.value.trim()) })} placeholder="http://127.0.0.1:7890" /></label>
                  </div>
                  <footer className="tab-save-footer">{settingsNotice ? <p className="settings-notice" role="status">{settingsNotice}</p> : <span>仅保存 yt-dlp 下载设置。</span>}<button className="primary-button" type="button" onClick={() => saveSettings('yt-dlp')} disabled={savingSettings !== null}>{savingSettings === 'yt-dlp' ? '正在保存…' : '保存 yt-dlp 设置'}</button></footer>
                </section>}

                {settingsTab === 'ffmpeg' && <section className="general-settings engine-simple-settings" role="tabpanel">
                  <header><p className="eyebrow">处理引擎</p><h2>FFmpeg 处理设置</h2><p>仅在 yt-dlp 合并或转码媒体时生效。</p></header>
                  <div className="friendly-form">
                    <div className="friendly-control-row"><span><b>网页播放优化</b><small>优化 MP4 文件在网页中的起播速度。</small></span><SettingSelect ariaLabel="网页播放优化" value={configOptionValue(settingsDraft.ffmpeg_config, '-movflags')} options={[{ value: '', label: '自动' }, { value: '+faststart', label: '开启' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-movflags', value) })} /></div>
                    <div className="friendly-control-row"><span><b>视频编码</b><small>需要兼容更多播放器时可转换为 H.264。</small></span><SettingSelect ariaLabel="视频编码" value={configOptionValue(settingsDraft.ffmpeg_config, '-c:v')} options={[{ value: '', label: '保持原编码' }, { value: 'libx264', label: 'H.264' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-c:v', value) })} /></div>
                    <div className="friendly-control-row"><span><b>视频质量</b><small>仅在视频转换为 H.264 时使用。</small></span><SettingSelect ariaLabel="视频质量" value={configOptionValue(settingsDraft.ffmpeg_config, '-crf')} options={[{ value: '', label: '自动' }, { value: '18', label: '高画质' }, { value: '23', label: '均衡' }, { value: '28', label: '较小文件' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-crf', value) })} /></div>
                    <div className="friendly-control-row"><span><b>编码速度</b><small>速度越慢，通常压缩率越高。</small></span><SettingSelect ariaLabel="编码速度" value={configOptionValue(settingsDraft.ffmpeg_config, '-preset')} options={[{ value: '', label: '自动' }, { value: 'fast', label: '较快' }, { value: 'medium', label: '均衡' }, { value: 'slow', label: '较慢' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-preset', value) })} /></div>
                    <div className="friendly-control-row"><span><b>音频编码</b><small>需要兼容更多播放器时可转换为 AAC。</small></span><SettingSelect ariaLabel="音频编码" value={configOptionValue(settingsDraft.ffmpeg_config, '-c:a')} options={[{ value: '', label: '保持原编码' }, { value: 'aac', label: 'AAC' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-c:a', value) })} /></div>
                    <div className="friendly-control-row"><span><b>音频比特率</b><small>仅在音频转换为 AAC 时使用。</small></span><SettingSelect ariaLabel="音频比特率" value={configOptionValue(settingsDraft.ffmpeg_config, '-b:a')} options={[{ value: '', label: '自动' }, { value: '128k', label: '128 kbps' }, { value: '192k', label: '192 kbps' }, { value: '256k', label: '256 kbps' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-b:a', value) })} /></div>
                    <div className="friendly-control-row"><span><b>音频采样率</b><small>没有兼容问题时保持自动。</small></span><SettingSelect ariaLabel="音频采样率" value={configOptionValue(settingsDraft.ffmpeg_config, '-ar')} options={[{ value: '', label: '自动' }, { value: '44100', label: '44.1 kHz' }, { value: '48000', label: '48 kHz' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, ffmpeg_config: updateConfigOption(settingsDraft.ffmpeg_config, '-ar', value) })} /></div>
                  </div>
                  <footer className="tab-save-footer">{settingsNotice ? <p className="settings-notice" role="status">{settingsNotice}</p> : <span>仅保存 FFmpeg 处理设置。</span>}<button className="primary-button" type="button" onClick={() => saveSettings('ffmpeg')} disabled={savingSettings !== null}>{savingSettings === 'ffmpeg' ? '正在保存…' : '保存 FFmpeg 设置'}</button></footer>
                </section>}

                {settingsTab === 'aria2' && <section className="general-settings aria2-settings" role="tabpanel">
                  <header><p className="eyebrow">下载引擎</p><h2>aria2 下载设置</h2><p>仅影响直链、迅雷链接和 BT 下载。</p></header>
                  <div className="friendly-form">
                    <div className="friendly-control-row"><span><b>直链分片数</b><small>同一文件使用的并行连接数；网络不稳定时可降低。</small></span><SettingSelect ariaLabel="直链分片数" value={settingsDraft.aria2.split} options={[{ value: '1', label: '1 个' }, { value: '2', label: '2 个' }, { value: '5', label: '5 个' }, { value: '8', label: '8 个' }, { value: '16', label: '16 个' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, aria2: { ...settingsDraft.aria2, split: Number(value) } })} /></div>
                    <div className="friendly-control-row"><span><b>失败重试次数</b><small>直链下载遇到临时网络错误时的重试次数。</small></span><SettingSelect ariaLabel="aria2 失败重试次数" value={settingsDraft.aria2.max_tries} options={[{ value: '1', label: '1 次' }, { value: '3', label: '3 次' }, { value: '5', label: '5 次' }, { value: '10', label: '10 次' }, { value: '20', label: '20 次' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, aria2: { ...settingsDraft.aria2, max_tries: Number(value) } })} /></div>
                    <div className="friendly-control-row"><span><b>重试间隔</b><small>每次重试前等待的时间。</small></span><SettingSelect ariaLabel="重试间隔" value={settingsDraft.aria2.retry_wait} options={[{ value: '0', label: '立即重试' }, { value: '2', label: '2 秒' }, { value: '5', label: '5 秒' }, { value: '10', label: '10 秒' }, { value: '30', label: '30 秒' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, aria2: { ...settingsDraft.aria2, retry_wait: Number(value) } })} /></div>
                    <div className="friendly-control-row"><span><b>BT 无数据超时</b><small>在这段时间内没有获得任何数据时停止任务并提示重试。</small></span><SettingSelect ariaLabel="BT 无数据超时" value={settingsDraft.aria2.bt_stall_timeout} options={[{ value: '30', label: '30 秒' }, { value: '60', label: '60 秒' }, { value: '90', label: '90 秒' }, { value: '180', label: '3 分钟' }, { value: '300', label: '5 分钟' }, { value: '600', label: '10 分钟' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, aria2: { ...settingsDraft.aria2, bt_stall_timeout: Number(value) } })} /></div>
                  </div>
                  <footer className="tab-save-footer">{settingsNotice ? <p className="settings-notice" role="status">{settingsNotice}</p> : <span>仅保存 aria2 下载设置。</span>}<button className="primary-button" type="button" onClick={() => saveSettings('aria2')} disabled={savingSettings !== null}>{savingSettings === 'aria2' ? '正在保存…' : '保存 aria2 设置'}</button></footer>
                </section>}

                {settingsTab === 'qbittorrent' && <section className="general-settings aria2-settings" role="tabpanel">
                  <header><p className="eyebrow">可选 BT 引擎</p><h2>qBittorrent 连接</h2><p>启用后，磁力链接和种子文件会优先交给 qBittorrent；网页视频和直链仍使用原下载引擎。</p></header>
                  <div className="friendly-form">
                    <label className="friendly-switch"><span><b>启用 qBittorrent</b><small>保存时会立即验证 Web UI 连接和登录信息。</small></span><input type="checkbox" checked={settingsDraft.qbittorrent.enabled} onChange={(event) => setSettingsDraft({ ...settingsDraft, qbittorrent: { ...settingsDraft.qbittorrent, enabled: event.target.checked } })} /></label>
                    <label><span><b>Web UI 地址</b><small>必须是本机或局域网内可访问的完整 HTTP(S) 地址。</small></span><input value={settingsDraft.qbittorrent.base_url} onChange={(event) => setSettingsDraft({ ...settingsDraft, qbittorrent: { ...settingsDraft.qbittorrent, base_url: event.target.value } })} placeholder="http://127.0.0.1:8080" /></label>
                    <label><span><b>用户名</b><small>qBittorrent Web UI 登录用户名。</small></span><input value={settingsDraft.qbittorrent.username} onChange={(event) => setSettingsDraft({ ...settingsDraft, qbittorrent: { ...settingsDraft.qbittorrent, username: event.target.value } })} autoComplete="username" /></label>
                    <label><span><b>密码</b><small>仅保存在本机应用数据库中，不会写入下载日志。</small></span><input type="password" value={settingsDraft.qbittorrent.password} onChange={(event) => setSettingsDraft({ ...settingsDraft, qbittorrent: { ...settingsDraft.qbittorrent, password: event.target.value } })} autoComplete="current-password" /></label>
                    <div className="friendly-control-row"><span><b>BT 无数据超时</b><small>没有取得元数据或下载进度时停止并给出错误。</small></span><SettingSelect ariaLabel="qBittorrent BT 无数据超时" value={settingsDraft.qbittorrent.bt_stall_timeout} options={[{ value: '30', label: '30 秒' }, { value: '60', label: '60 秒' }, { value: '90', label: '90 秒' }, { value: '180', label: '3 分钟' }, { value: '300', label: '5 分钟' }, { value: '600', label: '10 分钟' }]} onChange={(value) => setSettingsDraft({ ...settingsDraft, qbittorrent: { ...settingsDraft.qbittorrent, bt_stall_timeout: Number(value) } })} /></div>
                    <p className="input-help">qBittorrent 进程必须能读写与本应用相同的下载绝对路径；远程主机或容器需把该路径映射为同一路径。</p>
                  </div>
                  <footer className="tab-save-footer">{settingsNotice ? <p className="settings-notice" role="status">{settingsNotice}</p> : <span>启用时会测试连接；关闭时仅保存配置。</span>}<button className="primary-button" type="button" onClick={() => saveSettings('qbittorrent')} disabled={savingSettings !== null}>{savingSettings === 'qbittorrent' ? '正在连接…' : '保存并测试连接'}</button></footer>
                </section>}

                {settingsTab === 'backup' && <section className="general-settings maintenance-settings backup-settings" role="tabpanel">
                  <header><p className="eyebrow">本地数据</p><h2>备份与恢复</h2><p>备份收藏、观看进度、媒体记录、下载任务和所有设置；不会复制或移动视频与音频文件。</p></header>
                  <section className="maintenance-card">
                    <div><h3>导出备份</h3><p>生成带格式版本和应用版本信息的 JSON 文件。配置中可能包含 qBittorrent 密码，请保存在可信位置。</p></div>
                    <div className="maintenance-card-actions"><a className="primary-button" href={`${apiBase}/api/v1/backup/export`} download>下载备份文件</a></div>
                  </section>
                  <section className="maintenance-card backup-restore-card">
                    <div><h3>恢复备份</h3><p>先选择备份文件并预览。恢复使用单一事务，失败时不会覆盖当前数据；进行中的下载会阻止恢复。</p></div>
                    <label className="backup-file-picker"><span>{backupFileName || '尚未选择备份文件'}</span><input type="file" accept="application/json,.json" disabled={backupWorking} onChange={(event) => { const file = event.target.files?.[0]; event.currentTarget.value = ''; if (file) void previewBackupFile(file) }} /><b>{backupWorking ? '正在校验…' : '选择备份文件'}</b></label>
                    {backupPreview && <div className="backup-preview" role="status">
                      <dl><div><dt>媒体与任务</dt><dd>{backupPreview.counts.downloads}</dd></div><div><dt>合集</dt><dd>{backupPreview.counts.playlists}</dd></div><div><dt>关注源</dt><dd>{backupPreview.counts.source_follows}</dd></div><div><dt>处理记录</dt><dd>{backupPreview.counts.media_derivative_jobs}</dd></div><div><dt>下载日志</dt><dd>{backupPreview.counts.download_logs}</dd></div><div><dt>设置项</dt><dd>{backupPreview.counts.app_settings}</dd></div></dl>
                      <p>备份时间：{backupPreview.created_at ? dateLabel(backupPreview.created_at) : '未知'}；引用的媒体文件中有 {backupPreview.missing_media_files} 个当前不存在。备份本身不包含媒体文件。</p>
                      <label className="delete-file-option"><input type="checkbox" checked={backupRestoreConfirmed} onChange={(event) => setBackupRestoreConfirmed(event.target.checked)} /><span><b>我已核对预览，确认用此备份替换当前应用数据</b><small>视频和音频文件不会被删除、复制或移动。</small></span></label>
                    </div>}
                    <div className="maintenance-card-actions"><button className="delete-button" type="button" onClick={() => void restoreBackup()} disabled={backupWorking || !backupPreview || !backupRestoreConfirmed}>{backupWorking ? '正在恢复…' : '确认恢复备份'}</button></div>
                  </section>
                  {backupNotice && <p className="settings-notice maintenance-notice" role="status">{backupNotice}</p>}
                </section>}

                {settingsTab === 'maintenance' && <section className="general-settings maintenance-settings" role="tabpanel">
                  <header><p className="eyebrow">本地维护</p><h2>清理记录、未完成文件与重复媒体</h2><p>先扫描和预览，只处理明确列出的项目；重复媒体按文件内容精确比对，并始终手动选择要移入废纸篓的副本。</p></header>
                  <div className="friendly-form">
                    <label className="friendly-switch"><span><b>启动时自动清理</b><small>按下方保留天数清理旧日志和终止任务记录，不会自动删除未完成文件。</small></span><input type="checkbox" checked={settingsDraft.maintenance.auto_cleanup_enabled} onChange={(event) => setSettingsDraft({ ...settingsDraft, maintenance: { ...settingsDraft.maintenance, auto_cleanup_enabled: event.target.checked } })} /></label>
                    <div className="friendly-control-row"><span><b>保留最近记录</b><small>超过这个天数的终止任务和下载日志进入清理范围。</small></span><SettingSelect ariaLabel="清理保留天数" value={settingsDraft.maintenance.retention_days} options={[{ value: '7', label: '7 天' }, { value: '15', label: '15 天' }, { value: '30', label: '30 天' }, { value: '60', label: '60 天' }, { value: '90', label: '90 天' }, { value: '180', label: '180 天' }, { value: '365', label: '365 天' }]} onChange={(value) => { setSettingsDraft({ ...settingsDraft, maintenance: { ...settingsDraft.maintenance, retention_days: Number(value) } }); setMaintenancePreview(null) }} /></div>
                    <label className="friendly-switch"><span><b>清理旧下载日志</b><small>保留进行中和已暂停任务的日志。</small></span><input type="checkbox" checked={settingsDraft.maintenance.clean_download_logs} onChange={(event) => setSettingsDraft({ ...settingsDraft, maintenance: { ...settingsDraft.maintenance, clean_download_logs: event.target.checked } })} /></label>
                    <label className="friendly-switch"><span><b>清理旧下载任务</b><small>仅隐藏失败、取消和中断的旧任务；不处理已完成媒体和本地文件。</small></span><input type="checkbox" checked={settingsDraft.maintenance.clean_download_tasks} onChange={(event) => setSettingsDraft({ ...settingsDraft, maintenance: { ...settingsDraft.maintenance, clean_download_tasks: event.target.checked } })} /></label>
                  </div>
                  <section className="maintenance-card">
                    <div><h3>历史记录预览</h3><p>{maintenancePreview ? `${settingsDraft.maintenance.retention_days} 天前共有 ${maintenancePreview.log_count} 条可清理日志、${maintenancePreview.task_count} 个可清理任务。` : '点击重新扫描，确认当前可清理数量。'}</p></div>
                    <div className="maintenance-card-actions"><button className="cancel-button" type="button" onClick={() => void refreshMaintenanceData()} disabled={maintenanceWorking}>{maintenanceWorking ? '扫描中…' : '重新扫描'}</button><button className="delete-button" type="button" onClick={() => void runHistoryCleanup()} disabled={maintenanceWorking || !maintenancePreview || (!settingsDraft.maintenance.clean_download_logs && !settingsDraft.maintenance.clean_download_tasks) || ((settingsDraft.maintenance.clean_download_logs ? maintenancePreview.log_count : 0) + (settingsDraft.maintenance.clean_download_tasks ? maintenancePreview.task_count : 0) === 0)}>立即清理历史记录</button></div>
                  </section>
                  <section className="maintenance-card residue-card">
                    <div><h3>未完成文件</h3><p>{incompletePreview ? `发现 ${incompletePreview.total_items} 项，共约 ${incompletePreview.total_bytes === 0 ? '0 B' : fileSizeLabel(incompletePreview.total_bytes)}。勾选后移入 macOS 废纸篓。` : '扫描 .part、.ytdl、.aria2、BT 临时目录和种子缓存。'}</p></div>
                    {incompletePreview && incompletePreview.items.length > 0 && <><label className="maintenance-select-all"><input type="checkbox" checked={selectedResiduePaths.length === incompletePreview.items.length} onChange={(event) => setSelectedResiduePaths(event.target.checked ? incompletePreview.items.map((item) => item.path) : [])} />全选 {incompletePreview.items.length} 项</label><ol className="residue-list">{incompletePreview.items.map((item) => <li key={item.path}><label><input type="checkbox" checked={selectedResiduePaths.includes(item.path)} onChange={(event) => setSelectedResiduePaths((current) => event.target.checked ? [...current, item.path] : current.filter((path) => path !== item.path))} /><span><b>{item.kind} · {item.title}</b><small>{item.path} · {fileSizeLabel(item.size)} · {item.status === 'paused' ? '已暂停，可继续下载' : item.task_deleted ? '任务记录已清理' : statusLabel(item.status)}</small></span></label></li>)}</ol></>}
                    <div className="maintenance-card-actions"><button className="delete-button" type="button" onClick={() => void cleanupSelectedResidues()} disabled={maintenanceWorking || selectedResiduePaths.length === 0}>将已选 {selectedResiduePaths.length} 项移入废纸篓</button></div>
                  </section>
                  <section className="maintenance-card residue-card duplicate-card">
                    <div><h3>重复媒体</h3><p>{duplicatePreview ? `扫描 ${duplicatePreview.scanned_files} 个可用媒体，发现 ${duplicatePreview.total_groups} 组、${duplicatePreview.total_files} 个内容完全相同的文件，最多可释放约 ${duplicatePreview.potential_reclaim_bytes === 0 ? '0 B' : fileSizeLabel(duplicatePreview.potential_reclaim_bytes)}${duplicatePreview.skipped_files ? `；${duplicatePreview.skipped_files} 个文件无法读取` : ''}。` : '仅对大小相同的候选文件计算 SHA-256，不按标题或来源猜测。'}</p></div>
                    {duplicatePreview && duplicatePreview.groups.length > 0 && <div className="duplicate-groups">{duplicatePreview.groups.map((group, groupIndex) => <section key={group.fingerprint} className="duplicate-group">
                      <header><b>重复组 {groupIndex + 1}</b><span>{group.items.length} 个文件 · 每个 {fileSizeLabel(group.size)} · 可释放 {fileSizeLabel(group.reclaimable_bytes)}</span></header>
                      <ol className="residue-list">{group.items.map((item) => {
                        const selected = selectedDuplicateIds.includes(item.id)
                        const selectingWouldRemoveAll = !selected && group.items.every((candidate) => candidate.id === item.id || selectedDuplicateIds.includes(candidate.id))
                        return <li key={item.id}><label><input type="checkbox" checked={selected} disabled={selectingWouldRemoveAll} onChange={(event) => { setDuplicateCleanupConfirmed(false); setSelectedDuplicateIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id)) }} /><span><b>{selected ? '将移入废纸篓' : selectingWouldRemoveAll ? '保留此文件' : '保留或选择清理'} · {item.title}</b><small>{item.path} · {item.media_type === 'audio' ? '音频' : '视频'} · {item.file_origin === 'local' ? '本地源文件' : '下载文件'}</small></span></label></li>
                      })}</ol>
                    </section>)}</div>}
                    {selectedDuplicateIds.length > 0 && <label className="delete-file-option duplicate-confirm"><input type="checkbox" checked={duplicateCleanupConfirmed} onChange={(event) => setDuplicateCleanupConfirmed(event.target.checked)} /><span><b>确认把已选 {selectedDuplicateIds.length} 个重复媒体及其同名附属文件移入废纸篓</b><small>每组至少保留一个文件；操作前会重新比对内容。</small></span></label>}
                    <div className="maintenance-card-actions"><button className="delete-button" type="button" onClick={() => void cleanupSelectedDuplicates()} disabled={maintenanceWorking || selectedDuplicateIds.length === 0 || !duplicateCleanupConfirmed}>清理已选 {selectedDuplicateIds.length} 个重复媒体</button></div>
                  </section>
                  {maintenanceNotice && <p className="settings-notice maintenance-notice" role="status">{maintenanceNotice}</p>}
                  <footer className="tab-save-footer">{settingsNotice ? <p className="settings-notice" role="status">{settingsNotice}</p> : <span>自动清理只在应用启动时执行；未完成文件始终需要手动确认。</span>}<button className="primary-button" type="button" onClick={() => saveSettings('maintenance')} disabled={savingSettings !== null}>{savingSettings === 'maintenance' ? '正在保存…' : '保存清理设置'}</button></footer>
                </section>}
              </div>
            ) : <div className="empty-state"><span><Icon name="settings" size={25} /></span><h2>正在读取下载设置</h2><p>请确认本地下载服务仍在运行。</p></div>}
          </section>
        )}
      </section>

      {advancedSearchOpen && (
        <div className="modal-backdrop" onMouseDown={() => setAdvancedSearchOpen(false)}>
          <section className="advanced-search-modal" role="dialog" aria-modal="true" aria-labelledby="advanced-search-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><p className="eyebrow">高级搜索</p><h2 id="advanced-search-title">筛选{page === 'audio' ? '音频' : '视频'}</h2><p>按内容属性缩小范围，设置只会在点击应用后生效。</p></div>
              <button className="icon-button" type="button" onClick={() => setAdvancedSearchOpen(false)} aria-label="关闭高级搜索"><Icon name="close" size={18} /></button>
            </header>
            <div className="advanced-filter-grid">
              {page === 'videos' && <div className="advanced-filter-control"><span>清晰度</span><SettingSelect ariaLabel="筛选清晰度" value={advancedVideoFilters.resolution} options={[{ value: '', label: '全部清晰度' }, ...(advancedVideoFilters.resolution && !videoFilterOptions.resolutions.includes(advancedVideoFilters.resolution) ? [{ value: advancedVideoFilters.resolution, label: advancedVideoFilters.resolution }] : []), ...videoFilterOptions.resolutions.filter((resolution) => resolution !== '未知').map((resolution) => ({ value: resolution, label: resolution }))]} onChange={(resolution) => setAdvancedVideoFilters((current) => ({ ...current, resolution }))} /></div>}
              <div className="advanced-filter-control"><span>文件状态</span><SettingSelect ariaLabel="筛选文件状态" value={advancedVideoFilters.fileStatus} options={[{ value: 'all', label: '全部文件' }, { value: 'available', label: '文件可用' }, { value: 'missing', label: '文件不存在' }]} onChange={(fileStatus) => setAdvancedVideoFilters((current) => ({ ...current, fileStatus: fileStatus as VideoFileStatus }))} /></div>
              <div className="advanced-filter-control"><span>排序方式</span><SettingSelect ariaLabel="筛选排序方式" value={`${advancedVideoFilters.sortBy}:${advancedVideoFilters.sortOrder}`} options={[{ value: 'created_at:desc', label: '最新保存' }, { value: 'created_at:asc', label: '最早保存' }, { value: 'title:asc', label: '标题 A-Z' }, { value: 'title:desc', label: '标题 Z-A' }, { value: 'file_size:desc', label: '文件最大' }, { value: 'file_size:asc', label: '文件最小' }, { value: 'duration:desc', label: '时长最长' }, { value: 'duration:asc', label: '时长最短' }]} onChange={(value) => { const [sortBy, sortOrder] = value.split(':') as [VideoSortBy, VideoSortOrder]; setAdvancedVideoFilters((current) => ({ ...current, sortBy, sortOrder })) }} /></div>
              <label className="advanced-favorite-toggle"><span><b>只看收藏</b><small>隐藏未收藏的内容</small></span><input className="sr-only" type="checkbox" checked={advancedVideoFilters.favoriteOnly} onChange={(event) => setAdvancedVideoFilters((current) => ({ ...current, favoriteOnly: event.target.checked }))} /><span className="advanced-toggle-track" aria-hidden="true"><i /></span></label>
              <fieldset className="advanced-filter-wide"><legend>来源平台</legend><div className="advanced-option-list">{videoFilterOptions.platforms.length > 0 ? videoFilterOptions.platforms.map((platform) => <label key={platform}><input className="sr-only" type="checkbox" checked={advancedVideoFilters.platforms.includes(platform)} onChange={() => toggleAdvancedPlatform(platform)} /><span>{platform}</span><Icon name="check" size={15} /></label>) : <span className="advanced-filter-empty">暂无可选平台</span>}</div></fieldset>
              <fieldset className="advanced-filter-wide"><legend>文件格式</legend><div className="advanced-option-list">{videoFilterOptions.formats.length > 0 ? videoFilterOptions.formats.map((format) => { const value = format.toLowerCase(); return <label key={format}><input className="sr-only" type="checkbox" checked={advancedVideoFilters.fileFormats.includes(value)} onChange={() => toggleAdvancedFileFormat(value)} /><span>{format}</span><Icon name="check" size={15} /></label> }) : <span className="advanced-filter-empty">暂无可选格式</span>}</div></fieldset>
            </div>
            <footer><button type="button" className="text-button" onClick={resetAdvancedSearchDraft}>重置高级条件</button><div><button type="button" className="cancel-button" onClick={() => setAdvancedSearchOpen(false)}>取消</button><button type="button" className="primary-button" onClick={applyAdvancedSearch}>应用筛选</button></div></footer>
          </section>
        </div>
      )}

      {videoBatchAction && (
        <div className="modal-backdrop" onMouseDown={() => { if (!videoBatchWorking) setVideoBatchAction(null) }}>
          <section className="video-batch-modal" role="dialog" aria-modal="true" aria-labelledby="video-batch-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">批量管理</p>
            <h2 id="video-batch-title">{videoBatchAction === 'favorite' ? `收藏已选 ${selectedVideoIds.length} 项？` : videoBatchAction === 'unfavorite' ? `取消收藏已选 ${selectedVideoIds.length} 项？` : `从${page === 'audio' ? '音频' : '视频'}管理移除已选 ${selectedVideoIds.length} 项？`}</h2>
            <p>{videoBatchAction === 'remove' ? `只会移除管理记录，不会删除本地${page === 'audio' ? '音频' : '视频'}文件。${page === 'videos' ? '合集暂不参与批量操作。' : ''}` : '只更新收藏状态，不会修改本地文件。'}</p>
            <footer><button type="button" className="cancel-button" onClick={() => setVideoBatchAction(null)} disabled={videoBatchWorking}>取消</button><button type="button" className={videoBatchAction === 'remove' ? 'delete-button' : 'primary-button'} onClick={() => void confirmVideoBatchAction()} disabled={videoBatchWorking}>{videoBatchWorking ? '正在处理…' : '确认'}</button></footer>
          </section>
        </div>
      )}

      {sourceFollowOpen && (
        <div className="modal-backdrop source-follow-backdrop" onMouseDown={() => { if (!sourceFollowWorking) setSourceFollowOpen(false) }}>
          <section className="source-follow-modal" role="dialog" aria-modal="true" aria-labelledby="source-follow-title" onMouseDown={(event) => event.stopPropagation()}>
            <header className="modal-heading">
              <div><p className="eyebrow">个人更新订阅</p><h2 id="source-follow-title">关注频道与合集</h2><p>每次启动只检查更新，不会自动下载；确认选择后才创建任务。</p></div>
              <button className="icon-button" type="button" onClick={() => setSourceFollowOpen(false)} disabled={Boolean(sourceFollowWorking)} aria-label="关闭"><Icon name="close" size={18} /></button>
            </header>
            <form className="source-follow-add" onSubmit={addSourceFollow}>
              <label htmlFor="source-follow-url">频道、合集或播放列表地址</label>
              <div><input id="source-follow-url" type="url" value={sourceFollowUrl} onChange={(event) => setSourceFollowUrl(event.target.value)} placeholder="https://…" autoFocus={sourceFollows.length === 0} /><button className="primary-button" type="submit" disabled={Boolean(sourceFollowWorking) || !sourceFollowUrl.trim()}>{sourceFollowWorking === 'add' ? '正在读取…' : '添加关注'}</button></div>
            </form>
            <div className={`source-follow-workspace${sourceFollows.length === 0 ? ' is-empty' : ''}`}>
              {sourceFollows.length === 0 ? <section className="source-follow-onboarding"><span><Icon name="repeat" size={24} /></span><b>还没有关注源</b><p>在上方粘贴频道、合集或播放列表地址，添加后会在这里显示尚未下载的新视频。</p></section> : <>
                <nav className="source-follow-list" aria-label="已关注来源">
                  {sourceFollows.map((follow) => <button key={follow.id} type="button" className={activeSourceFollowId === follow.id ? 'is-active' : ''} onClick={() => { setActiveSourceFollowId(follow.id); setSelectedFollowEntryUrls(follow.entries.map((entry) => entry.webpage_url)); setSourceFollowNotice(null) }}><span className="source-follow-cover"><img src={follow.thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></span><span><b>{follow.title}</b><small>{follow.last_error ? '上次检查失败' : follow.new_count ? `${follow.new_count} 个新增` : '没有新增'}</small></span>{follow.new_count > 0 && <i>{follow.new_count}</i>}</button>)}
                </nav>
                <section className="source-follow-detail">
                {activeSourceFollow ? <>
                  <header><div><p className="eyebrow">{activeSourceFollow.source_platform || '视频来源'}</p><h3>{activeSourceFollow.title}</h3><span>{activeSourceFollow.uploader || '未知作者'} · {activeSourceFollow.last_checked_at ? `${dateLabel(activeSourceFollow.last_checked_at)} 检查` : '尚未检查'}</span></div><div><button className="cancel-button" type="button" onClick={() => void checkSourceFollow(activeSourceFollow)} disabled={Boolean(sourceFollowWorking)}>{sourceFollowWorking === `check:${activeSourceFollow.id}` ? '检查中…' : '检查更新'}</button><button className="text-button is-danger" type="button" onClick={() => void deleteSourceFollow(activeSourceFollow)} disabled={Boolean(sourceFollowWorking)}>取消关注</button></div></header>
                  <label className="friendly-switch source-follow-startup"><span><b>启动时检查</b><small>只读取更新列表，不自动下载。</small></span><input type="checkbox" checked={activeSourceFollow.check_on_startup} onChange={() => void toggleSourceFollowStartup(activeSourceFollow)} disabled={Boolean(sourceFollowWorking)} /></label>
                  {activeSourceFollow.last_error && <p className="notice is-error" role="status">上次检查失败：{activeSourceFollow.last_error}</p>}
                  {activeSourceFollow.entries.length > 0 ? <>
                    <div className="source-follow-selection"><label><input type="checkbox" checked={selectedFollowEntryUrls.length === activeSourceFollow.entries.length} onChange={(event) => setSelectedFollowEntryUrls(event.target.checked ? activeSourceFollow.entries.map((entry) => entry.webpage_url) : [])} />全选新增视频</label><span>已选 {selectedFollowEntryUrls.length} / {activeSourceFollow.entries.length}</span></div>
                    <ol className="source-follow-entries">{activeSourceFollow.entries.map((entry) => <li key={entry.webpage_url}><label><input type="checkbox" checked={selectedFollowEntryUrls.includes(entry.webpage_url)} onChange={(event) => setSelectedFollowEntryUrls((current) => event.target.checked ? [...current, entry.webpage_url] : current.filter((url) => url !== entry.webpage_url))} /><span className="source-follow-entry-cover"><img src={entry.thumbnail || videoPlaceholder} alt="" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></span><span><b>{entry.title}</b><small>第 {entry.playlist_index} 个 · {durationLabel(entry.duration)}</small></span></label></li>)}</ol>
                    <button className="primary-button source-follow-download" type="button" onClick={() => void downloadFollowEntries(activeSourceFollow)} disabled={Boolean(sourceFollowWorking) || selectedFollowEntryUrls.length === 0}>{sourceFollowWorking === `download:${activeSourceFollow.id}` ? '正在创建任务…' : `下载已选 ${selectedFollowEntryUrls.length} 个`}</button>
                  </> : <div className="source-follow-detail-empty"><Icon name="check" size={25} /><b>目前没有新增视频</b><span>本地已完成和正在下载的内容都会自动排除。</span></div>}
                </> : <div className="source-follow-detail-empty"><Icon name="repeat" size={25} /><b>选择一个关注源</b><span>这里会显示尚未下载的新视频。</span></div>}
                </section>
              </>}
            </div>
            {sourceFollowNotice && <p className="notice source-follow-notice" role="status">{sourceFollowNotice}</p>}
          </section>
        </div>
      )}

      {resourceSearchOpen && (
        <div className="modal-backdrop resource-search-backdrop" onMouseDown={closeResourceSearch}>
          <section className="resource-search-modal" role="dialog" aria-modal="true" aria-labelledby="resource-search-title" onMouseDown={(event) => event.stopPropagation()}>
            <header className="task-modal-controls">
              <div><h2 id="resource-search-title">搜索资源</h2><p>选择平台后搜索关键词，勾选结果并按当前下载设置创建任务。</p></div>
              <button className="icon-button" type="button" onClick={closeResourceSearch} aria-label="关闭资源搜索" disabled={resourceSearchWorking || resourceDownloadWorking}><Icon name="close" /></button>
            </header>

            <form className="resource-search-form" onSubmit={searchResources}>
              <label htmlFor="resource-search-query">关键词</label>
              <div className="resource-search-controls">
                <SettingSelect
                  ariaLabel="搜索平台"
                  value={resourceSearchProvider}
                  options={resourceSearchProviders.length > 0
                    ? resourceSearchProviders.map((provider) => ({ value: provider.key, label: provider.label }))
                    : [{ value: '', label: '正在读取平台…' }]}
                  onChange={selectResourceSearchProvider}
                  disabled={resourceSearchWorking || resourceDownloadWorking || resourceSearchProviders.length === 0}
                />
                <div className="resource-search-query">
                  <Icon name="search" size={18} />
                  <input id="resource-search-query" value={resourceSearchQuery} onChange={(event) => setResourceSearchQuery(event.target.value)} placeholder="输入要查找的视频、节目或音频" autoComplete="off" autoFocus />
                  <button type="submit" disabled={resourceSearchWorking || resourceDownloadWorking || !resourceSearchProvider || !resourceSearchQuery.trim()}>{resourceSearchWorking ? '搜索中…' : '搜索'}</button>
                </div>
              </div>
            </form>

            {resourceSearchNotice && <p className="notice resource-search-notice" role="status">{resourceSearchNotice}</p>}

            {resourceSearchResults.length > 0 && <section className="resource-search-results" aria-label="搜索结果">
              <header>
                <label><input type="checkbox" checked={resourceSearchResults.every((result) => selectedResourceUrls.includes(result.webpage_url))} onChange={(event) => setSelectedResourceUrls(event.target.checked ? resourceSearchResults.map((result) => result.webpage_url) : [])} />全选</label>
                <span>已选 {selectedResourceUrls.length} / {resourceSearchResults.length}</span>
              </header>
              <ol>
                {resourceSearchResults.map((result) => <li key={`${result.id}:${result.webpage_url}`} className={selectedResourceUrls.includes(result.webpage_url) ? 'is-selected' : ''}>
                  <label>
                    <input type="checkbox" checked={selectedResourceUrls.includes(result.webpage_url)} onChange={() => toggleResourceResult(result.webpage_url)} />
                    <span className="resource-result-cover"><img src={result.thumbnail || videoPlaceholder} alt="" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></span>
                    <span className="resource-result-copy"><b>{result.title}</b><small>{result.uploader || result.provider_label} · {durationLabel(result.duration)} · {dateLabel(result.upload_date)}</small></span>
                  </label>
                </li>)}
              </ol>
              <footer>
                <button className="primary-button" type="button" onClick={() => void downloadSelectedResources()} disabled={resourceDownloadWorking || selectedResourceUrls.length === 0}>{resourceDownloadWorking ? '正在创建任务…' : `下载已选 ${selectedResourceUrls.length} 项`}</button>
              </footer>
            </section>}

            {resourceSearchHasSearched && resourceSearchResults.length === 0 && !resourceSearchWorking && <div className="resource-search-empty"><Icon name="search" size={22} /><b>没有可显示的搜索结果</b><span>换一个关键词，或选择其他平台后再试。</span></div>}
          </section>
        </div>
      )}

      {modalOpen && (
        <div className="modal-backdrop" onMouseDown={closeModal}>
          <section className="task-modal" role="dialog" aria-modal="true" aria-labelledby="new-task-title" onMouseDown={(event) => event.stopPropagation()}>
            <header className="task-modal-controls"><div><h2 id="new-task-title">新建下载</h2><p>单个链接先解析格式；多个链接每行一个并批量创建任务。</p></div><button className="icon-button" onClick={closeModal} aria-label="关闭下载"><Icon name="close" /></button></header>
            <form onSubmit={inspect} className="modal-form">
              <label htmlFor="video-url">下载链接</label>
              <div><textarea id="video-url" value={url} onChange={(event) => { setUrl(event.target.value); setEngineHint('auto'); if (event.target.value) setTorrentFile(null) }} placeholder={'网页、直链、magnet 或 thunder://\n多个链接时每行粘贴一个'} autoComplete="off" autoFocus disabled={Boolean(torrentFile)} rows={batchUrls.length > 1 ? 5 : 2} /><button type="submit" disabled={working || (!url.trim() && !torrentFile)}>{working && !media ? (batchUrls.length > 1 ? '创建中…' : '解析中…') : batchUrls.length > 1 ? `创建 ${batchUrls.length} 个任务` : '解析'}</button></div>
              {batchUrls.length > 1 && <p className="input-help">已识别 {batchUrls.length} 个链接，将逐条解析并使用当前下载设置创建任务；批量模式自动选择各链接的最佳格式。</p>}
              <div className="download-alternate-input"><span>或</span><label className="torrent-file-picker"><input type="file" accept=".torrent,application/x-bittorrent" aria-label="上传 .torrent 文件" onChange={(event) => chooseTorrentFile(event.target.files?.[0])} /><b>{torrentFile ? torrentFile.name : '上传 .torrent 文件'}</b></label>{torrentFile && <button type="button" className="text-button" onClick={() => { setTorrentFile(null); setEngineHint('auto') }}>移除</button>}</div>
            </form>
            <details className="download-options">
              <summary><span>下载选项</span><small>优先级：{priorityLabel(downloadPriority)}</small></summary>
              <fieldset className="option-selector" aria-describedby="download-priority-help">
                <legend>任务优先级</legend>
                <div className="option-selector-grid priority-selector-grid">
                  {downloadPriorityOptions.map((option) => <label key={option.value}><input type="radio" name="download-priority" value={option.value} checked={downloadPriority === option.value} onChange={() => setDownloadPriority(option.value)} /><span className="option-selector-choice"><b>{option.label}</b><small>{option.hint}</small></span></label>)}
                </div>
                <small id="download-priority-help">只影响尚未开始的任务；同优先级按创建顺序执行。</small>
              </fieldset>
            </details>
            {notice && <div className="notice download-notice" role="status"><span>{notice}</span>{notice.includes('已下载') && <button type="button" onClick={() => { closeModal(); openLibraryPage('videos') }}>查看视频</button>}</div>}
            {media && (media.kind === 'video' || media.kind === 'playlist') && (
              <div className="inspect-result">
                <div className={`media-cover${media.thumbnail ? '' : ' is-fallback'}`}><img src={media.thumbnail || videoPlaceholder} alt="" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.closest('.media-cover')?.classList.add('is-fallback'); event.currentTarget.src = videoPlaceholder }} /><span className="cover-fallback-reason">封面暂时无法显示</span></div>
                <div className="media-details">
                  <p className="media-source">{media.kind === 'playlist' ? '播放列表' : media.uploader || '未知上传者'}</p>
                  <h3>{media.title}</h3>
                  {media.kind === 'video' ? <>
                    <div className="media-facts"><span>{durationLabel(media.duration)}</span><span>{media.resolution || '视频'}</span><span>{dateLabel(media.upload_date)}</span></div>
                    <div className="format-picker">
                      <label htmlFor="format">下载格式</label>
                      <select id="format" value={selectedFormat || ''} onChange={(event) => setSelectedFormat(event.target.value || null)}>{media.formats.length === 0 && <option value="">自动选择最佳格式</option>}{media.formats.map((format) => <option key={format.format_id} value={format.format_id}>{format.label}{format.file_size_label ? ` · ${format.file_size_label}` : ''}</option>)}</select>
                      <button type="button" className="primary-button" onClick={() => void createDownload()} disabled={working}>开始下载</button>
                    </div>
                  </> : <>
                    <p className="playlist-download-note">共 {media.entry_count} 个视频。选择后，每个视频会单独创建下载任务，并按当前通用下载设置保存。</p>
                    <fieldset className="option-selector" aria-describedby="playlist-quality-help">
                      <legend>下载清晰度</legend>
                      <div className="option-selector-grid quality-selector-grid">
                        {playlistQualityOptions.map((option) => <label key={option.value}><input type="radio" name="playlist-quality" value={option.value} checked={selectedPlaylistFormat === option.value} onChange={() => setSelectedPlaylistFormat(option.value)} /><span className="option-selector-choice"><b>{option.label}</b><small>{option.hint}</small></span></label>)}
                      </div>
                      <small id="playlist-quality-help">每个视频会在自己的可用格式中，优先选择不高于所选清晰度的版本。</small>
                    </fieldset>
                    <div className="playlist-selection-bar"><label><input type="checkbox" checked={selectedPlaylistUrls.length === media.entries.length} onChange={(event) => setSelectedPlaylistUrls(event.target.checked ? media.entries.map((entry) => entry.webpage_url) : [])} />全选</label><span>已选 {selectedPlaylistUrls.length} / {media.entry_count} 个</span></div>
                    <ol className="playlist-preview-list playlist-select-list">{media.entries.map((entry) => <li key={entry.webpage_url}><label><input type="checkbox" checked={selectedPlaylistUrls.includes(entry.webpage_url)} onChange={() => togglePlaylistEntry(entry.webpage_url)} /><span>{entry.playlist_index}</span><span className="playlist-entry-cover"><img src={entry.thumbnail || videoPlaceholder} alt="" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = videoPlaceholder }} /></span><b>{entry.title}</b><small>{durationLabel(entry.duration)}</small></label></li>)}</ol>
                    <button type="button" className="primary-button playlist-download-button" onClick={() => void createDownload()} disabled={working || selectedPlaylistUrls.length === 0}>{selectedPlaylistUrls.length === media.entry_count ? `下载全部 ${media.entry_count} 个视频` : `下载已选 ${selectedPlaylistUrls.length} 个视频`}</button>
                  </>}
                </div>
              </div>
            )}
            {media?.kind === 'file' && <div className="engine-inspect-result"><p className="media-source">{media.source_type === 'thunder' ? '迅雷链接 · 已转为直链' : '直链文件'} · aria2</p><h3>{media.title}</h3><div className="media-facts"><span>{fileSizeLabel(media.file_size)}</span><span>{media.content_type || '文件类型未知'}</span><span>{media.resolved_host || '目标地址未返回主机'}</span></div><p>{media.message}</p><button type="button" className="primary-button" onClick={() => void createDownload()} disabled={working}>使用 aria2 下载</button></div>}
            {media?.kind === 'torrent' && <div className="engine-inspect-result">
              <p className="media-source">BT 下载 · {media.engine === 'qbittorrent' ? 'qBittorrent' : 'aria2'}</p>
              <h3>{media.title}</h3>
              <div className="media-facts"><span>{media.source_type === 'magnet' ? '磁力链接' : media.source_type === 'torrent_file' ? '种子文件' : media.source_type === 'thunder_bt' ? '迅雷 BT 链接' : '种子地址'}</span><span>{`${torrentMediaFiles.length} 个视频或音频`}</span><span>{fileSizeLabel(torrentMediaSize)}</span></div>
              {media.message && <p>{media.message}</p>}
              {torrentMediaFiles.length > 1 ? <>
                <section className="torrent-picker" aria-label="选择要下载的媒体文件">
                  <header className="torrent-selection-bar">
                    <label><input type="checkbox" checked={selectedTorrentFileIndexes.length === torrentMediaFiles.length} onChange={(event) => setSelectedTorrentFileIndexes(event.target.checked ? torrentMediaFiles.map((file) => file.index) : [])} />全选媒体文件</label>
                    <span><b>已选 {selectedTorrentFileIndexes.length} / {torrentMediaFiles.length}</b><small>{fileSizeLabel(selectedTorrentMediaSize)}</small></span>
                  </header>
                  <label className="torrent-search-field" htmlFor="torrent-file-search">
                    <span>搜索文件</span>
                    <input id="torrent-file-search" type="search" value={torrentFileQuery} onChange={(event) => { setTorrentFileQuery(event.target.value); setTorrentVisibleFileCount(torrentVisibleFileStep) }} placeholder="输入文件名或目录" autoComplete="off" />
                  </label>
                  <div className="torrent-group-list">
                    {visibleTorrentFileGroups.map((group) => {
                      const groupIndexes = group.files.map((file) => file.index)
                      const groupSelected = groupIndexes.every((index) => selectedTorrentFileIndexes.includes(index))
                      return <section className="torrent-file-group" key={group.name}>
                        <header><span><b>{group.name}</b><small>{group.files.length} 个媒体文件</small></span><button type="button" aria-pressed={groupSelected} onClick={() => toggleTorrentGroup(groupIndexes)}>{torrentFileQuery.trim() ? (groupSelected ? '取消匹配项' : '选择匹配项') : (groupSelected ? '取消目录' : '选择目录')}</button></header>
                        <ul>{group.visibleFiles.map((file) => <li key={file.index} className={selectedTorrentFileIndexes.includes(file.index) ? 'is-selected' : ''}><label><input type="checkbox" checked={selectedTorrentFileIndexes.includes(file.index)} onChange={() => toggleTorrentFile(file.index)} /><span title={file.path}>{torrentFileLabel(file.path, group.name)}</span><b>{fileSizeLabel(file.size)}</b></label></li>)}</ul>
                      </section>
                    })}
                    {filteredTorrentMediaFiles.length === 0 && <p className="torrent-search-empty" role="status">没有匹配的媒体文件，换一个文件名或目录关键词再试。</p>}
                  </div>
                  {filteredTorrentMediaFiles.length > torrentVisibleFileCount && <footer className="torrent-list-footer"><span>已显示 {torrentVisibleFileCount} / {filteredTorrentMediaFiles.length} 个匹配文件</span><button type="button" onClick={() => setTorrentVisibleFileCount((count) => count + torrentVisibleFileStep)}>显示更多</button></footer>}
                </section>
                <p className="input-help">仅下载已选视频或音频；{media.engine === 'qbittorrent' ? 'qBittorrent' : 'aria2'} 会继续连接可用节点或 Web Seed。</p>
              </> : torrentMediaFiles.length === 1 ? <p className="input-help">已排除非音视频文件，仅下载这个媒体文件。</p> : <p className="notice">种子中没有可下载的视频或音频文件。</p>}
              <button type="button" className="primary-button" onClick={() => void createDownload()} disabled={working || torrentMediaFiles.length === 0 || selectedTorrentFileIndexes.length === 0}>{torrentMediaFiles.length > 1 ? (selectedTorrentFileIndexes.length === torrentMediaFiles.length ? `下载全部 ${torrentMediaFiles.length} 个媒体文件` : `下载已选 ${selectedTorrentFileIndexes.length} 个媒体文件`) : torrentMediaFiles.length === 1 ? `下载 ${torrentMediaFiles[0].path}` : '没有可下载的视频或音频'}</button>
            </div>}
          </section>
        </div>
      )}

      {duplicateDownloadOpen && (
        <div className="modal-backdrop" onMouseDown={() => setDuplicateDownloadOpen(false)}>
          <section className="duplicate-download-modal" role="dialog" aria-modal="true" aria-labelledby="duplicate-download-title" onMouseDown={(event) => event.stopPropagation()}>
            <h2 id="duplicate-download-title">这个链接正在下载中</h2>
            <p>为了避免重复下载，系统不会同时下载同一个链接。</p>
            <p>继续操作会先取消当前下载，再重新开始下载。</p>
            <footer><button type="button" className="cancel-button" onClick={() => setDuplicateDownloadOpen(false)}>取消</button><button type="button" className="primary-button" onClick={() => { setDuplicateDownloadOpen(false); void createDownload(true) }} disabled={working}>取消原下载并重新开始</button></footer>
          </section>
        </div>
      )}

      {taskActionTarget && (
        <div className="modal-backdrop" onMouseDown={() => { if (!taskActionWorking) setTaskActionTarget(null) }}>
          <section className="task-action-modal" role="dialog" aria-modal="true" aria-labelledby="task-action-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">{taskActionTarget.action === 'cancel' ? '取消下载' : taskActionTarget.action === 'deleteMany' ? '批量删除任务' : '删除任务'}</p>
            <h2 id="task-action-title">{taskActionTarget.action === 'cancel' ? '取消当前下载？' : taskActionTarget.action === 'deleteMany' ? `删除已选 ${taskActionTarget.taskIds.length} 个任务？` : '从下载任务中移除？'}</h2>
            <p>{taskActionTarget.action === 'cancel' ? '下载会立即停止，已下载的部分会保留；之后可以通过“继续下载”复用临时文件。' : taskActionTarget.action === 'deleteMany' ? '将删除所选任务及其下载日志；不会删除已放入视频管理的本地视频。下载中的任务需要先取消，因此不会出现在可选范围内。' : '将从下载任务中移除，并清理下载日志；不会删除已放入视频管理的本地视频。'}</p>
            {taskActionTarget.action !== 'cancel' && <label className="remove-incomplete-files"><input type="checkbox" checked={removeIncompleteFiles} onChange={(event) => setRemoveIncompleteFiles(event.target.checked)} /><span><b>同时移除未完成文件</b><small>默认移入 macOS 废纸篓；已完成视频不会被删除。</small></span></label>}
            {taskActionNotice && <p className="notice" role="status">{taskActionNotice}</p>}
            {taskCleanupReport && <div className="cleanup-report">
              {taskCleanupReport.trashed_files.length > 0 && <section><b>已移入废纸篓</b><ul>{taskCleanupReport.trashed_files.map((path) => <li key={path}>{path}</li>)}</ul></section>}
              {taskCleanupReport.failed_files.length > 0 && <section><b>未能移动</b><ul>{taskCleanupReport.failed_files.map((item) => <li key={item.path}>{item.path}<small>{item.error}</small></li>)}</ul></section>}
            </div>}
            <footer><button type="button" className="cancel-button" onClick={() => setTaskActionTarget(null)} disabled={taskActionWorking}>返回</button><button type="button" className={taskActionTarget.action === 'cancel' ? 'primary-button' : 'delete-button'} onClick={() => void confirmTaskAction()} disabled={taskActionWorking}>{taskActionWorking ? '正在处理…' : taskActionTarget.action === 'cancel' ? '确认取消下载' : taskActionTarget.action === 'deleteMany' ? `确认删除 ${taskActionTarget.taskIds.length} 项` : '确认删除任务'}</button></footer>
          </section>
        </div>
      )}

      {inspectProgressOpen && inspectJob && (
        <div className="modal-backdrop inspect-progress-backdrop" onMouseDown={() => setInspectProgressOpen(false)}>
          <section className="inspect-progress-modal" role="dialog" aria-modal="true" aria-labelledby="inspect-progress-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><p className="eyebrow">链接解析</p><h2 id="inspect-progress-title">{inspectStatusLabel(inspectJob.status)}</h2><p>{inspectJob.status === 'running' ? `正在识别下载类型并读取内容信息，${elapsedLabel(inspectJob.started_at)}。` : inspectJob.status === 'completed' ? '解析完成，可以确认下载。' : inspectJob.status === 'failed' ? '请检查链接是否仍可访问，或改用其他下载方式。' : '正在等待解析服务开始。'}</p></div>
              <div className="inspect-progress-actions"><span className={`inspect-status is-${inspectJob.status}`}>{inspectStatusLabel(inspectJob.status)}</span><button className="icon-button" type="button" onClick={() => setInspectProgressOpen(false)} aria-label="关闭解析进度"><Icon name="close" /></button></div>
            </header>
            <div className="inspect-log-region"><div className="inspect-log-heading"><span>解析记录</span></div><ol className="inspect-log-list" ref={inspectLogRef} aria-live="polite">
              {inspectJob.logs.map((log) => <li key={log.id} className={`is-${log.level}`}><time>{timeLabel(log.created_at)}</time><span>{log.message}</span></li>)}
              {(inspectJob.status === 'queued' || inspectJob.status === 'running') && <li className="inspect-log-loading"><time aria-hidden="true" /><span>加载中<span className="inspect-loading-dots" aria-hidden="true"><i /><i /><i /></span></span></li>}
            </ol></div>
            {inspectJob.status === 'failed' && <footer><p>{inspectJob.error || '该链接暂时无法下载。请检查链接是否正确、内容是否公开可访问。'}</p><button className="primary-button" type="button" onClick={() => { setInspectProgressOpen(false); setInspectJob(null) }}>返回修改链接</button></footer>}
          </section>
        </div>
      )}

      {outputPlayer && (
        <div className="modal-backdrop player-backdrop" onMouseDown={() => setOutputPlayer(null)}>
          <section className={`player-modal${outputPlayerIsAudio ? ' is-audio' : ''}`} role="dialog" aria-modal="true" aria-labelledby="output-player-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><p className="eyebrow">{outputPlayerIsAudio ? '任务音频' : '任务输出'}</p><h2 id="output-player-title">{outputPlayer.output.relative_path}</h2><p>{fileSizeLabel(outputPlayer.output.size)} · {outputPlayer.output.file_type}</p></div>
              <button className="icon-button" onClick={() => setOutputPlayer(null)} aria-label="关闭播放器"><Icon name="close" /></button>
            </header>
            {outputPlayerIsAudio ? <AudioWavePlayer
              src={`${apiBase}/api/v1/downloads/${outputPlayer.task.id}/outputs/${outputPlayer.output.index}/file`}
              title={outputPlayer.output.relative_path}
              thumbnail={outputPlayer.task.thumbnail}
              autoPlay
              repeatMode={audioRepeatMode}
              onCycleRepeat={cycleAudioRepeatMode}
              onError={() => setOutputPlayerNotice('浏览器无法读取此音频，请打开文件位置并使用本地播放器播放。')}
            /> : <div className="player-stage">
              <video src={`${apiBase}/api/v1/downloads/${outputPlayer.task.id}/outputs/${outputPlayer.output.index}/file`} controls autoPlay playsInline preload="metadata" onError={() => setOutputPlayerNotice('浏览器无法直接播放此文件，请打开文件位置并使用本地播放器播放。')} />
            </div>}
            {outputPlayerNotice && <p className="notice" role="status">{outputPlayerNotice}</p>}
            <footer><span>正在播放任务的原始输出文件，不会转码。</span><div><button className="cancel-button" onClick={() => void revealTaskOutput(outputPlayer.task, outputPlayer.output)}>打开文件位置</button><button className="cancel-button" onClick={() => setOutputPlayer(null)}>关闭播放器</button></div></footer>
          </section>
        </div>
      )}

      {legalModal && (
        <div className="modal-backdrop legal-backdrop" onMouseDown={() => setLegalModal(null)}>
          <section className="legal-modal" role="dialog" aria-modal="true" aria-labelledby="legal-modal-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><p className="eyebrow">{legalModal === 'open-source' ? 'OPEN SOURCE' : 'LOCAL USE'}</p><h2 id="legal-modal-title">{legalModal === 'open-source' ? '开源软件与致谢' : '使用与免责说明'}</h2></div>
              <button className="icon-button" type="button" onClick={() => setLegalModal(null)} aria-label="关闭"><Icon name="close" size={18} /></button>
            </header>
            {legalModal === 'open-source' ? <>
              <p className="legal-intro">Video Downloader 是一个本地界面与任务管理层。感谢以下项目的作者和贡献者；各项目的著作权及许可仍归原权利人，本项目不会改变其原许可证。</p>
              <div className="legal-component-list">
                <article><div><b>yt-dlp</b><code>{health?.engine_version || '未检测到'}</code></div><p>网页媒体解析与下载 · The Unlicense</p><a href="https://github.com/yt-dlp/yt-dlp" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>FFmpeg</b><code>{health?.ffmpeg_version || '未检测到'}</code></div><p>音视频合并与转码 · LGPL-2.1-or-later；启用 GPL 组件的构建适用 GPL</p><a href="https://ffmpeg.org/legal.html" target="_blank" rel="noreferrer">许可证说明 ↗</a></article>
                <article><div><b>aria2</b><code>{health?.engines?.aria2.version || '未检测到'}</code></div><p>直链、迅雷链接与 BT 下载 · GPL-2.0-or-later</p><a href="https://github.com/aria2/aria2" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>qBittorrent</b><code>{health?.engines?.qbittorrent.version || '未连接（可选）'}</code></div><p>可选 BT 客户端连接 · GPL-2.0</p><a href="https://github.com/qbittorrent/qBittorrent" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>FastAPI</b><code>{health?.application?.fastapi_version || '未检测到'}</code></div><p>本地 HTTP API · MIT</p><a href="https://github.com/fastapi/fastapi" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>Uvicorn</b><code>{health?.application?.uvicorn_version || '未检测到'}</code></div><p>本地 ASGI 服务 · BSD-3-Clause</p><a href="https://github.com/Kludex/uvicorn" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>React / React DOM</b><code>19.2.8</code></div><p>用户界面 · MIT</p><a href="https://github.com/facebook/react" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>Plyr</b><code>3.8.4</code></div><p>视频播放器界面 · MIT</p><a href="https://github.com/sampotts/plyr" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
                <article><div><b>WaveSurfer.js</b><code>7.12.11</code></div><p>音频波形播放器 · BSD-3-Clause</p><a href="https://github.com/katspaugh/wavesurfer.js" target="_blank" rel="noreferrer">项目与许可证 ↗</a></article>
              </div>
              <p className="legal-footnote">这里显示后端实际检测到的工具版本和前端锁定版本。完整的直接依赖、传递依赖与发行义务记录在仓库的 <code>THIRD_PARTY_NOTICES.md</code>。</p>
            </> : <div className="disclaimer-copy">
              <section><h3>本地运行</h3><p>本软件是开源下载工具的本地界面与任务管理层。下载、解析和播放请求从你的设备直接发往相应来源；项目作者不运营媒体下载服务，也不会接收、托管或保存你下载的媒体。</p></section>
              <section><h3>不内置媒体内容</h3><p>软件包不附带视频、音频、磁力链接或种子文件。下载对象来自你输入的链接，或你主动使用资源搜索时从上游取得的结果。</p></section>
              <section><h3>仅限合法使用</h3><p>请只下载或播放你拥有权利、已经获得授权、属于公有领域，或来源平台明确允许下载的内容。你需要自行遵守所在地法律、内容权利人的授权条件和第三方平台条款，并对所提交链接、下载内容及后续使用负责。</p></section>
              <section><h3>第三方关系与可用性</h3><p>本项目与 YouTube、Bilibili 及其他内容平台不存在隶属、授权或背书关系。上游接口、链接、频道、地区限制和登录要求可能随时变化，本项目不保证其持续可用、准确或适合特定用途。</p></section>
              <section><h3>责任边界</h3><p>在适用法律允许的范围内，项目作者不对用户未经授权的下载、传播、公开播放或其他侵权行为，以及由第三方来源失效、数据错误或本地文件损失造成的损害承担责任。法律规定不得排除或限制的责任不受本说明影响。</p></section>
            </div>}
            <footer><span>Video Downloader {health?.application?.version || '0.1.0'}</span><button className="primary-button" type="button" onClick={() => setLegalModal(null)}>我已阅读</button></footer>
          </section>
        </div>
      )}

      {playerVideo && (
        <div className="modal-backdrop player-backdrop" onMouseDown={() => setPlayerVideo(null)}>
          <section className={`player-modal${currentPlayerMediaType === 'audio' ? ' is-audio' : ''}`} role="dialog" aria-modal="true" aria-labelledby="player-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><p className="eyebrow">{currentPlayerMediaType === 'audio' ? '音频播放器' : '本地播放器'}</p><h2 id="player-title">{playerVideo.title || `未命名${currentPlayerMediaType === 'audio' ? '音频' : '视频'}`}</h2><p>{sourceLabel(playerVideo)} · {durationLabel(playerVideo.duration)} · {libraryResolutionLabel({ kind: 'video', ...playerVideo })}</p></div>
              <button className="icon-button" onClick={() => setPlayerVideo(null)} aria-label="关闭播放器"><Icon name="close" /></button>
            </header>
            {currentPlayerMediaType === 'audio' ? <AudioWavePlayer
              key={playerVideo.id}
              src={`${apiBase}/api/v1/videos/${playerVideo.id}/file`}
              title={playerVideo.title || '未命名音频'}
              thumbnail={playerVideo.thumbnail}
              initialTime={playerVideo.watch_position || 0}
              autoPlay
              repeatMode={audioRepeatMode}
              onCycleRepeat={cycleAudioRepeatMode}
              onPrevious={audioPreviousPlaylistVideo ? () => playPlaylistVideo(audioPreviousPlaylistVideo) : undefined}
              onNext={audioNextPlaylistVideo ? () => playPlaylistVideo(audioNextPlaylistVideo) : undefined}
              onProgress={saveAudioProgress}
              onEnded={handleAudioEnded}
              onError={() => setPlayerNotice('浏览器无法读取此原始音频，请打开文件位置并使用本地播放器播放。')}
            /> : <div className="player-stage">
              <video ref={videoPlayerElementRef} key={playerVideo.id} src={`${apiBase}/api/v1/videos/${playerVideo.id}/file`} controls autoPlay playsInline preload="metadata" onEnded={() => { if (nextPlaylistVideo) playPlaylistVideo(nextPlaylistVideo) }} onError={() => setPlayerNotice('浏览器无法直接播放此原始文件，请打开文件位置并使用本地播放器播放。')}>
                {videoSubtitles.map((track) => <track
                  key={track.filename}
                  kind="subtitles"
                  src={`${apiBase}${track.url}`}
                  srcLang={track.language}
                  label={track.label}
                  default={track.filename === selectedSubtitleFilename}
                />)}
              </video>
            </div>}
            {currentPlayerMediaType === 'video' && videoSubtitles.length > 0 && (
              <label className="player-subtitle-selector">
                <span>字幕</span>
                <select value={selectedSubtitleFilename} onChange={(event) => void selectVideoSubtitle(event.target.value)}>
                  <option value="">关闭字幕</option>
                  {videoSubtitles.map((track) => <option key={track.filename} value={track.filename}>{track.label} · {track.format.toUpperCase()}</option>)}
                </select>
              </label>
            )}
            {currentPlayerMediaType === 'video' && videoCompatibility && <section className={`player-compatibility${videoCompatibility.direct_play_likely ? ' is-compatible' : ''}`} aria-label="播放兼容性">
              <div><b>{videoCompatibility.direct_play_likely ? '内置播放兼容' : '播放兼容性建议'}</b><span>{videoCompatibility.reason}</span><small>{videoCompatibility.container || '未知封装'} · {videoCompatibility.video_codec?.toUpperCase() || '未知视频编码'}{videoCompatibility.audio_codec ? ` / ${videoCompatibility.audio_codec.toUpperCase()}` : ''}</small></div>
              <div className="player-compatibility-actions">
                {videoCompatibility.players.system && <button className="cancel-button" type="button" onClick={() => void openExternalPlayer(playerVideo, 'system')} disabled={Boolean(compatibilityWorking)}>系统播放器</button>}
                {videoCompatibility.players.iina && <button className="cancel-button" type="button" onClick={() => void openExternalPlayer(playerVideo, 'iina')} disabled={Boolean(compatibilityWorking)}>IINA</button>}
                {videoCompatibility.players.vlc && <button className="cancel-button" type="button" onClick={() => void openExternalPlayer(playerVideo, 'vlc')} disabled={Boolean(compatibilityWorking)}>VLC</button>}
                {videoCompatibility.remux_available && compatibilityJob?.status !== 'completed' && <button className="primary-button" type="button" onClick={() => void startCompatibilityRemux(playerVideo)} disabled={Boolean(compatibilityWorking) || Boolean(compatibilityJob && ['queued', 'running'].includes(compatibilityJob.status))}>{compatibilityWorking === 'remux' ? '正在创建…' : compatibilityJob && ['queued', 'running'].includes(compatibilityJob.status) ? '正在生成兼容副本…' : '生成 MP4 兼容副本'}</button>}
              </div>
              {compatibilityJob?.status === 'failed' && <p className="notice is-error" role="status">兼容副本生成失败：{compatibilityJob.error || '未知错误'}</p>}
              {compatibilityJob?.status === 'completed' && <p className="notice" role="status">兼容副本已加入视频管理，原始文件保持不变。</p>}
            </section>}
            {playlistVideos.length > 0 && (
              <section className="player-playlist" aria-label="播放列表">
                <div className="player-playlist-heading"><div><b>播放列表</b><span>{playerPlaylistPosition + 1} / {playlistVideos.length}</span></div><div><button type="button" className="cancel-button" onClick={() => previousPlaylistVideo && playPlaylistVideo(previousPlaylistVideo)} disabled={!previousPlaylistVideo}>上一集</button><button type="button" className="play-action" onClick={() => nextPlaylistVideo && playPlaylistVideo(nextPlaylistVideo)} disabled={!nextPlaylistVideo}>下一集</button></div></div>
                <ol>{playlistVideos.map((video, index) => <li key={video.id}><button type="button" className={video.id === playerVideo.id ? 'is-current' : ''} onClick={() => playPlaylistVideo(video)}><span>{index + 1}</span><b>{video.title || `第 ${index + 1} 个${libraryItemMediaType({ kind: 'video', ...video }) === 'audio' ? '音频' : '视频'}`}</b><small>{durationLabel(video.duration)}</small></button></li>)}</ol>
              </section>
            )}
            {libraryItemMediaType({ kind: 'video', ...playerVideo }) === 'video' && playerVideo.metadata?.chapters?.some((chapter) => chapter.start_time !== null) && (
              <details className="player-chapters">
                <summary>视频分段 <span>点击跳转到对应位置</span></summary>
                <div className="player-chapter-list">
                  {playerVideo.metadata.chapters.map((chapter, index) => {
                    const startTime = chapter.start_time
                    if (startTime === null) return null
                    return <button key={`${startTime}-${index}`} type="button" onClick={() => { if (!videoPlayerElementRef.current) return; videoPlayerElementRef.current.currentTime = startTime; void videoPlayerElementRef.current.play().catch(() => undefined) }}><time>{durationLabel(startTime)}</time><span>{chapter.title || `第 ${index + 1} 段`}</span></button>
                  })}
                </div>
              </details>
            )}
            {playerNotice && <p className="notice" role="status">{playerNotice}</p>}
            <footer><span>正在播放本地原始文件，不会转码。</span><div><button className="cancel-button" onClick={() => void revealVideo(playerVideo)}>打开文件位置</button><button className="cancel-button" onClick={() => setPlayerVideo(null)}>关闭播放器</button></div></footer>
          </section>
        </div>
      )}

      {localVideoOpen && (
        <div className="modal-backdrop" onMouseDown={() => { if (!localVideoWorking && !localDirectoryScanning) setLocalVideoOpen(false) }}>
          <section className="task-modal local-video-modal" role="dialog" aria-modal="true" aria-labelledby="local-video-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p className="eyebrow">本地媒体</p>
                <h2 id="local-video-title">从目录添加文件</h2>
                <p>选择目录后选择要添加的视频和音频；只登记路径，不复制文件。</p>
              </div>
              <button className="icon-button" type="button" onClick={() => setLocalVideoOpen(false)} disabled={localVideoWorking || localDirectoryScanning} aria-label="关闭"><Icon name="close" size={18} /></button>
            </header>
            <div className="local-video-form">
              <label htmlFor="local-video-path">媒体目录</label>
              <div className="local-directory-input">
                <input id="local-video-path" value={localDirectoryPath} placeholder="请选择媒体目录" readOnly />
                <button className="cancel-button" type="button" onClick={() => void selectAndScanLocalMediaDirectory()} disabled={localDirectoryScanning || localVideoWorking} autoFocus>{localDirectoryScanning ? '正在扫描…' : '添加目录'}</button>
              </div>
              {localMediaFiles.length > 0 && <section className="local-media-results" aria-label="目录中的媒体文件">
                <header>
                  <label>
                    <input
                      type="checkbox"
                      checked={availableLocalMediaFiles.length > 0 && selectedLocalMediaPaths.length === availableLocalMediaFiles.length}
                      onChange={(event) => setSelectedLocalMediaPaths(event.target.checked ? availableLocalMediaFiles.map((file) => file.path) : [])}
                    />
                    全选可添加文件
                  </label>
                  <span>已选 {selectedLocalMediaPaths.length} / {availableLocalMediaFiles.length}</span>
                </header>
                <ol>
                  {localMediaFiles.map((file) => <li key={file.path} className={selectedLocalMediaPaths.includes(file.path) ? 'is-selected' : ''}>
                    <label>
                      <input type="checkbox" disabled={file.already_added} checked={selectedLocalMediaPaths.includes(file.path)} onChange={() => toggleLocalMedia(file.path)} />
                      <span><b>{file.name}</b><small>{file.relative_path} · {file.media_type === 'video' ? '视频' : '音频'} · {fileSizeLabel(file.size)}{file.already_added ? ' · 已添加' : ''}</small></span>
                    </label>
                  </li>)}
                </ol>
              </section>}
              {localVideoNotice && <p className="notice" role="status">{localVideoNotice}</p>}
              <footer>
                <button className="primary-button" type="button" onClick={() => void addSelectedLocalMedia()} disabled={localVideoWorking || localDirectoryScanning || selectedLocalMediaPaths.length === 0}>{localVideoWorking ? '正在添加…' : `添加已选 ${selectedLocalMediaPaths.length} 项`}</button>
              </footer>
            </div>
          </section>
        </div>
      )}

      {renameVideoTarget && (
        <div className="modal-backdrop" onMouseDown={() => { if (!renameVideoWorking) setRenameVideoTarget(null) }}>
          <section className="rename-video-modal" role="dialog" aria-modal="true" aria-labelledby="rename-video-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">媒体管理</p>
            <h2 id="rename-video-title">重命名{libraryItemMediaType({ kind: 'video', ...renameVideoTarget }) === 'audio' ? '音频' : '视频'}</h2>
            <label><span>新标题</span><input value={renameVideoTitle} onChange={(event) => setRenameVideoTitle(event.target.value)} autoFocus maxLength={500} /></label>
            <label className="remove-incomplete-files"><input type="checkbox" checked={renameVideoFile} onChange={(event) => setRenameVideoFile(event.target.checked)} disabled={!renameVideoTarget.file_exists} /><span><b>同时修改本地文件名</b><small>保留原扩展名，并同步重命名同名字幕、封面和 info JSON；遇到重名文件会停止。</small></span></label>
            {renameVideoNotice && <p className="notice" role="status">{renameVideoNotice}</p>}
            <footer><button className="cancel-button" type="button" onClick={() => setRenameVideoTarget(null)} disabled={renameVideoWorking}>取消</button><button className="primary-button" type="button" onClick={() => void confirmVideoRename()} disabled={renameVideoWorking || !renameVideoTitle.trim()}>{renameVideoWorking ? '正在重命名…' : '确认重命名'}</button></footer>
          </section>
        </div>
      )}

      {metadataEditTarget && (
        <div className="modal-backdrop" onMouseDown={() => { if (!metadataEditWorking) setMetadataEditTarget(null) }}>
          <section className="rename-video-modal metadata-edit-modal" role="dialog" aria-modal="true" aria-labelledby="metadata-edit-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">轻量整理</p>
            <h2 id="metadata-edit-title">编辑媒体信息</h2>
            <label><span>作者</span><input value={metadataUploader} onChange={(event) => setMetadataUploader(event.target.value)} maxLength={500} autoFocus placeholder="留空表示未知" /></label>
            <label><span>发布日期</span><input type="date" value={metadataUploadDate} onChange={(event) => setMetadataUploadDate(event.target.value)} /></label>
            <label><span>封面图片网址</span><input type="url" value={metadataThumbnail} onChange={(event) => setMetadataThumbnail(event.target.value)} maxLength={2048} placeholder="https://example.com/cover.jpg" /></label>
            {metadataThumbnail.trim() && <div className="metadata-cover-preview"><img src={metadataThumbnail.trim()} alt="封面预览" onLoad={(event) => { event.currentTarget.style.display = '' }} onError={(event) => { event.currentTarget.style.display = 'none' }} /></div>}
            <label><span>合集归属</span><select value={metadataPlaylistId} onChange={(event) => setMetadataPlaylistId(event.target.value)} disabled={metadataEditLoading}>
              <option value="">不属于任何合集</option>
              {metadataPlaylistOptions.map((playlist) => <option key={playlist.id} value={playlist.id}>{playlist.title} · {playlist.item_count} 项</option>)}
            </select></label>
            <small className="metadata-edit-help">只修改媒体库记录，不改动原始媒体文件；更换合集后会排在新合集末尾。</small>
            {metadataEditNotice && <p className="notice" role="status">{metadataEditNotice}</p>}
            <footer><button className="cancel-button" type="button" onClick={() => setMetadataEditTarget(null)} disabled={metadataEditWorking}>取消</button><button className="primary-button" type="button" onClick={() => void confirmMetadataEdit()} disabled={metadataEditWorking || metadataEditLoading}>{metadataEditWorking ? '正在保存…' : metadataEditLoading ? '正在读取合集…' : '保存媒体信息'}</button></footer>
          </section>
        </div>
      )}

      {upgradeTarget && (
        <div className="modal-backdrop" onMouseDown={() => { if (!upgradeWorking) setUpgradeTarget(null) }}>
          <section className="rename-video-modal video-upgrade-modal" role="dialog" aria-modal="true" aria-labelledby="video-upgrade-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">画质升级</p>
            <h2 id="video-upgrade-title">检查“{upgradeTarget.title || '未命名视频'}”</h2>
            <p>当前文件：{upgradeOptions?.current_resolution || upgradeTarget.resolution || '清晰度未知'}。下载新版期间会完整保留旧文件。</p>
            {upgradeWorking && !upgradeOptions ? <p className="notice" role="status">正在重新检查原页面可用画质…</p> : upgradeOptions && upgradeOptions.candidates.length > 0 && <label><span>选择更高画质</span><select value={upgradeFormat} onChange={(event) => setUpgradeFormat(event.target.value)}>
              {upgradeOptions.candidates.map((format) => <option key={format.format_id} value={format.format_id}>{format.label}{format.file_size_label ? ` · 约 ${format.file_size_label}` : ''}</option>)}
            </select></label>}
            <small className="metadata-edit-help">新版完成后会单独出现在视频管理中；只有你再次确认，旧版才会移入 macOS 废纸篓。</small>
            {upgradeNotice && <p className="notice" role="status">{upgradeNotice}</p>}
            <footer><button className="cancel-button" type="button" onClick={() => setUpgradeTarget(null)} disabled={upgradeWorking}>取消</button><button className="primary-button" type="button" onClick={() => void startVideoUpgrade()} disabled={upgradeWorking || !upgradeFormat}>{upgradeWorking ? '正在处理…' : '下载更高画质'}</button></footer>
          </section>
        </div>
      )}

      {videoToDelete && (
        <div className="modal-backdrop" onMouseDown={() => { if (!deletingVideo) setVideoToDelete(null) }}>
          <section className="delete-modal" role="dialog" aria-modal="true" aria-labelledby="delete-video-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">删除{libraryItemMediaType({ kind: 'video', ...videoToDelete }) === 'audio' ? '音频' : '视频'}</p>
            <h2 id="delete-video-title">从{libraryItemMediaType({ kind: 'video', ...videoToDelete }) === 'audio' ? '音频' : '视频'}管理移除？</h2>
            <p>{removeVideoFile ? `将从${libraryItemMediaType({ kind: 'video', ...videoToDelete }) === 'audio' ? '音频' : '视频'}管理移除“${videoToDelete.title || `未命名${libraryItemMediaType({ kind: 'video', ...videoToDelete }) === 'audio' ? '音频' : '视频'}`}”，并把下列播放文件及关联文件移入 macOS 废纸篓。` : `将只从${libraryItemMediaType({ kind: 'video', ...videoToDelete }) === 'audio' ? '音频' : '视频'}管理移除“${videoToDelete.title || `未命名${libraryItemMediaType({ kind: 'video', ...videoToDelete }) === 'audio' ? '音频' : '视频'}`}”的记录，源文件保持原样。`}</p>
            <label className="delete-file-option"><input type="checkbox" checked={removeVideoFile} onChange={(event) => { const checked = event.target.checked; setRemoveVideoFile(checked); if (checked && deletePreview.length === 0) loadVideoDeletePreview(videoToDelete) }} /><span><b>同时删除播放文件</b><small>勾选后，预览中的文件会移入 macOS 废纸篓。</small></span></label>
            {removeVideoFile && (deletePreviewLoading ? <p className="delete-preview-loading">正在读取待移动文件…</p> : deletePreview.length > 0 && <ol className="delete-preview-list">{deletePreview.map((file) => <li key={file.path}><b>{file.kind}</b><code>{file.path}</code></li>)}</ol>)}
            {deleteNotice && <p className="notice" role="status">{deleteNotice}</p>}
            {deleteReport && <div className="cleanup-report">
              {deleteReport.trashed_files.length > 0 && <section><b>已移入废纸篓</b><ul>{deleteReport.trashed_files.map((path) => <li key={path}>{path}</li>)}</ul></section>}
              {deleteReport.failed_files.length > 0 && <section><b>未能移动</b><ul>{deleteReport.failed_files.map((item) => <li key={item.path}>{item.path}<small>{item.error}</small></li>)}</ul></section>}
            </div>}
            <footer>
              <button className="cancel-button" onClick={() => setVideoToDelete(null)} disabled={deletingVideo}>取消</button>
              <button className="delete-button" onClick={deleteVideo} disabled={deletingVideo || (removeVideoFile && (deletePreviewLoading || deletePreview.length === 0))}>{deletingVideo ? (removeVideoFile ? '正在移入废纸篓…' : '正在删除记录…') : (removeVideoFile ? '移入废纸篓并删除记录' : '仅删除记录')}</button>
            </footer>
          </section>
        </div>
      )}
    </main>
  )
}
