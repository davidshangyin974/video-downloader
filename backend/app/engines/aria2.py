"""aria2c adapter for ordinary HTTP, HTTPS and FTP file downloads."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


class Aria2Error(RuntimeError):
    pass


# A BT task with no metadata or peer traffic should surface a recovery path
# instead of remaining at 0% indefinitely.
BT_STALL_TIMEOUT_SECONDS = 90
BT_DHT_BOOTSTRAP = "dht.transmissionbt.com:6881"
_DHT_STATE_LOCK = threading.Lock()
_DHT_METADATA_LOCK = threading.Lock()
_DHT_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)

ARIA2_EXIT_MESSAGES = {
    2: "连接超时",
    3: "远程资源不存在",
    5: "下载速度长期低于最低限制",
    6: "网络连接失败",
    9: "磁盘可用空间不足",
    13: "目标文件已存在且无法覆盖",
    14: "无法重命名下载文件",
    15: "无法打开已有文件",
    16: "无法创建新文件",
    17: "文件系统读写失败",
    18: "无法创建下载目录",
    19: "域名解析失败",
    23: "远程地址发生了过多重定向",
    24: "远程服务器需要登录或鉴权",
    25: "种子文件格式无效",
    27: "磁力链接格式无效",
    32: "下载内容校验失败",
}


def failure_message(returncode: int | None, *, bt: bool = False, stall_timeout: int = BT_STALL_TIMEOUT_SECONDS) -> str:
    code = int(returncode or 1)
    detail = ARIA2_EXIT_MESSAGES.get(code)
    if detail:
        return f"aria2 下载失败（错误码 {code}）：{detail}。"
    if bt:
        return (
            f"BT 任务在 {stall_timeout} 秒内没有获得数据。"
            "资源可能已没有可用节点，或当前网络限制了 DHT/BT 连接；可稍后重试。"
        )
    return f"aria2 未能下载该文件（错误码 {code}）。请确认链接仍有效、网络可用且不需要登录。"


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


def resolve_dht_bootstrap(entry_point: str = BT_DHT_BOOTSTRAP) -> tuple[str, int, tuple[str, ...]]:
    """Resolve the DHT bootstrap before starting a long metadata wait."""
    try:
        host, raw_port = entry_point.rsplit(":", 1)
        port = int(raw_port)
        answers = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
    except (OSError, ValueError) as error:
        raise Aria2Error(f"无法解析 DHT 引导地址 {entry_point}，请检查 DNS 或代理设置。") from error
    addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers if answer[4]))
    if not addresses:
        raise Aria2Error(f"DHT 引导地址 {entry_point} 没有返回 IPv4 地址。")
    fake_addresses = [
        address
        for address in addresses
        if any(ipaddress.ip_address(address) in network for network in _DHT_FAKE_IP_NETWORKS)
    ]
    if fake_addresses:
        raise Aria2Error(
            "检测到 DHT 引导地址被系统代理映射为 Fake-IP "
            f"（{', '.join(fake_addresses)}）。请让 aria2/DHT 流量直连，或将 "
            f"{host} 加入代理的 Fake-IP 排除列表后重试。"
        )
    return host, port, addresses


def magnet_metadata_failure(log_output: str, bt_stall_timeout: int) -> str:
    normalized = log_output.lower()
    if "malformed dht message" in normalized or "missing token" in normalized:
        return (
            "DHT 收到了代理或中间网络返回的无效响应，无法取得磁力元数据。"
            "请让 aria2/DHT 流量直连后重试。"
        )
    if "failed to bind udp port" in normalized or "errors occurred while binding port" in normalized:
        return "aria2 无法监听 DHT 的 UDP 端口。请检查系统网络权限、防火墙或代理 TUN 设置。"
    if "name resolution" in normalized or "failed to resolve" in normalized:
        return "DHT 引导地址解析失败。请检查 DNS 或代理设置后重试。"
    return (
        f"磁力链接在 {bt_stall_timeout} 秒内没有返回文件列表。"
        "资源可能已没有可用节点，或当前网络限制了 DHT/BT 连接。"
    )


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
            raise Aria2Error(failure_message(process.returncode))
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


def _prepare_dht_state(shared_path: str | None, work_dir: Path) -> Path | None:
    if not shared_path:
        return None
    shared = Path(shared_path)
    task_state = work_dir / ".video-downloader" / "aria2-dht.dat"
    try:
        task_state.parent.mkdir(parents=True, exist_ok=True)
        if shared.resolve() != task_state.resolve() and shared.is_file() and not task_state.exists():
            with _DHT_STATE_LOCK:
                if shared.is_file() and not task_state.exists():
                    shutil.copyfile(shared, task_state)
    except OSError:
        return None
    return task_state


def _promote_dht_state(task_state: Path | None, shared_path: str | None) -> None:
    if task_state is None or not shared_path or not task_state.is_file():
        return
    shared = Path(shared_path)
    temporary: Path | None = None
    try:
        if shared.resolve() == task_state.resolve():
            return
        shared.parent.mkdir(parents=True, exist_ok=True)
        temporary = shared.with_name(f".{shared.name}.{uuid.uuid4().hex}.tmp")
        with _DHT_STATE_LOCK:
            shutil.copyfile(task_state, temporary)
            temporary.replace(shared)
    except OSError:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass


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
    dht_state_path: str | None = None,
) -> list[Path]:
    binary = executable()
    if not binary:
        raise Aria2Error("未找到 aria2c。请安装 aria2 后再开始 BT 下载。")
    target_dir = Path(download_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    task_dht_state = _prepare_dht_state(dht_state_path, target_dir)
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
    if task_dht_state is not None:
        command.append(f"--dht-file-path={task_dht_state}")
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
            raise Aria2Error(failure_message(process.returncode, bt=True, stall_timeout=bt_stall_timeout))
        files = _managed_files(target_dir)
        if not files:
            raise Aria2Error("aria2 已结束，但未找到 BT 下载的输出文件。")
        total_size = sum(path.stat().st_size for path in files)
        on_progress(total_size, total_size, 0)
        return files
    finally:
        _promote_dht_state(task_dht_state, dht_state_path)
        on_process(None)


def fetch_magnet_metadata(
    magnet_url: str,
    bt_stall_timeout: int = BT_STALL_TIMEOUT_SECONDS,
    dht_state_path: str | None = None,
) -> bytes:
    """Resolve a magnet link into its torrent metadata without downloading files."""
    binary = executable()
    if not binary:
        raise Aria2Error("未找到 aria2c，无法读取磁力链接中的文件列表。")
    _bootstrap_host, bootstrap_port, bootstrap_addresses = resolve_dht_bootstrap()
    bootstrap_address = bootstrap_addresses[0]

    # Serialize metadata discovery so each attempt starts from the newest DHT
    # routing table instead of allowing concurrent temporary processes to
    # overwrite one another's learned nodes.
    with _DHT_METADATA_LOCK:
        with tempfile.TemporaryDirectory(prefix="video-downloader-metadata-") as temp_dir:
            task_dht_state = _prepare_dht_state(dht_state_path, Path(temp_dir))
            command = [
                binary,
                "--console-log-level=warn",
                "--summary-interval=1",
                "--download-result=hide",
                "--file-allocation=none",
                "--seed-time=0",
                "--enable-dht=true",
                "--bt-enable-lpd=true",
                "--enable-peer-exchange=true",
                f"--dht-entry-point={bootstrap_address}:{bootstrap_port}",
                f"--bt-stop-timeout={bt_stall_timeout}",
                "--bt-metadata-only=true",
                "--bt-save-metadata=true",
                "--dir",
                temp_dir,
                magnet_url,
            ]
            if task_dht_state is not None:
                command.insert(-1, f"--dht-file-path={task_dht_state}")
            try:
                try:
                    result = subprocess.run(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=bt_stall_timeout + 15,
                        check=False,
                    )
                except subprocess.TimeoutExpired as error:
                    captured = error.stderr if isinstance(error.stderr, str) else ""
                    raise Aria2Error(magnet_metadata_failure(captured, bt_stall_timeout)) from error

                metadata_files = list(Path(temp_dir).glob("*.torrent"))
                if metadata_files:
                    return metadata_files[0].read_bytes()
                captured = result.stderr if isinstance(result.stderr, str) else ""
                if result.returncode != 0:
                    detailed_message = magnet_metadata_failure(captured, bt_stall_timeout)
                    if captured or int(result.returncode or 1) not in ARIA2_EXIT_MESSAGES:
                        raise Aria2Error(detailed_message)
                    detail = ARIA2_EXIT_MESSAGES[int(result.returncode)]
                    raise Aria2Error(f"磁力链接解析失败（aria2 错误码 {result.returncode}）：{detail}。")
                if captured:
                    raise Aria2Error(magnet_metadata_failure(captured, bt_stall_timeout))
                raise Aria2Error("已结束磁力链接解析，但没有得到有效的文件列表。")
            finally:
                _promote_dht_state(task_dht_state, dht_state_path)
