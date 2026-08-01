"""Input recognition shared by the download API and engine adapters."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from ..core.config import DIRECT_FILE_EXTENSIONS, MEDIA_EXTENSIONS


class InputError(ValueError):
    """A user-facing unsupported or malformed download input."""


def decode_thunder_url(url: str) -> str:
    payload = url.strip()[len("thunder://") :]
    if not payload:
        raise InputError("迅雷链接内容为空。")
    padded_payload = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.b64decode(padded_payload, validate=False).decode("utf-8")
    except (UnicodeDecodeError, binascii.Error, ValueError) as error:
        raise InputError("迅雷链接无法解码，请确认链接完整。") from error
    if decoded.startswith("AA") and decoded.endswith("ZZ"):
        decoded = decoded[2:-2]
    try:
        parsed = urlparse(decoded)
    except ValueError as error:
        raise InputError("迅雷链接中的资源地址无效。") from error
    if parsed.scheme not in {"http", "https", "ftp", "magnet", "ed2k"}:
        raise InputError("迅雷链接中的资源类型暂不支持。")
    return decoded


def is_torrent_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".torrent")


def is_direct_file_url(url: str) -> bool:
    return PurePosixPath(unquote(urlparse(url).path)).suffix.lower() in DIRECT_FILE_EXTENSIONS


def route_input(url: str, engine_hint: str = "auto", has_torrent_file: bool = False) -> dict[str, str]:
    hint = engine_hint.strip().lower() or "auto"
    if hint not in {"auto", "yt-dlp", "aria2", "qbittorrent"}:
        raise InputError("请选择自动识别、网页媒体、aria2 或 qBittorrent 下载。")
    if has_torrent_file:
        if hint not in {"auto", "aria2", "qbittorrent"}:
            raise InputError("种子文件只能交给 aria2 或 qBittorrent 下载。")
        return {"engine": "qbittorrent" if hint == "qbittorrent" else "aria2", "source_type": "torrent_file", "resolved_url": ""}

    value = url.strip()
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise InputError("请输入有效的网页链接、直链、磁力链接或迅雷链接。") from error
    if not parsed.scheme:
        raise InputError("请输入网页链接、直链、磁力链接或迅雷链接。")
    if parsed.scheme == "thunder":
        resolved_url = decode_thunder_url(value)
        target = route_input(resolved_url, hint)
        # Keep the original 迅雷 input visible to the user while preserving that
        # its decoded target is a BT task rather than a normal direct download.
        source_type = "thunder_bt" if target["source_type"] in {"magnet", "torrent_url"} else "thunder"
        return {**target, "source_type": source_type, "resolved_url": resolved_url}
    if parsed.scheme == "magnet":
        if hint not in {"auto", "aria2", "qbittorrent"}:
            raise InputError("磁力链接需要使用 aria2 或 qBittorrent 下载。")
        return {"engine": "qbittorrent" if hint == "qbittorrent" else "aria2", "source_type": "magnet", "resolved_url": value}
    if parsed.scheme == "ed2k":
        raise InputError("这是 eD2K 资源。当前只支持网页、直链和 BT，不支持 eD2K。")
    if parsed.scheme not in {"http", "https", "ftp"} or not parsed.netloc:
        raise InputError("请输入有效的网页链接、直链、磁力链接或迅雷链接。")
    if is_torrent_url(value):
        if hint not in {"auto", "aria2", "qbittorrent"}:
            raise InputError("种子地址只能交给 aria2 或 qBittorrent 下载。")
        return {"engine": "qbittorrent" if hint == "qbittorrent" else "aria2", "source_type": "torrent_url", "resolved_url": value}
    if hint == "qbittorrent":
        raise InputError("qBittorrent 只用于磁力链接和种子文件。")
    if hint == "aria2":
        return {"engine": "aria2", "source_type": "direct", "resolved_url": value}
    if hint == "yt-dlp":
        return {"engine": "yt-dlp", "source_type": "webpage", "resolved_url": value}
    if is_direct_file_url(value):
        return {"engine": "aria2", "source_type": "direct", "resolved_url": value}
    return {"engine": "yt-dlp", "source_type": "webpage", "resolved_url": value}


def direct_file_name(url: str) -> str:
    name = PurePosixPath(unquote(urlparse(url).path)).name
    return name or "未命名文件"


def is_media_file(file_name: str, content_type: str | None = None) -> bool:
    extension = PurePosixPath(file_name).suffix.lower()
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    return extension in MEDIA_EXTENSIONS or normalized_content_type.startswith(("video/", "audio/"))


def direct_file_probe(url: str) -> dict[str, Any]:
    request = Request(url, method="HEAD", headers={"User-Agent": "Video Downloader/0.1"})
    try:
        with urlopen(request, timeout=8) as response:
            content_length = response.headers.get("Content-Length")
            disposition = response.headers.get("Content-Disposition", "")
            file_name_match = re.search(r"filename\*?=(?:UTF-8''|\")?([^;\"]+)", disposition, re.IGNORECASE)
            return {
                "file_name": unquote(file_name_match.group(1)).strip() if file_name_match else direct_file_name(url),
                "file_size": int(content_length) if content_length and content_length.isdigit() else None,
                "content_type": response.headers.get_content_type() or None,
            }
    except (HTTPError, URLError, TimeoutError, ValueError):
        return {"file_name": direct_file_name(url), "file_size": None, "content_type": None}


def _parse_bencode(data: bytes, offset: int = 0) -> tuple[Any, int]:
    if offset >= len(data):
        raise InputError("种子文件内容不完整。")
    marker = data[offset : offset + 1]
    if marker == b"i":
        end = data.find(b"e", offset)
        if end < 0:
            raise InputError("种子文件中的整数格式无效。")
        try:
            return int(data[offset + 1 : end]), end + 1
        except ValueError as error:
            raise InputError("种子文件中的整数格式无效。") from error
    if marker == b"l":
        values: list[Any] = []
        offset += 1
        while data[offset : offset + 1] != b"e":
            value, offset = _parse_bencode(data, offset)
            values.append(value)
        return values, offset + 1
    if marker == b"d":
        values: dict[bytes, Any] = {}
        offset += 1
        while data[offset : offset + 1] != b"e":
            key, offset = _parse_bencode(data, offset)
            if not isinstance(key, bytes):
                raise InputError("种子文件字典键无效。")
            value, offset = _parse_bencode(data, offset)
            values[key] = value
        return values, offset + 1
    if marker.isdigit():
        colon = data.find(b":", offset)
        if colon < 0:
            raise InputError("种子文件字符串格式无效。")
        try:
            length = int(data[offset:colon])
        except ValueError as error:
            raise InputError("种子文件字符串格式无效。") from error
        start = colon + 1
        end = start + length
        if end > len(data):
            raise InputError("种子文件内容不完整。")
        return data[start:end], end
    raise InputError("这不是有效的 .torrent 文件。")


def _decode_torrent_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def inspect_torrent(data: bytes, original_name: str | None) -> dict[str, Any]:
    # Some hand-created or exported torrent files end with a text line break.
    # It is harmless outside the bencoded payload and should not hide the file list.
    payload = data.rstrip(b"\r\n")
    try:
        torrent, end = _parse_bencode(payload)
    except RecursionError as error:
        raise InputError("种子文件层级过深，无法读取。") from error
    if end != len(payload) or not isinstance(torrent, dict) or not isinstance(torrent.get(b"info"), dict):
        raise InputError("这不是有效的 .torrent 文件。")
    info = torrent[b"info"]
    title = _decode_torrent_value(info.get(b"name")) or original_name or "未命名种子"
    files: list[dict[str, Any]] = []
    if isinstance(info.get(b"files"), list):
        for index, item in enumerate(info[b"files"]):
            if not isinstance(item, dict):
                continue
            path_values = item.get(b"path")
            if not isinstance(path_values, list):
                continue
            path = "/".join(_decode_torrent_value(part) for part in path_values)
            files.append({"index": index, "path": path, "size": item.get(b"length") if isinstance(item.get(b"length"), int) else None})
    else:
        files.append({"index": 0, "path": title, "size": info.get(b"length") if isinstance(info.get(b"length"), int) else None})
    return {
        "kind": "torrent",
        "title": title,
        "engine": "aria2",
        "source_type": "torrent_file",
        "file_count": len(files),
        "file_size": sum(item["size"] or 0 for item in files) or None,
        "files": files[:100],
    }


def inspect_non_ytdlp_input(route: dict[str, str], original_url: str, torrent_data: bytes | None = None, torrent_name: str | None = None) -> dict[str, Any]:
    if route["source_type"] == "torrent_file" or torrent_data is not None:
        if torrent_data is None:
            raise InputError("请重新选择 .torrent 文件。")
        inspected = inspect_torrent(torrent_data, torrent_name)
        inspected["engine"] = route["engine"]
        inspected["source_type"] = route["source_type"]
        inspected["message"] = "已读取文件列表。默认全选；你可以只保留需要下载的文件。"
        return inspected
    resolved_url = route["resolved_url"]
    if route["source_type"] in {"direct", "thunder"}:
        probe = direct_file_probe(resolved_url)
        if not is_media_file(probe["file_name"], probe["content_type"]):
            raise InputError("只支持下载视频和音频文件，其他文件类型不允许下载。")
        return {
            "kind": "file",
            "title": probe["file_name"],
            "file_name": probe["file_name"],
            "file_size": probe["file_size"],
            "content_type": probe["content_type"],
            "engine": route["engine"],
            "source_type": route["source_type"],
            "resolved_host": urlparse(resolved_url).hostname,
            "message": f"将由 {route['engine']} 下载，支持断点续传。",
        }
    parsed = urlparse(resolved_url)
    title = parse_qs(parsed.query).get("dn", [None])[0] or "BT 下载任务"
    return {
        "kind": "torrent",
        "title": title,
        "engine": route["engine"],
        "source_type": route["source_type"],
        "file_count": None,
        "file_size": None,
        "files": [],
        "message": f"将由 {route['engine']} 获取 BT 元数据并开始下载。",
    }
