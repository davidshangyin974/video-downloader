from __future__ import annotations

import threading
import time
import os
import urllib.error
import urllib.request
import webbrowser
import shutil
from pathlib import Path

import uvicorn

from app.main import app


HOST = "127.0.0.1"
PORT = int(os.environ.get("VIDEO_DOWNLOADER_PORT", "8000"))
if not 1 <= PORT <= 65535:
    raise SystemExit("VIDEO_DOWNLOADER_PORT 必须是 1 到 65535 之间的端口。")
HEALTH_URL = f"http://{HOST}:{PORT}/health"
APP_URL = f"http://{HOST}:{PORT}"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def check_dependencies() -> None:
    missing = [name for name in ("aria2c", "ffmpeg") if shutil.which(name) is None]
    if not FRONTEND_DIST.joinpath("index.html").is_file():
        missing.append("frontend/dist")
    if missing:
        raise SystemExit(f"缺少运行依赖：{', '.join(missing)}")


def open_browser_after_health() -> None:
    for _ in range(50):
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(APP_URL)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)


def main() -> None:
    check_dependencies()
    if os.environ.get("VIDEO_DOWNLOADER_DESKTOP") != "1":
        browser_thread = threading.Thread(target=open_browser_after_health, daemon=True)
        browser_thread.start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
