#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
APP_DIR="$PROJECT_ROOT/dist/Video Downloader.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
mkdir -p "$PROJECT_ROOT/.build/clang-module-cache"
CLANG_MODULE_CACHE_PATH="$PROJECT_ROOT/.build/clang-module-cache" clang \
  -fobjc-arc \
  "$PROJECT_ROOT/desktop/macos/VideoDownloaderApp.m" \
  -o "$MACOS_DIR/Video Downloader" \
  -framework Cocoa \
  -framework WebKit
cp "$PROJECT_ROOT/desktop/macos/Info.plist" "$CONTENTS_DIR/Info.plist"
print -r -- "$PROJECT_ROOT" > "$RESOURCES_DIR/project-root.txt"
chmod +x "$MACOS_DIR/Video Downloader"

echo "$APP_DIR"
