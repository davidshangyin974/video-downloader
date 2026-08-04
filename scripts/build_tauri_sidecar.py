#!/usr/bin/env python3
"""Bundle the local FastAPI service and download executables for Tauri.

PyInstaller must run on the target platform. Build macOS Apple Silicon on an
Apple Silicon Mac and the Windows sidecar on Windows; this script does not
pretend that Python or native media binaries can be cross-compiled.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAURI_ROOT = ROOT / "desktop" / "tauri"
RUNTIME_ROOT = ROOT / "desktop" / "runtime"
BUILD_ROOT = ROOT / ".build" / "tauri-sidecar"


def target_triple() -> str:
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        return "aarch64-apple-darwin"
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    raise SystemExit("仅支持在 Apple Silicon macOS 或 Windows x64 主机上构建对应安装包。")


def runtime_binary(runtime_dir: Path, name: str) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    path = runtime_dir / "bin" / f"{name}{suffix}"
    if not path.is_file():
        raise SystemExit(f"缺少随包运行文件：{path}\n先准备当前平台的 ffmpeg、ffprobe 和 aria2c。")
    return path


def sign_macos_runtime(runtime_dir: Path) -> None:
    """Give every nested Mach-O the same identity as the outer sidecar."""
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "-")
    candidates = sorted(runtime_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        description = subprocess.run(
            ["file", "-b", str(candidate)], capture_output=True, text=True, check=True
        ).stdout
        if not description.startswith("Mach-O"):
            continue
        subprocess.run(["codesign", "--force", "--sign", identity, str(candidate)], check=True)


def main() -> None:
    target = target_triple()
    runtime_dir = Path(os.environ.get("VIDEO_DOWNLOADER_RUNTIME_DIR", RUNTIME_ROOT / target))
    binaries = [runtime_binary(runtime_dir, name) for name in ("ffmpeg", "ffprobe", "aria2c")]
    libraries = runtime_dir / "lib"
    separator = ";" if sys.platform == "win32" else ":"

    if shutil.which("pyinstaller") is None and subprocess.run(
        [sys.executable, "-c", "import PyInstaller"], check=False
    ).returncode != 0:
        raise SystemExit("未找到 PyInstaller。请先运行 make install-desktop-tools。")

    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    output_dir = BUILD_ROOT / target
    work_dir = BUILD_ROOT / "work" / target
    spec_dir = BUILD_ROOT / "spec"
    shutil.rmtree(output_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "video-downloader-server",
        "--paths",
        str(ROOT / "backend"),
        "--distpath",
        str(output_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--copy-metadata",
        "fastapi",
        "--copy-metadata",
        "uvicorn",
    ]
    if sys.platform == "darwin":
        # Tauri signs the external sidecar again while bundling. Sign every
        # nested Python/native binary with that same identity first; otherwise
        # Apple Silicon rejects the extracted Python framework at launch.
        command.extend(("--codesign-identity", os.environ.get("APPLE_SIGNING_IDENTITY", "-")))
    for binary in binaries:
        command.extend(("--add-binary", f"{binary}{separator}."))
    if libraries.is_dir():
        command.extend(("--add-binary", f"{libraries}{separator}lib"))
    command.append(str(ROOT / "desktop" / "sidecar" / "main.py"))
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(BUILD_ROOT / "pyinstaller-config")
    subprocess.run(command, check=True, cwd=ROOT, env=environment)

    source = output_dir / "video-downloader-server"
    if sys.platform == "darwin":
        sign_macos_runtime(source)
    destination = TAURI_ROOT / "resources" / "sidecar"
    shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    executable = destination / ("video-downloader-server.exe" if sys.platform == "win32" else "video-downloader-server")
    if sys.platform != "win32":
        executable.chmod(0o755)
    print(executable)


if __name__ == "__main__":
    main()
