from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app import server
from app.core.models import BatchDownloadRequest, DownloadRequest


class BatchDownloadTests(unittest.TestCase):
    def test_batch_creation_runs_in_parallel_and_keeps_item_results(self) -> None:
        lock = threading.Lock()
        active = 0
        peak = 0

        def fake_create(
            request: DownloadRequest,
            background_tasks: BackgroundTasks,
        ) -> dict[str, str]:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            if request.url == "https://example.com/fail":
                raise HTTPException(status_code=422, detail="测试失败")
            background_tasks.add_task(lambda: None)
            return {"id": str(request.url), "source_url": str(request.url)}

        request = BatchDownloadRequest(items=[
            DownloadRequest(url=f"https://example.com/{index}")
            for index in range(5)
        ] + [
            DownloadRequest(url="https://example.com/fail"),
        ])
        background_tasks = BackgroundTasks()

        with patch("app.server.create_download", side_effect=fake_create):
            response = server.create_download_batch(request, background_tasks)

        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, server.MAX_CONCURRENT_DOWNLOADS)
        self.assertEqual(response["created"], 5)
        self.assertEqual(response["failed"], 1)
        self.assertEqual(response["failures"][0]["index"], 5)
        self.assertEqual(response["failures"][0]["error"], "测试失败")
        self.assertEqual(len(background_tasks.tasks), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
