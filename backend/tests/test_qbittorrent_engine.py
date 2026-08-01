from __future__ import annotations

import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks

from app import server
from app.core import storage
from app.core.models import DownloadRequest, InspectRequest, QbittorrentSettings
from app.engines import qbittorrent
from app.engines.inputs import InputError, route_input
from app.services.engine_tasks import run_qbittorrent_task


class FakeQbittorrentClient:
    def __init__(self, complete: bool = True) -> None:
        self.complete = complete
        self.added = False
        self.priorities: list[tuple[list[int], int]] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.deleted: list[tuple[str, bool]] = []
        self.download_rate_limit_kbps = 0

    def app_version(self) -> str:
        return "v5.1.2"

    def add(
        self,
        _source_url: str | None,
        _torrent_data: bytes | None,
        _download_dir: str,
        _tag: str,
        _download_rate_limit_kbps: int = 0,
    ) -> None:
        self.added = True
        self.download_rate_limit_kbps = _download_rate_limit_kbps

    def info(self, tag: str | None = None, hashes: str | None = None) -> list[dict[str, object]]:
        if not self.added and not hashes:
            return []
        if tag or hashes:
            if self.complete:
                return [{"hash": "abc123", "state": "uploading", "total_size": 9, "completed": 9, "amount_left": 0, "progress": 1, "dlspeed": 0, "eta": 0}]
            return [{"hash": "abc123", "state": "downloading", "total_size": 9, "completed": 0, "amount_left": 9, "progress": 0, "dlspeed": 0, "eta": 100}]
        return []

    def files(self, _torrent_hash: str) -> list[dict[str, object]]:
        return [
            {"index": 0, "name": "selected.mp4", "size": 8},
            {"index": 1, "name": "notes.txt", "size": 1},
        ]

    def file_priority(self, _torrent_hash: str, ids: list[int], priority: int) -> None:
        self.priorities.append((ids, priority))

    def resume(self, torrent_hash: str) -> None:
        self.resumed.append(torrent_hash)

    def pause(self, torrent_hash: str) -> None:
        self.paused.append(torrent_hash)

    def delete(self, torrent_hash: str, delete_files: bool = False) -> None:
        self.deleted.append((torrent_hash, delete_files))


class QbittorrentEngineTests(unittest.TestCase):
    def test_adapter_uses_version_specific_start_and_stop_endpoints(self) -> None:
        client = qbittorrent.Client("http://127.0.0.1:8080", "admin", "secret")
        with patch.object(client, "app_version", return_value="v5.1.2"), patch.object(client, "_form") as form:
            client.resume("abc")
            client.pause("abc")
        self.assertEqual(form.call_args_list[0].args[0], "/api/v2/torrents/start")
        self.assertEqual(form.call_args_list[1].args[0], "/api/v2/torrents/stop")

        with patch.object(client, "app_version", return_value="v4.6.7"), patch.object(client, "_form") as form:
            client.resume("abc")
            client.pause("abc")
        self.assertEqual(form.call_args_list[0].args[0], "/api/v2/torrents/resume")
        self.assertEqual(form.call_args_list[1].args[0], "/api/v2/torrents/pause")

    def test_adapter_accepts_qbittorrent_52_empty_success_body(self) -> None:
        client = qbittorrent.Client("http://127.0.0.1:8080", "admin", "secret")
        with patch.object(client, "_request", return_value=b""):
            client.login()
        self.assertTrue(client._logged_in)

    def test_adapter_accepts_qbittorrent_52_json_add_response(self) -> None:
        client = qbittorrent.Client("http://127.0.0.1:8080", "admin", "secret")
        response = b'{"added_torrent_ids":["abc123"],"failure_count":0,"pending_count":0,"success_count":1}'
        with patch.object(client, "login"), patch.object(client, "_request", return_value=response):
            torrent_ids = client.add("magnet:?xt=urn:btih:abc123", None, "/tmp/download", "test")
        self.assertEqual(torrent_ids, ["abc123"])

    def test_adapter_passes_download_limit_when_adding_torrent(self) -> None:
        client = qbittorrent.Client("http://127.0.0.1:8080", "admin", "secret")
        with patch.object(client, "_form", return_value=b"Ok.") as form:
            client.add("magnet:?xt=urn:btih:abc123", None, "/tmp/download", "test", 256)
        self.assertEqual(form.call_args.args[1]["dlLimit"], str(256 * 1024))

    def test_qbittorrent_route_accepts_only_bt_inputs(self) -> None:
        magnet = route_input("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", "qbittorrent")
        self.assertEqual(magnet["engine"], "qbittorrent")
        torrent_file = route_input("", "qbittorrent", has_torrent_file=True)
        self.assertEqual(torrent_file["engine"], "qbittorrent")
        with self.assertRaisesRegex(InputError, "只用于磁力链接和种子文件"):
            route_input("https://example.com/video.mp4", "qbittorrent")

    def test_qbittorrent_task_selects_files_and_registers_output(self) -> None:
        fake = FakeQbittorrentClient()
        updates: list[dict[str, object]] = []
        logs: list[str] = []
        registered: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "BT" / "qbit-complete" / "selected.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"selected")
            with patch("app.services.engine_tasks.qbittorrent.Client", return_value=fake):
                run_qbittorrent_task(
                    "qbit-complete",
                    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                    temp_dir,
                    None,
                    [0],
                    None,
                    {"base_url": "http://127.0.0.1:8080", "username": "admin", "password": "secret", "bt_stall_timeout": 30, "download_rate_limit_kbps": 512},
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, message: logs.append(message),
                    lambda _id: None,
                    lambda _id: False,
                    registered.append,
                    lambda _id: False,
                    lambda _id: False,
                )
        self.assertIn(([1], 0), fake.priorities)
        self.assertEqual(fake.download_rate_limit_kbps, 512)
        self.assertIn(([0], 1), fake.priorities)
        self.assertEqual(fake.deleted, [("abc123", False)])
        self.assertEqual(updates[-1]["status"], "completed")
        self.assertEqual(updates[-1]["file_size"], 8)
        self.assertTrue(updates[-1]["library_visible"])
        self.assertTrue(any("下载完成" in message for message in logs))

    def test_qbittorrent_pause_reaches_persisted_paused_state(self) -> None:
        fake = FakeQbittorrentClient(complete=False)
        updates: list[dict[str, object]] = []
        pause_checks = 0

        def is_paused(_download_id: str) -> bool:
            nonlocal pause_checks
            pause_checks += 1
            return pause_checks >= 3

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("app.services.engine_tasks.qbittorrent.Client", return_value=fake),
                patch("app.services.engine_tasks.time.sleep", return_value=None),
            ):
                run_qbittorrent_task(
                    "qbit-pause",
                    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                    temp_dir,
                    None,
                    [0],
                    None,
                    {"base_url": "http://127.0.0.1:8080", "username": "admin", "password": "secret", "bt_stall_timeout": 30},
                    lambda _id, **values: updates.append(values),
                    lambda _id, _level, _message: None,
                    lambda _id: None,
                    lambda _id: False,
                    None,
                    is_paused,
                    lambda _id: False,
                )
        self.assertEqual(updates[-1]["status"], "paused")
        self.assertIn("abc123", fake.paused)
        self.assertEqual(fake.deleted, [])


class QbittorrentServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(storage, "DATABASE_PATH", self.root / "data" / "test.sqlite3")
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        storage.initialize_database()

    def test_enabled_qbittorrent_becomes_auto_bt_route_and_can_create_task(self) -> None:
        fake = FakeQbittorrentClient()
        qbit_settings = QbittorrentSettings(
            enabled=True,
            base_url="http://127.0.0.1:8080",
            username="admin",
            password="secret",
            bt_stall_timeout=30,
        )
        torrent_data = (
            b"d4:infod6:lengthi8e4:name12:selected.mp412:piece lengthi16384e"
            b"6:pieces20:00000000000000000000ee"
        )
        with patch("app.server.qbittorrent_client", return_value=fake):
            saved = server.save_qbittorrent_settings(qbit_settings)
            source = server.prepare_inspect_source(
                InspectRequest(
                    engine_hint="auto",
                    torrent_name="selected.torrent",
                    torrent_base64=base64.b64encode(torrent_data).decode(),
                )
            )
        self.assertEqual(source["route"]["engine"], "qbittorrent")
        self.assertTrue(saved["qbittorrent"]["enabled"])

        inspect_id = "qbit-server-create"
        server.inspect_jobs[inspect_id] = {
            "status": "completed",
            "source": source,
            "media": {
                "kind": "torrent",
                "title": "selected.mp4",
                "engine": "qbittorrent",
                "source_type": "torrent_file",
                "files": [{"index": 0, "path": "selected.mp4", "size": 8}],
            },
        }
        self.addCleanup(server.inspect_jobs.pop, inspect_id, None)
        with patch("app.server.qbittorrent_client", return_value=fake):
            created = server.create_download(
                DownloadRequest(
                    inspect_id=inspect_id,
                    selected_file_indexes=[0],
                    download_dir=str(self.root / "downloads"),
                ),
                BackgroundTasks(),
            )
        self.assertEqual(created["engine"], "qbittorrent")
        self.assertEqual(created["status"], "queued")
        task, args = server.existing_download_task(server.download_record(created["id"]))
        self.assertIs(task, run_qbittorrent_task)
        self.assertEqual(args[4], [0])
        self.assertEqual(args[6]["base_url"], "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()
