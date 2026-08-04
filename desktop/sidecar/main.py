"""Entrypoint bundled with the distributable desktop application.

The Tauri shell starts this process as a local-only FastAPI sidecar. PyInstaller
extracts bundled executables beside this file at runtime, so add that directory
to PATH before importing the application.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bundled_root() -> Path:
    value = getattr(sys, "_MEIPASS", None)
    return Path(value) if value else Path(sys.executable).resolve().parent


def default_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Video Downloader"
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA")
        return Path(app_data) / "Video Downloader" if app_data else home / "AppData" / "Roaming" / "Video Downloader"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "video-downloader"


def configure_runtime() -> None:
    root = bundled_root()
    os.environ.setdefault("VIDEO_DOWNLOADER_DESKTOP", "1")
    os.environ.setdefault("VIDEO_DOWNLOADER_DATA_DIR", str(default_data_dir()))
    os.environ["PATH"] = os.pathsep.join((str(root), str(root / "lib"), os.environ.get("PATH", "")))


configure_runtime()

# PyInstaller receives --paths backend, making this import resolve to the
# existing runner without maintaining a second server implementation.
from run import main  # noqa: E402


if __name__ == "__main__":
    main()
