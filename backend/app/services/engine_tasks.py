"""Long-running task coordination for non-yt-dlp engines."""

from __future__ import annotations

import json
import mimetypes
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.config import MEDIA_EXTENSIONS
from ..engines import aria2, qbittorrent

UpdateTask = Callable[..., None]
AppendLog = Callable[[str, str, str], None]
PublishTask = Callable[[str], None]
IsCancelled = Callable[[str], bool]
IsPaused = Callable[[str], bool]
IsInterrupted = Callable[[str], bool]
RegisterProcess = Callable[[str, Any | None], None]
RegisterMediaOutputs = Callable[[str], None]


def run_aria2_task(
    download_id: str,
    source_url: str,
    download_dir: str,
    source_type: str,
    file_name: str | None,
    expected_size: int | None,
    torrent_data: bytes | None,
    selected_file_indexes: list[int] | None,
    update_task: UpdateTask,
    append_log: AppendLog,
    publish_task: PublishTask,
    is_cancelled: IsCancelled,
    register_process: RegisterProcess,
    aria2_settings: dict[str, Any] | None = None,
    register_media_outputs: RegisterMediaOutputs | None = None,
    is_paused: IsPaused | None = None,
    is_interrupted: IsInterrupted | None = None,
) -> None:
    if (is_paused is not None and is_paused(download_id)) or (
        is_interrupted is not None and is_interrupted(download_id)
    ):
        return
    aria2_settings = aria2_settings or {}
    split = int(aria2_settings.get("split", 5))
    max_tries = int(aria2_settings.get("max_tries", 3))
    retry_wait = int(aria2_settings.get("retry_wait", 2))
    bt_stall_timeout = int(aria2_settings.get("bt_stall_timeout", aria2.BT_STALL_TIMEOUT_SECONDS))
    download_rate_limit_kbps = int(aria2_settings.get("download_rate_limit_kbps", 0))
    dht_state_path = str(aria2_settings.get("dht_state_path") or "") or None
    update_task(download_id, status="running", error=None, restart_pending=0)
    task_label = "BT" if source_type in {"magnet", "torrent_url", "torrent_file", "thunder_bt"} else "直链"
    download_root = Path(download_dir).resolve()
    task_output_dir = download_root / "BT" / download_id if task_label == "BT" else download_root
    has_resume_state = (
        task_output_dir.exists() and any(task_output_dir.rglob("*.aria2"))
        if task_label == "BT"
        else bool(file_name and (task_output_dir / f"{file_name}.aria2").is_file())
    )
    append_log(download_id, "info", f"aria2 已启动，正在建立{task_label}断点续传下载。")
    if has_resume_state:
        append_log(download_id, "info", "发现上次保留的未完成数据，正在校验断点并继续下载。")
    elif task_label == "BT":
        append_log(
            download_id,
            "info",
            f"BT 文件清单已就绪，正在连接节点。若 {bt_stall_timeout} 秒内没有获得数据，任务会停止并提示重试。",
        )
    publish_task(download_id)

    started_at = time.monotonic()
    no_data_notice_sent = False

    def should_stop() -> bool:
        return (
            is_cancelled(download_id)
            or (is_paused is not None and is_paused(download_id))
            or (is_interrupted is not None and is_interrupted(download_id))
        )

    def on_progress(downloaded: int, total: int | None, speed: float) -> None:
        nonlocal no_data_notice_sent
        progress = round(downloaded / total * 100, 1) if total else 0
        if task_label == "BT" and downloaded == 0 and not no_data_notice_sent and time.monotonic() - started_at >= 30:
            append_log(download_id, "warning", "仍未收到 BT 数据，正在继续寻找可用节点和文件信息。")
            no_data_notice_sent = True
        update_task(
            download_id,
            status="running",
            progress=progress,
            downloaded_bytes=downloaded,
            total_bytes=total,
            speed=round(speed) if speed else None,
            eta=round((total - downloaded) / speed) if total and speed > 0 and total >= downloaded else None,
        )
        publish_task(download_id)

    try:
        if torrent_data is not None or source_type == "torrent_file" or source_type == "torrent_url" or (source_type == "thunder_bt" and not source_url.startswith("magnet:")):
            torrent_dir = Path(download_dir) / ".video-downloader" / "torrents"
            torrent_dir.mkdir(parents=True, exist_ok=True)
            source_path = torrent_dir / f"{download_id}.torrent"
            if torrent_data is not None:
                source_path.write_bytes(torrent_data)
            elif source_type == "torrent_file":
                raise aria2.Aria2Error("原始种子文件已不可用，请重新添加该 .torrent 文件。")
            else:
                append_log(download_id, "info", "aria2 正在获取 BT 种子文件。")
                aria2.download(
                    source_url,
                    str(torrent_dir),
                    source_path.name,
                    None,
                    should_stop,
                    lambda process: register_process(download_id, process),
                    on_progress,
                    split,
                    max_tries,
                    retry_wait,
                    download_rate_limit_kbps,
                )
            files = aria2.download_bt(
                str(source_path), str(task_output_dir), expected_size, selected_file_indexes, should_stop,
                lambda process: register_process(download_id, process), on_progress, bt_stall_timeout,
                download_rate_limit_kbps, dht_state_path,
            )
        elif source_type in {"magnet", "torrent_url", "thunder_bt"}:
            files = aria2.download_bt(
                source_url, str(task_output_dir), expected_size, selected_file_indexes, should_stop,
                lambda process: register_process(download_id, process), on_progress, bt_stall_timeout,
                download_rate_limit_kbps, dht_state_path,
            )
        else:
            if not file_name:
                raise aria2.Aria2Error("直链缺少输出文件名。")
            files = [aria2.download(
                source_url, download_dir, file_name, expected_size, should_stop,
                lambda process: register_process(download_id, process), on_progress, split, max_tries, retry_wait,
                download_rate_limit_kbps,
            )]
        media_files = [path for path in files if path.suffix.lower() in MEDIA_EXTENSIONS]
        media_path = media_files[0] if len(media_files) == 1 else None
        final_size = sum(path.stat().st_size for path in files)
        library_visible = len(media_files) == 1
        output_files = [
            {
                "relative_path": str(path.resolve().relative_to(download_root)),
                "size": path.stat().st_size,
                "file_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "playable": path.suffix.lower() in MEDIA_EXTENSIONS,
            }
            for path in sorted(files, key=lambda item: str(item).lower())
        ]
        update_task(
            download_id,
            status="completed",
            progress=100,
            file_path=str(media_path) if media_path else None,
            file_size=final_size,
            downloaded_bytes=final_size,
            total_bytes=final_size,
            speed=None,
            eta=None,
            output_files_json=json.dumps(output_files, ensure_ascii=False),
            library_visible=library_visible,
            error=None,
            restart_pending=0,
        )
        if register_media_outputs is not None and media_files:
            try:
                register_media_outputs(download_id)
            except Exception as error:
                append_log(download_id, "warning", f"媒体文件已下载，但加入媒体库时失败：{error}")
        append_log(
            download_id,
            "info",
            f"{task_label}下载完成。"
            + ("媒体已加入本地媒体库。" if library_visible else f"已整理 {len(media_files)} 个媒体文件。"),
        )
    except aria2.Aria2Error as error:
        if is_paused is not None and is_paused(download_id):
            update_task(download_id, status="paused", error=None, speed=None, eta=None, restart_pending=0)
            append_log(download_id, "info", f"{task_label}下载已暂停，已下载部分会保留。")
        elif is_interrupted is not None and is_interrupted(download_id):
            update_task(download_id, status="interrupted", speed=None, eta=None, restart_pending=1)
            append_log(download_id, "warning", f"{task_label}下载因应用退出而中断，将在下次启动时恢复。")
        elif is_cancelled(download_id):
            update_task(download_id, status="cancelled", error="已由用户取消下载。", speed=None, eta=None, restart_pending=0)
            append_log(download_id, "info", f"{task_label}下载已取消，已下载部分会保留。")
        else:
            update_task(download_id, status="failed", error=str(error), speed=None, eta=None, restart_pending=0)
            append_log(download_id, "error", f"aria2 {task_label}下载失败：{error}")
    except Exception as error:
        update_task(download_id, status="failed", error=str(error), speed=None, eta=None, restart_pending=0)
        append_log(download_id, "error", f"aria2 {task_label}下载异常：{error}")
    finally:
        register_process(download_id, None)
        publish_task(download_id)


def run_qbittorrent_task(
    download_id: str,
    source_url: str,
    download_dir: str,
    torrent_data: bytes | None,
    selected_file_indexes: list[int] | None,
    engine_task_id: str | None,
    qbittorrent_settings: dict[str, Any],
    update_task: UpdateTask,
    append_log: AppendLog,
    publish_task: PublishTask,
    is_cancelled: IsCancelled,
    register_media_outputs: RegisterMediaOutputs | None = None,
    is_paused: IsPaused | None = None,
    is_interrupted: IsInterrupted | None = None,
) -> None:
    if (is_paused is not None and is_paused(download_id)) or (
        is_interrupted is not None and is_interrupted(download_id)
    ):
        return

    client = qbittorrent.Client(
        str(qbittorrent_settings.get("base_url", "")),
        str(qbittorrent_settings.get("username", "")),
        str(qbittorrent_settings.get("password", "")),
    )
    stall_timeout = int(qbittorrent_settings.get("bt_stall_timeout", 90))
    download_root = Path(download_dir).resolve()
    task_output_dir = download_root / "BT" / download_id
    task_output_dir.mkdir(parents=True, exist_ok=True)
    tag = f"video-downloader-{download_id}"
    torrent_hash = engine_task_id

    def should_stop() -> bool:
        return (
            is_cancelled(download_id)
            or (is_paused is not None and is_paused(download_id))
            or (is_interrupted is not None and is_interrupted(download_id))
        )

    try:
        version = client.app_version()
        append_log(download_id, "info", f"qBittorrent {version} 已连接，正在准备 BT 任务。")
        update_task(download_id, status="running", error=None, restart_pending=0)
        publish_task(download_id)

        # The add request may succeed immediately before the app can persist the
        # returned hash. The per-download tag lets retry/restart adopt that task
        # instead of submitting a duplicate torrent.
        tasks = client.info(hashes=torrent_hash) if torrent_hash else client.info(tag=tag)
        if not tasks:
            added_hashes = client.add(
                source_url or None,
                torrent_data,
                str(task_output_dir),
                tag,
                int(qbittorrent_settings.get("download_rate_limit_kbps", 0)),
            )
            if added_hashes:
                tasks = client.info(hashes="|".join(added_hashes))
            lookup_deadline = time.monotonic() + 30
            while not tasks and time.monotonic() < lookup_deadline:
                if should_stop():
                    raise qbittorrent.QbittorrentError("下载已停止")
                tasks = client.info(tag=tag)
                if tasks:
                    break
                time.sleep(0.25)
            if not tasks:
                raise qbittorrent.QbittorrentError("qBittorrent 已接收任务，但未返回任务标识。")
        torrent_hash = str(tasks[0].get("hash") or "")
        if not torrent_hash:
            raise qbittorrent.QbittorrentError("qBittorrent 返回的任务缺少哈希标识。")
        update_task(download_id, engine_task_id=torrent_hash)

        files = client.files(torrent_hash)
        if not files:
            client.resume(torrent_hash)
            metadata_deadline = time.monotonic() + stall_timeout
            while time.monotonic() < metadata_deadline:
                if should_stop():
                    raise qbittorrent.QbittorrentError("下载已停止")
                files = client.files(torrent_hash)
                if files:
                    client.pause(torrent_hash)
                    break
                time.sleep(0.5)
            if not files:
                raise qbittorrent.QbittorrentError(
                    f"qBittorrent 在 {stall_timeout} 秒内没有取得 BT 文件列表。"
                )

        indexed_files = [(int(item.get("index", index)), item) for index, item in enumerate(files)]
        available_indexes = {index for index, _item in indexed_files}
        selected_indexes = set(selected_file_indexes or available_indexes)
        if not selected_indexes or not selected_indexes.issubset(available_indexes):
            raise qbittorrent.QbittorrentError("已选择的 BT 文件与 qBittorrent 返回的文件列表不一致。")
        skipped_indexes = sorted(available_indexes - selected_indexes)
        if skipped_indexes:
            client.file_priority(torrent_hash, skipped_indexes, 0)
        client.file_priority(torrent_hash, sorted(selected_indexes), 1)
        client.resume(torrent_hash)
        append_log(download_id, "info", f"qBittorrent 已开始下载 {len(selected_indexes)} 个已选文件。")

        previous_downloaded = -1
        last_progress_at = time.monotonic()
        while True:
            if should_stop():
                client.pause(torrent_hash)
                raise qbittorrent.QbittorrentError("下载已停止")
            tasks = client.info(hashes=torrent_hash)
            if not tasks:
                raise qbittorrent.QbittorrentError("qBittorrent 中的任务已被移除。")
            task = tasks[0]
            state = str(task.get("state") or "")
            if state in {"error", "missingFiles", "unknown"}:
                raise qbittorrent.QbittorrentError(f"qBittorrent 任务状态异常：{state}。")
            total = int(task.get("total_size") or 0)
            downloaded = int(task.get("completed") or 0)
            speed = int(task.get("dlspeed") or 0)
            progress = max(0.0, min(100.0, float(task.get("progress") or 0) * 100))
            eta_value = int(task.get("eta") or 0)
            eta = eta_value if 0 < eta_value < 8_640_000 else None
            update_task(
                download_id,
                status="running",
                progress=round(progress, 1),
                downloaded_bytes=downloaded,
                total_bytes=total or None,
                speed=speed or None,
                eta=eta,
            )
            publish_task(download_id)
            if progress >= 100 or (total > 0 and int(task.get("amount_left") or 0) == 0):
                break
            if downloaded > previous_downloaded:
                previous_downloaded = downloaded
                last_progress_at = time.monotonic()
            elif time.monotonic() - last_progress_at >= stall_timeout:
                raise qbittorrent.QbittorrentError(
                    f"qBittorrent 在 {stall_timeout} 秒内没有获得新数据，请检查资源节点或网络。"
                )
            time.sleep(0.5)

        client.pause(torrent_hash)
        files = client.files(torrent_hash)
        output_paths: list[Path] = []
        for index, item in [(int(value.get("index", offset)), value) for offset, value in enumerate(files)]:
            if index not in selected_indexes:
                continue
            name = str(item.get("name") or "")
            path = (task_output_dir / name).resolve()
            try:
                path.relative_to(task_output_dir.resolve())
            except ValueError as error:
                raise qbittorrent.QbittorrentError("qBittorrent 返回了不安全的输出路径。") from error
            if not path.is_file():
                raise qbittorrent.QbittorrentError(
                    "qBittorrent 已完成任务，但应用无法访问输出文件；请确认双方使用相同的保存路径。"
                )
            output_paths.append(path)
        if not output_paths:
            raise qbittorrent.QbittorrentError("qBittorrent 已完成任务，但没有找到已选择的输出文件。")

        media_files = [path for path in output_paths if path.suffix.lower() in MEDIA_EXTENSIONS]
        media_path = media_files[0] if len(media_files) == 1 else None
        final_size = sum(path.stat().st_size for path in output_paths)
        output_files = [
            {
                "relative_path": str(path.relative_to(download_root)),
                "size": path.stat().st_size,
                "file_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "playable": path.suffix.lower() in MEDIA_EXTENSIONS,
            }
            for path in sorted(output_paths, key=lambda item: str(item).lower())
        ]
        update_task(
            download_id,
            status="completed",
            progress=100,
            file_path=str(media_path) if media_path else None,
            file_size=final_size,
            downloaded_bytes=final_size,
            total_bytes=final_size,
            speed=None,
            eta=None,
            output_files_json=json.dumps(output_files, ensure_ascii=False),
            library_visible=len(media_files) == 1,
            error=None,
            restart_pending=0,
        )
        if register_media_outputs is not None and media_files:
            try:
                register_media_outputs(download_id)
            except Exception as error:
                append_log(download_id, "warning", f"媒体文件已下载，但补充媒体信息时失败：{error}")
        client.delete(torrent_hash, delete_files=False)
        append_log(download_id, "info", "qBittorrent BT 下载完成，输出文件已加入本地媒体库。")
    except qbittorrent.QbittorrentError as error:
        if torrent_hash:
            try:
                client.pause(torrent_hash)
            except qbittorrent.QbittorrentError:
                pass
        if is_paused is not None and is_paused(download_id):
            update_task(download_id, status="paused", error=None, speed=None, eta=None, restart_pending=0)
            append_log(download_id, "info", "qBittorrent BT 下载已暂停，已下载部分会保留。")
        elif is_interrupted is not None and is_interrupted(download_id):
            update_task(download_id, status="interrupted", speed=None, eta=None, restart_pending=1)
            append_log(download_id, "warning", "qBittorrent BT 下载因应用退出而中断，将在下次启动时恢复。")
        elif is_cancelled(download_id):
            update_task(download_id, status="cancelled", error="已由用户取消下载。", speed=None, eta=None, restart_pending=0)
            append_log(download_id, "info", "qBittorrent BT 下载已取消，已下载部分会保留。")
        else:
            update_task(download_id, status="failed", error=str(error), speed=None, eta=None, restart_pending=0)
            append_log(download_id, "error", f"qBittorrent BT 下载失败：{error}")
    except Exception as error:
        update_task(download_id, status="failed", error=str(error), speed=None, eta=None, restart_pending=0)
        append_log(download_id, "error", f"qBittorrent BT 下载异常：{error}")
    finally:
        publish_task(download_id)
