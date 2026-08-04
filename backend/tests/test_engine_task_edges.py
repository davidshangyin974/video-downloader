from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.engines import aria2, qbittorrent
from app.services.engine_tasks import run_aria2_task, run_qbittorrent_task


class EngineTaskEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name).resolve()
        self.updates: list[dict[str, object]] = []
        self.logs: list[tuple[str, str]] = []
        self.publishes = 0
        self.processes: list[object | None] = []

    def update(self, _download_id: str, **values: object) -> None:
        self.updates.append(values)

    def log(self, _download_id: str, level: str, message: str) -> None:
        self.logs.append((level, message))

    def publish(self, _download_id: str) -> None:
        self.publishes += 1

    def run_aria2(
        self,
        *,
        download_id: str = "aria-task",
        source_type: str = "direct",
        file_name: str | None = "movie.mp4",
        torrent_data: bytes | None = None,
        is_cancelled=lambda _id: False,
        is_paused=None,
        is_interrupted=None,
        register_media_outputs=None,
    ) -> None:
        run_aria2_task(
            download_id,
            "magnet:?xt=urn:btih:test" if source_type == "magnet" else "https://example.com/source",
            str(self.root),
            source_type,
            file_name,
            100,
            torrent_data,
            [0],
            self.update,
            self.log,
            self.publish,
            is_cancelled,
            lambda _id, process: self.processes.append(process),
            {"bt_stall_timeout": 5},
            register_media_outputs,
            is_paused,
            is_interrupted,
        )

    def test_aria2_early_stop_for_paused_or_interrupted_tasks(self) -> None:
        with patch("app.services.engine_tasks.aria2.download") as download:
            self.run_aria2(is_paused=lambda _id: True)
            self.run_aria2(is_interrupted=lambda _id: True)
        download.assert_not_called()
        self.assertEqual(self.updates, [])

    def test_aria2_bt_progress_torrent_data_and_library_registration_failure(self) -> None:
        task_dir = self.root / "BT" / "bt-task"
        task_dir.mkdir(parents=True)
        first = task_dir / "one.mp4"
        second = task_dir / "two.mkv"
        first.write_bytes(b"one")
        second.write_bytes(b"two")

        def fake_bt(*args: object) -> list[Path]:
            on_progress = args[6]
            on_progress(0, None, 0)
            on_progress(0, None, 0)
            return [first, second]

        with (
            patch("app.services.engine_tasks.aria2.download_bt", side_effect=fake_bt),
            patch("app.services.engine_tasks.time.monotonic", side_effect=[0, 31, 32]),
        ):
            self.run_aria2(
                download_id="bt-task",
                source_type="torrent_file",
                torrent_data=b"torrent",
                register_media_outputs=MagicMock(side_effect=RuntimeError("index failed")),
            )
        self.assertTrue((self.root / ".video-downloader" / "torrents" / "bt-task.torrent").is_file())
        self.assertEqual(self.updates[-1]["status"], "completed")
        self.assertFalse(self.updates[-1]["library_visible"])
        self.assertEqual(sum("仍未收到 BT 数据" in message for _level, message in self.logs), 1)
        self.assertTrue(any("加入媒体库时失败" in message for _level, message in self.logs))

    def test_aria2_torrent_url_downloads_source_before_bt(self) -> None:
        source_file = self.root / "BT" / "torrent-url" / "movie.mp4"
        source_file.parent.mkdir(parents=True)
        source_file.write_bytes(b"video")

        def fake_source(_url: str, directory: str, name: str, *_args: object) -> Path:
            target = Path(directory) / name
            target.write_bytes(b"torrent")
            return target

        with (
            patch("app.services.engine_tasks.aria2.download", side_effect=fake_source) as download,
            patch("app.services.engine_tasks.aria2.download_bt", return_value=[source_file]),
        ):
            self.run_aria2(download_id="torrent-url", source_type="torrent_url")
        download.assert_called_once()
        self.assertEqual(self.updates[-1]["status"], "completed")

    def test_aria2_missing_torrent_file_and_direct_name_fail_cleanly(self) -> None:
        self.run_aria2(source_type="torrent_file", torrent_data=None)
        self.assertEqual(self.updates[-1]["status"], "failed")
        self.assertIn("重新添加", str(self.updates[-1]["error"]))

        self.updates.clear()
        self.run_aria2(file_name=None)
        self.assertEqual(self.updates[-1]["status"], "failed")
        self.assertIn("缺少输出文件名", str(self.updates[-1]["error"]))

    def test_aria2_error_maps_to_interrupted_cancelled_and_failed(self) -> None:
        def fail(*_args: object, **_kwargs: object) -> Path:
            raise aria2.Aria2Error("engine failed")

        interrupted_checks = iter([False, True])
        with patch("app.services.engine_tasks.aria2.download", side_effect=fail):
            self.run_aria2(is_interrupted=lambda _id: next(interrupted_checks))
        self.assertEqual(self.updates[-1]["status"], "interrupted")

        self.updates.clear()
        with patch("app.services.engine_tasks.aria2.download", side_effect=fail):
            self.run_aria2(is_cancelled=lambda _id: True)
        self.assertEqual(self.updates[-1]["status"], "cancelled")

        self.updates.clear()
        with patch("app.services.engine_tasks.aria2.download", side_effect=fail):
            self.run_aria2()
        self.assertEqual(self.updates[-1]["status"], "failed")

    def qbit_client(self) -> MagicMock:
        client = MagicMock()
        client.app_version.return_value = "v5.2.0"
        client.add.return_value = []
        client.info.return_value = []
        client.files.return_value = []
        return client

    def run_qbit(
        self,
        client: MagicMock,
        *,
        download_id: str = "qbit-task",
        selected: list[int] | None = None,
        engine_task_id: str | None = None,
        is_cancelled=lambda _id: False,
        is_paused=None,
        is_interrupted=None,
        register_media_outputs=None,
        monotonic=None,
    ) -> None:
        contexts = [
            patch("app.services.engine_tasks.qbittorrent.Client", return_value=client),
            patch("app.services.engine_tasks.time.sleep", return_value=None),
        ]
        if monotonic is not None:
            contexts.append(patch("app.services.engine_tasks.time.monotonic", side_effect=monotonic))
        with contexts[0], contexts[1]:
            if len(contexts) == 3:
                with contexts[2]:
                    self._call_qbit(
                        download_id, selected, engine_task_id, is_cancelled, is_paused,
                        is_interrupted, register_media_outputs,
                    )
            else:
                self._call_qbit(
                    download_id, selected, engine_task_id, is_cancelled, is_paused,
                    is_interrupted, register_media_outputs,
                )

    def _call_qbit(
        self,
        download_id: str,
        selected: list[int] | None,
        engine_task_id: str | None,
        is_cancelled,
        is_paused,
        is_interrupted,
        register_media_outputs,
    ) -> None:
        run_qbittorrent_task(
            download_id,
            "magnet:?xt=urn:btih:test",
            str(self.root),
            None,
            selected,
            engine_task_id,
            {"base_url": "http://localhost:8080", "bt_stall_timeout": 5, "download_rate_limit_kbps": 64},
            self.update,
            self.log,
            self.publish,
            is_cancelled,
            register_media_outputs,
            is_paused,
            is_interrupted,
        )

    def complete_task(self, hash_value: str = "hash") -> dict[str, object]:
        return {
            "hash": hash_value,
            "state": "uploading",
            "total_size": 5,
            "completed": 5,
            "amount_left": 0,
            "progress": 1,
            "dlspeed": 0,
            "eta": 0,
        }

    def test_qbit_early_stop_for_paused_or_interrupted_tasks(self) -> None:
        client = self.qbit_client()
        self.run_qbit(client, is_paused=lambda _id: True)
        self.run_qbit(client, is_interrupted=lambda _id: True)
        client.app_version.assert_not_called()

    def test_qbit_add_hash_and_delayed_tag_lookup_paths_complete(self) -> None:
        for added_hashes, info_results, download_id in (
            (["hash"], [[], [self.complete_task()], [self.complete_task()]], "added-hash"),
            ([], [[], [], [self.complete_task()], [self.complete_task()]], "tag-lookup"),
        ):
            with self.subTest(download_id=download_id):
                self.updates.clear()
                client = self.qbit_client()
                client.add.return_value = added_hashes
                client.info.side_effect = info_results
                file_info = [{"index": 0, "name": "movie.mp4"}]
                client.files.side_effect = [file_info, file_info]
                output = self.root / "BT" / download_id / "movie.mp4"
                output.parent.mkdir(parents=True)
                output.write_bytes(b"video")
                self.run_qbit(client, download_id=download_id)
                self.assertEqual(self.updates[-1]["status"], "completed")

    def test_qbit_add_can_stop_or_expire_without_task_identifier(self) -> None:
        client = self.qbit_client()
        client.info.return_value = []
        self.run_qbit(client, is_cancelled=lambda _id: True, monotonic=[0, 1])
        self.assertEqual(self.updates[-1]["status"], "cancelled")

        self.updates.clear()
        client = self.qbit_client()
        client.info.return_value = []
        self.run_qbit(client, monotonic=[0, 31])
        self.assertEqual(self.updates[-1]["status"], "failed")
        self.assertIn("未返回任务标识", str(self.updates[-1]["error"]))

        self.updates.clear()
        client = self.qbit_client()
        client.info.return_value = [{"hash": ""}]
        self.run_qbit(client)
        self.assertIn("缺少哈希", str(self.updates[-1]["error"]))

    def test_qbit_metadata_wait_success_stop_and_timeout(self) -> None:
        file_info = [{"index": 0, "name": "movie.mp4"}]
        client = self.qbit_client()
        client.info.side_effect = [[self.complete_task()], [self.complete_task()]]
        client.files.side_effect = [[], [], file_info, file_info]
        output = self.root / "BT" / "metadata-success" / "movie.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"video")
        self.run_qbit(client, download_id="metadata-success")
        self.assertEqual(self.updates[-1]["status"], "completed")

        self.updates.clear()
        client = self.qbit_client()
        client.info.return_value = [self.complete_task()]
        client.files.return_value = []
        self.run_qbit(client, engine_task_id="hash", is_cancelled=lambda _id: True)
        self.assertEqual(self.updates[-1]["status"], "cancelled")

        self.updates.clear()
        client = self.qbit_client()
        client.info.return_value = [self.complete_task()]
        client.files.return_value = []
        self.run_qbit(client, engine_task_id="hash", monotonic=[0, 6])
        self.assertIn("没有取得 BT 文件列表", str(self.updates[-1]["error"]))

    def test_qbit_rejects_selection_removed_task_and_error_state(self) -> None:
        file_info = [{"index": 0, "name": "movie.mp4"}]
        client = self.qbit_client()
        client.info.return_value = [self.complete_task()]
        client.files.return_value = file_info
        self.run_qbit(client, selected=[9], engine_task_id="hash")
        self.assertIn("文件列表不一致", str(self.updates[-1]["error"]))

        for task_result, message in (([], "已被移除"), ([{"hash": "hash", "state": "error"}], "状态异常")):
            self.updates.clear()
            client = self.qbit_client()
            client.info.side_effect = [[self.complete_task()], task_result]
            client.files.return_value = file_info
            self.run_qbit(client, engine_task_id="hash")
            self.assertIn(message, str(self.updates[-1]["error"]))

    def test_qbit_stall_detection_and_output_path_failures(self) -> None:
        downloading = {
            "hash": "hash",
            "state": "downloading",
            "total_size": 5,
            "completed": 0,
            "amount_left": 5,
            "progress": 0,
            "dlspeed": 0,
            "eta": 99,
        }
        client = self.qbit_client()
        client.info.side_effect = [[self.complete_task()], [downloading], [downloading]]
        client.files.return_value = [{"index": 0, "name": "movie.mp4"}]
        self.run_qbit(client, engine_task_id="hash", monotonic=[0, 0, 6])
        self.assertIn("没有获得新数据", str(self.updates[-1]["error"]))

        cases = (
            ([{"index": 0, "name": "../escape.mp4"}], "不安全的输出路径"),
            ([{"index": 0, "name": "missing.mp4"}], "无法访问输出文件"),
            ([{"index": 1, "name": "ignored.mp4"}], "没有找到已选择"),
        )
        for final_files, message in cases:
            with self.subTest(message=message):
                self.updates.clear()
                client = self.qbit_client()
                client.info.side_effect = [[self.complete_task()], [self.complete_task()]]
                client.files.side_effect = [[{"index": 0, "name": "movie.mp4"}], final_files]
                self.run_qbit(client, selected=[0], engine_task_id="hash")
                self.assertIn(message, str(self.updates[-1]["error"]))

    def test_qbit_multiple_media_registers_outputs(self) -> None:
        client = self.qbit_client()
        client.info.side_effect = [[self.complete_task()], [self.complete_task()]]
        file_info = [{"index": 0, "name": "one.mp4"}, {"index": 1, "name": "two.mkv"}]
        client.files.side_effect = [file_info, file_info]
        output_dir = self.root / "BT" / "multi"
        output_dir.mkdir(parents=True)
        (output_dir / "one.mp4").write_bytes(b"one")
        (output_dir / "two.mkv").write_bytes(b"two")
        register = MagicMock()
        self.run_qbit(client, download_id="multi", engine_task_id="hash", register_media_outputs=register)
        register.assert_called_once_with("multi")
        self.assertFalse(self.updates[-1]["library_visible"])

    def test_qbit_errors_map_to_interrupted_cancelled_failed_and_unexpected(self) -> None:
        scenarios = []

        interrupted = iter([False, True])
        scenarios.append((lambda _id: False, None, lambda _id: next(interrupted), "interrupted"))
        scenarios.append((lambda _id: True, None, None, "cancelled"))
        scenarios.append((lambda _id: False, None, None, "failed"))
        for cancelled, paused, interrupted_check, expected in scenarios:
            with self.subTest(expected=expected):
                self.updates.clear()
                client = self.qbit_client()
                client.app_version.side_effect = qbittorrent.QbittorrentError("failed")
                self.run_qbit(
                    client,
                    engine_task_id="hash",
                    is_cancelled=cancelled,
                    is_paused=paused,
                    is_interrupted=interrupted_check,
                )
                self.assertEqual(self.updates[-1]["status"], expected)

        self.updates.clear()
        client = self.qbit_client()
        client.app_version.side_effect = qbittorrent.QbittorrentError("failed")
        client.pause.side_effect = qbittorrent.QbittorrentError("pause failed")
        self.run_qbit(client, engine_task_id="hash")
        self.assertEqual(self.updates[-1]["status"], "failed")

        self.updates.clear()
        client = self.qbit_client()
        client.app_version.side_effect = ValueError("unexpected")
        self.run_qbit(client)
        self.assertEqual(self.updates[-1]["status"], "failed")
        self.assertTrue(any("下载异常" in message for _level, message in self.logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
