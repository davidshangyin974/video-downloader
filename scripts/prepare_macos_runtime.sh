#!/bin/zsh
# Create a relocatable Apple Silicon runtime for the distributable sidecar.
# It copies Homebrew's complete transitive dylib closure and rewrites those
# absolute references, so recipients do not need Homebrew or these tools.
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
RUNTIME_DIR="${VIDEO_DOWNLOADER_RUNTIME_DIR:-${PROJECT_ROOT}/desktop/runtime/aarch64-apple-darwin}"
BIN_DIR="${RUNTIME_DIR}/bin"
LIB_DIR="${RUNTIME_DIR}/lib"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  print -u2 -- "此脚本只能在 Apple Silicon macOS 上运行。"
  exit 1
fi

for command_name in ffmpeg ffprobe aria2c otool install_name_tool codesign; do
  command -v "$command_name" >/dev/null || { print -u2 -- "缺少 ${command_name}"; exit 1; }
done

if [[ -d "$RUNTIME_DIR" ]]; then
  chmod -R u+w "$RUNTIME_DIR"
fi
rm -rf "$RUNTIME_DIR"
mkdir -p "$BIN_DIR" "$LIB_DIR"
cp -L "$(command -v ffmpeg)" "$BIN_DIR/ffmpeg"
cp -L "$(command -v ffprobe)" "$BIN_DIR/ffprobe"
cp -L "$(command -v aria2c)" "$BIN_DIR/aria2c"

typeset -a queue
queue=("$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe" "$BIN_DIR/aria2c")
typeset -A seen

while (( ${#queue[@]} )); do
  current="$queue[1]"
  queue[1]=()
  while IFS= read -r dependency; do
    [[ "$dependency" == /opt/homebrew/* ]] || continue
    [[ -f "$dependency" ]] || continue
    dependency="${dependency:A}"
    name="${dependency:t}"
    destination="$LIB_DIR/$name"
    if [[ -z "${seen[$dependency]-}" ]]; then
      seen[$dependency]=1
      cp -L "$dependency" "$destination"
      queue+=("$destination")
    fi
  done < <(otool -L "$current" | tail -n +2 | awk '{print $1}')
done

chmod u+w "$BIN_DIR"/* "$LIB_DIR"/*

for current in "$BIN_DIR"/* "$LIB_DIR"/*; do
  while IFS= read -r dependency; do
    [[ "$dependency" == /opt/homebrew/* ]] || continue
    name="${dependency:t}"
    if [[ -f "$LIB_DIR/$name" ]]; then
      if [[ "$current" == "$BIN_DIR"/* ]]; then
        install_name_tool -change "$dependency" "@executable_path/lib/$name" "$current"
      else
        install_name_tool -change "$dependency" "@loader_path/$name" "$current"
      fi
    fi
  done < <(otool -L "$current" | tail -n +2 | awk '{print $1}')
  if [[ "$current" == "$LIB_DIR"/* ]]; then
    install_name_tool -id "@loader_path/${current:t}" "$current"
  fi
done

for current in "$LIB_DIR"/* "$BIN_DIR"/*; do
  codesign --force --sign - "$current" >/dev/null
done

print -- "已准备可随包携带的运行文件：$RUNTIME_DIR"
