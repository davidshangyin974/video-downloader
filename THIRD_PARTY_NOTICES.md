# Third-Party Software Notices

This file records the third-party software directly used by Video Downloader and the runtime dependencies installed in the verified development environment on 2026-08-02. Copyright and license rights remain with the respective authors.

The current macOS launcher does not bundle `aria2c`, FFmpeg, qBittorrent, Python, `backend/.venv`, or media files. It starts the dependencies installed in the local project or operating system. A future standalone client or Docker image must regenerate this inventory from the exact release artifact and include the corresponding license texts and source-code obligations.

## Download and media engines

| Component | Verified version | Role | License | Project |
| --- | --- | --- | --- | --- |
| yt-dlp | 2026.7.4 | Web media inspection and download | The Unlicense | https://github.com/yt-dlp/yt-dlp |
| aria2 | 1.37.0 | Direct, Thunder and BitTorrent downloads | GPL-2.0-or-later | https://github.com/aria2/aria2 |
| FFmpeg | 8.1.2 | Media merge and transcoding | LGPL-2.1-or-later; GPL applies to GPL-enabled builds | https://ffmpeg.org/ |
| qBittorrent | Runtime-detected, optional external client | Optional BitTorrent Web API client | GPL-2.0 | https://github.com/qbittorrent/qBittorrent |

The locally detected FFmpeg 8.1.2 build was configured with GPL components. Do not copy that binary into a client or container without satisfying the applicable GPL terms and providing the exact corresponding FFmpeg source and build information.

## Backend runtime

The following versions were installed in `backend/.venv`. Test-only packages are listed separately.

| Component | Version | License | Project |
| --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.14.2 | MIT | https://github.com/agronholm/anyio |
| click | 8.4.2 | BSD-3-Clause | https://github.com/pallets/click |
| FastAPI | 0.141.1 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httptools | 0.8.0 | MIT | https://github.com/MagicStack/httptools |
| idna | 3.18 | BSD-3-Clause | https://github.com/kjd/idna |
| pydantic | 2.13.4 | MIT | https://github.com/pydantic/pydantic |
| pydantic-core | 2.46.4 | MIT | https://github.com/pydantic/pydantic-core |
| python-dotenv | 1.2.2 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| Starlette | 1.3.1 | BSD-3-Clause | https://github.com/Kludex/starlette |
| typing-inspection | 0.4.2 | MIT | https://github.com/pydantic/typing-inspection |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| Uvicorn | 0.52.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| uvloop | 0.22.1 | MIT OR Apache-2.0 | https://github.com/MagicStack/uvloop |
| watchfiles | 1.2.0 | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.0 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| yt-dlp | 2026.7.4 | The Unlicense | https://github.com/yt-dlp/yt-dlp |

## Frontend runtime bundle

Versions come from `frontend/package-lock.json`.

| Component | Version | License | Project |
| --- | --- | --- | --- |
| React | 19.2.8 | MIT | https://github.com/facebook/react |
| React DOM | 19.2.8 | MIT | https://github.com/facebook/react |
| scheduler | 0.27.0 | MIT | https://github.com/facebook/react |
| Plyr | 3.8.4 | MIT | https://github.com/sampotts/plyr |
| core-js | 3.49.0 | MIT | https://github.com/zloirock/core-js |
| custom-event-polyfill | 1.0.7 | MIT | https://github.com/krambuhl/custom-event-polyfill |
| loadjs | 4.3.0 | MIT | https://github.com/muicss/loadjs |
| rangetouch | 2.0.1 | MIT | https://github.com/sampotts/rangetouch |
| url-polyfill | 1.1.14 | MIT | https://github.com/lifaon74/url-polyfill |
| WaveSurfer.js | 7.12.11 | BSD-3-Clause | https://github.com/katspaugh/wavesurfer.js |

## Build and test tools

These tools are not part of the application runtime bundle unless a distributor includes them separately.

| Component | Version | License | Project |
| --- | --- | --- | --- |
| Vite | 8.1.5 | MIT | https://github.com/vitejs/vite |
| @vitejs/plugin-react | 6.0.4 | MIT | https://github.com/vitejs/vite-plugin-react |
| TypeScript | 7.0.2 | Apache-2.0 | https://github.com/microsoft/TypeScript |
| coverage.py | 7.15.2 | Apache-2.0 | https://github.com/nedbat/coveragepy |
| Hypothesis | 6.164.0 | MPL-2.0 | https://github.com/HypothesisWorks/hypothesis |
| sortedcontainers | 2.4.0 | Apache-2.0 | https://github.com/grantjenks/python-sortedcontainers |

## User-configured live directories

The application package does not contain a default live-channel directory or stream addresses. It only reads M3U URLs explicitly configured by the user, checks the current HLS manifests, and temporarily caches playable results in local memory. Users remain responsible for the licenses, authorization, and terms that apply to each configured source.

## Distribution requirements

Before publishing a standalone client or Docker image:

1. Generate an inventory from the final artifact, not only from this development snapshot.
2. Include every required copyright notice and license text in the distributed artifact.
3. For any included GPL binary, provide the exact corresponding source and build information using a method allowed by that GPL version.
4. Record the exact FFmpeg configuration and licenses of optional codecs included in the build.
5. Keep optional external programs clearly separate from programs actually copied into the artifact.

This notice does not license Video Downloader itself. A separate project license must be selected by the copyright owner before granting others permission to copy, modify, or redistribute this repository.
