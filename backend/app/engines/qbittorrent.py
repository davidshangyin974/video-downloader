"""Small qBittorrent Web API adapter used by the optional BT engine."""

from __future__ import annotations

import json
import secrets
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


class QbittorrentError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 8) -> None:
        parsed = urlparse(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise QbittorrentError("qBittorrent 地址必须是完整的 http 或 https 地址。")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        self.username = username
        self.password = password
        self.timeout = timeout
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._logged_in = False

    def _request(self, path: str, data: bytes | None = None, content_type: str | None = None) -> bytes:
        headers = {"User-Agent": "Video Downloader/0.1", "Referer": f"{self.base_url}/"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise QbittorrentError("qBittorrent 拒绝了连接，请检查 Web UI 用户名、密码和访问权限。") from error
            raise QbittorrentError(f"qBittorrent 返回 HTTP {error.code}。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise QbittorrentError("无法连接 qBittorrent。请确认 Web UI 已启用且地址正确。") from error

    def login(self) -> None:
        if self._logged_in:
            return
        payload = urlencode({"username": self.username, "password": self.password}).encode()
        response = self._request("/api/v2/auth/login", payload, "application/x-www-form-urlencoded")
        # qBittorrent 5.2 may answer successful mutations with HTTP 204 and an
        # empty body, while older releases return ``Ok.``.
        if response.strip() not in {b"", b"Ok", b"Ok."}:
            raise QbittorrentError("qBittorrent 登录失败，请检查 Web UI 用户名和密码。")
        self._logged_in = True

    def app_version(self) -> str:
        self.login()
        return self._request("/api/v2/app/version").decode("utf-8", errors="replace").strip()

    def webapi_version(self) -> str:
        self.login()
        return self._request("/api/v2/app/webapiVersion").decode("utf-8", errors="replace").strip()

    def _form(self, path: str, fields: dict[str, str]) -> bytes:
        self.login()
        return self._request(path, urlencode(fields).encode(), "application/x-www-form-urlencoded")

    def add(
        self,
        source_url: str | None,
        torrent_data: bytes | None,
        download_dir: str,
        tag: str,
        download_rate_limit_kbps: int = 0,
    ) -> list[str]:
        fields = {"savepath": download_dir, "paused": "true", "tags": tag}
        if download_rate_limit_kbps > 0:
            fields["dlLimit"] = str(download_rate_limit_kbps * 1024)
        if torrent_data is None:
            if not source_url:
                raise QbittorrentError("BT 任务缺少磁力链接或种子文件。")
            response = self._form("/api/v2/torrents/add", {**fields, "urls": source_url})
        else:
            boundary = f"----video-downloader-{secrets.token_hex(12)}"
            parts: list[bytes] = []
            for name, value in fields.items():
                parts.extend([
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode(),
                    b"\r\n",
                ])
            parts.extend([
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="torrents"; filename="source.torrent"\r\n',
                b"Content-Type: application/x-bittorrent\r\n\r\n",
                torrent_data,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ])
            self.login()
            response = self._request("/api/v2/torrents/add", b"".join(parts), f"multipart/form-data; boundary={boundary}")
        normalized_response = response.strip()
        if normalized_response in {b"", b"Ok", b"Ok."}:
            return []
        try:
            result = json.loads(normalized_response)
        except json.JSONDecodeError as error:
            raise QbittorrentError("qBittorrent 没有接受该磁力链接或种子文件。") from error
        if not isinstance(result, dict) or int(result.get("success_count") or 0) < 1 or int(result.get("failure_count") or 0) > 0:
            raise QbittorrentError("qBittorrent 没有接受该磁力链接或种子文件。")
        torrent_ids = result.get("added_torrent_ids")
        return [str(value) for value in torrent_ids] if isinstance(torrent_ids, list) else []

    def info(self, tag: str | None = None, hashes: str | None = None) -> list[dict[str, Any]]:
        self.login()
        query = {}
        if tag:
            query["tag"] = tag
        if hashes:
            query["hashes"] = hashes
        body = self._request(f"/api/v2/torrents/info?{urlencode(query)}")
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise QbittorrentError("qBittorrent 返回了无法识别的任务信息。") from error
        return value if isinstance(value, list) else []

    def _uses_v5_api(self) -> bool:
        version = self.app_version().removeprefix("v")
        try:
            return int(version.split(".", 1)[0]) >= 5
        except ValueError:
            return False

    def resume(self, hashes: str) -> None:
        endpoint = "/api/v2/torrents/start" if self._uses_v5_api() else "/api/v2/torrents/resume"
        self._form(endpoint, {"hashes": hashes})

    def pause(self, hashes: str) -> None:
        endpoint = "/api/v2/torrents/stop" if self._uses_v5_api() else "/api/v2/torrents/pause"
        self._form(endpoint, {"hashes": hashes})

    def delete(self, hashes: str, delete_files: bool = False) -> None:
        self._form(
            "/api/v2/torrents/delete",
            {"hashes": hashes, "deleteFiles": "true" if delete_files else "false"},
        )

    def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        self.login()
        body = self._request(f"/api/v2/torrents/files?{urlencode({'hash': torrent_hash})}")
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise QbittorrentError("qBittorrent 返回了无法识别的文件列表。") from error
        return value if isinstance(value, list) else []

    def file_priority(self, torrent_hash: str, ids: list[int], priority: int) -> None:
        if not ids:
            return
        self._form(
            "/api/v2/torrents/filePrio",
            {"hash": torrent_hash, "id": "|".join(str(value) for value in ids), "priority": str(priority)},
        )


def media_file_path(task: dict[str, Any], files: list[dict[str, Any]], video_extensions: set[str]) -> str | None:
    video_files = [item for item in files if Path(str(item.get("name", ""))).suffix.lower() in video_extensions]
    if len(video_files) != 1:
        return None
    save_path = task.get("save_path")
    name = video_files[0].get("name")
    if not isinstance(save_path, str) or not isinstance(name, str):
        return None
    return str(Path(save_path) / name)
