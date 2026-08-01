"""aria2c adapter for ordinary HTTP, HTTPS and FTP file downloads."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable


class Aria2Error(RuntimeError):
    pass


# A BT task with no metadata or peer traffic should surface a recovery path
# instead of remaining at 0% indefinitely.
BT_STALL_TIMEOUT_SECONDS = 90
BT_DHT_BOOTSTRAP = "dht.transmissionbt.com:6881"


def executable() -> str | None:
    return shutil.which("aria2c")


def version() -> str | None:
    binary = executable()
    if not binary:
        return None
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    return first_line.removeprefix("aria2 version ").strip() or None


def download(
    url: str,
    download_dir: str,
    file_name: str,
    expected_size: int | None,
    cancelled: Callable[[], bool],
    on_process: Callable[[subprocess.Popen[bytes] | None], None],
    on_progress: Callable[[int, int | None, float], None],
    split: int = 5,
    max_tries: int = 3,
    retry_wait: int = 2,
    download_rate_limit_kbps: int = 0,
) -> Path:
    binary = executable()
    if not binary:
        raise Aria2Error("未找到 aria2c。请安装 aria2 后再开始直链或迅雷链接下载。")
    target_dir = Path(download_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_file_name = Path(file_name).name
    if not safe_file_name or safe_file_name != file_name or safe_file_name in {".", ".."}:
        raise Aria2Error("直链返回了不安全的文件名，无法保存。")
    target = target_dir / safe_file_name
    command = [
        binary,
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--continue=true",
        "--console-log-level=warn",
        "--summary-interval=1",
        "--download-result=hide",
        "--file-allocation=none",
        "--follow-torrent=false",
        f"--split={split}",
        f"--max-connection-per-server={split}",
        "--min-split-size=1M",
        f"--max-tries={max_tries}",
        f"--retry-wait={retry_wait}",
        "--dir",
        str(target_dir),
        "--out",
        safe_file_name,
        url,
    ]
    if download_rate_limit_kbps > 0:
        command.append(f"--max-download-limit={download_rate_limit_kbps}K")
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        raise Aria2Error(f"无法启动 aria2：{error}") from error
    on_process(process)
    previous_size = 0
    previous_time = time.monotonic()
    try:
        while process.poll() is None:
            if cancelled():
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise Aria2Error("下载已取消")
            current_size = target.stat().st_size if target.is_file() else 0
            current_time = time.monotonic()
            elapsed = max(current_time - previous_time, 0.001)
            speed = (current_size - previous_size) / elapsed if current_size >= previous_size else 0
            on_progress(current_size, expected_size, speed)
            previous_size = current_size
            previous_time = current_time
            time.sleep(0.5)
        if process.returncode != 0:
            raise Aria2Error("aria2 未能下载该文件。请确认链接仍有效、网络可用且不需要登录。")
        if not target.is_file():
            raise Aria2Error("aria2 已结束，但未找到输出文件。")
        on_progress(target.stat().st_size, target.stat().st_size, 0)
        return target
    finally:
        on_process(None)


def _managed_files(download_dir: Path) -> list[Path]:
    return [
        path for path in download_dir.rglob("*")
        if path.is_file() and path.suffix != ".aria2" and ".video-downloader" not in path.parts
    ]


def download_bt(
    source: str,
    download_dir: str,
    expected_size: int | None,
    selected_file_indexes: list[int] | None,
    cancelled: Callable[[], bool],
    on_process: Callable[[subprocess.Popen[bytes] | None], None],
    on_progress: Callable[[int, int | None, float], None],
    bt_stall_timeout: int = BT_STALL_TIMEOUT_SECONDS,
    download_rate_limit_kbps: int = 0,
) -> list[Path]:
    binary = executable()
    if not binary:
        raise Aria2Error("未找到 aria2c。请安装 aria2 后再开始 BT 下载。")
    target_dir = Path(download_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    command = [
        binary,
        "--continue=true",
        "--console-log-level=warn",
        "--summary-interval=1",
        "--download-result=hide",
        "--file-allocation=none",
        "--seed-time=0",
        "--bt-enable-lpd=true",
        "--enable-dht=true",
        f"--dht-entry-point={BT_DHT_BOOTSTRAP}",
        f"--bt-stop-timeout={bt_stall_timeout}",
        "--dir",
        str(target_dir),
    ]
    if download_rate_limit_kbps > 0:
        command.append(f"--max-download-limit={download_rate_limit_kbps}K")
    if selected_file_indexes:
        # aria2 uses one-based file positions while the API and UI use zero-based indexes.
        command.append("--select-file=" + ",".join(str(index + 1) for index in selected_file_indexes))
    command.append(source)
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        raise Aria2Error(f"无法启动 aria2：{error}") from error
    on_process(process)
    previous_size = 0
    previous_time = time.monotonic()
    try:
        while process.poll() is None:
            if cancelled():
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise Aria2Error("下载已取消")
            files = _managed_files(target_dir)
            current_size = sum(path.stat().st_size for path in files)
            current_time = time.monotonic()
            elapsed = max(current_time - previous_time, 0.001)
            speed = (current_size - previous_size) / elapsed if current_size >= previous_size else 0
            on_progress(current_size, expected_size, speed)
            previous_size = current_size
            previous_time = current_time
            time.sleep(0.5)
        if process.returncode != 0:
            raise Aria2Error(
                f"BT 任务在 {bt_stall_timeout} 秒内没有获得数据。"
                "资源可能已没有可用节点，或当前网络限制了 DHT/BT 连接；可稍后重试。"
            )
        files = _managed_files(target_dir)
        if not files:
            raise Aria2Error("aria2 已结束，但未找到 BT 下载的输出文件。")
        total_size = sum(path.stat().st_size for path in files)
        on_progress(total_size, total_size, 0)
        return files
    finally:
        on_process(None)


def fetch_magnet_metadata(
    magnet_url: str,
    bt_stall_timeout: int = BT_STALL_TIMEOUT_SECONDS,
) -> bytes:
    """Resolve a magnet link into its torrent metadata without downloading files."""
    binary = executable()
    if not binary:
        raise Aria2Error("未找到 aria2c，无法读取磁力链接中的文件列表。")

    with tempfile.TemporaryDirectory(prefix="video-downloader-metadata-") as temp_dir:
        command = [
            binary,
            "--console-log-level=warn",
            "--summary-interval=1",
            "--download-result=hide",
            "--file-allocation=none",
            "--seed-time=0",
            "--enable-dht=true",
            f"--dht-entry-point={BT_DHT_BOOTSTRAP}",
            f"--bt-stop-timeout={bt_stall_timeout}",
            "--bt-metadata-only=true",
            "--bt-save-metadata=true",
            "--dir",
            temp_dir,
            magnet_url,
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=bt_stall_timeout + 15,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise Aria2Error(
                f"磁力链接在 {bt_stall_timeout} 秒内没有返回文件列表。"
                "资源可能已没有可用节点，或当前网络限制了 DHT/BT 连接。"
            ) from error

        metadata_files = list(Path(temp_dir).glob("*.torrent"))
        if metadata_files:
            return metadata_files[0].read_bytes()
        if result.returncode != 0:
            raise Aria2Error(
                f"磁力链接在 {bt_stall_timeout} 秒内没有返回文件列表。"
                "资源可能已没有可用节点，或当前网络限制了 DHT/BT 连接。"
            )
        raise Aria2Error("已结束磁力链接解析，但没有得到有效的文件列表。")
