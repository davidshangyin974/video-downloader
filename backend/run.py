from __future__ import annotations

import threading
import time
import os
import socket
import urllib.error
import urllib.request
import webbrowser
import shutil
from pathlib import Path

import uvicorn

from app.main import app


HOST = "127.0.0.1"
PORT = int(os.environ.get("VIDEO_DOWNLOADER_PORT", "8000"))
if not 0 <= PORT <= 65535:
    raise SystemExit("VIDEO_DOWNLOADER_PORT 必须是 0 到 65535 之间的端口。")
HEALTH_URL = f"http://{HOST}:{PORT}/health"
APP_URL = f"http://{HOST}:{PORT}"
FRONTEND_DIST = Path(
    os.environ.get("VIDEO_DOWNLOADER_FRONTEND_DIST", Path(__file__).resolve().parent.parent / "frontend" / "dist")
)


def check_dependencies() -> None:
    missing = [name for name in ("aria2c", "ffmpeg") if shutil.which(name) is None]
    # Packaged Tauri builds serve the frontend from the app bundle. The local
    # API sidecar still needs the two download executables, but no project
    # checkout or frontend directory at runtime.
    if os.environ.get("VIDEO_DOWNLOADER_DESKTOP") != "1" and not FRONTEND_DIST.joinpath("index.html").is_file():
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


def bind_desktop_socket() -> socket.socket | None:
    if os.environ.get("VIDEO_DOWNLOADER_DESKTOP") != "1" or PORT != 0:
        return None
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, 0))
    listener.listen(socket.SOMAXCONN)
    address = f"http://{HOST}:{listener.getsockname()[1]}"
    print(f"VIDEO_DOWNLOADER_API_BASE={address}", flush=True)
    return listener


def main() -> None:
    check_dependencies()
    desktop_socket = bind_desktop_socket()
    if os.environ.get("VIDEO_DOWNLOADER_DESKTOP") != "1":
        browser_thread = threading.Thread(target=open_browser_after_health, daemon=True)
        browser_thread.start()
    if desktop_socket is not None:
        server = uvicorn.Server(uvicorn.Config(app, log_level="info"))
        server.run(sockets=[desktop_socket])
        return
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
