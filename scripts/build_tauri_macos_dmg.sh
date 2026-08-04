#!/usr/bin/env zsh
set -euo pipefail

project_root="${0:A:h:h}"
app_path="$project_root/desktop/tauri/target/release/bundle/macos/Video Downloader.app"
dmg_path="$project_root/desktop/tauri/target/release/bundle/dmg/Video Downloader_0.1.0_aarch64.dmg"

cd "$project_root"
npm --prefix frontend run tauri:build -- --bundles app

rm -f "$dmg_path"
mkdir -p "${dmg_path:h}"
hdiutil create -quiet -srcfolder "$app_path" -volname "Video Downloader" -format UDZO "$dmg_path"
echo "$dmg_path"
