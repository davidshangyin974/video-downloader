# Video Downloader

一个在本机运行的视频、音频下载器和个人媒体库。它用 `yt-dlp` 处理网页媒体，用 `aria2` 处理直链、迅雷链接和 BT 资源，并通过本地 Web 界面统一管理下载、播放与文件。

项目当前以 macOS 个人使用为主。所有服务默认只监听 `127.0.0.1`，任务和媒体信息保存在本机 SQLite 数据库中，不依赖云端账号。

> 本项目不提供任何视频、音频、磁力链接或种子文件。请只下载你拥有权利、已获授权、属于公有领域，或来源平台明确允许下载的内容。

## 功能

- 下载网页视频和音频，可选择格式、字幕、封面、章节与播放列表。
- 下载 HTTP、HTTPS、FTP 直链和迅雷链接。
- 读取本地或远程 `.torrent` 文件，在开始下载前选择需要的文件。
- 解析磁力链接的文件列表，并按文件选择结果创建 BT 任务。
- 跟踪下载进度、速度、日志和失败原因，支持暂停、继续、重试和取消。
- 分开管理视频与音频，支持搜索、排序、筛选、继续观看和下一集。
- 导入或扫描本地媒体目录，重新定位丢失文件，编辑轻量元数据。
- 管理字幕、封面、播放列表、关注更新和已有视频的画质升级。
- 检测内容完全相同的重复文件，在明确确认后移入废纸篓。
- 导出和恢复任务、媒体记录、合集与设置。备份文件不包含媒体文件本身。

## 支持范围

| 输入 | 默认引擎 | 说明 |
| --- | --- | --- |
| 网页媒体链接 | `yt-dlp` | 实际支持的网站和格式以当前 `yt-dlp` 能力为准 |
| HTTP / HTTPS / FTP 直链 | `aria2` | 仅接受视频和音频文件 |
| 迅雷链接 | `aria2` | 解码后按直链下载 |
| `.torrent` 文件或地址 | `aria2` | 校验种子并允许选择文件 |
| 磁力链接 | `aria2` | 先查询经过 Info Hash 校验的公共元数据缓存，未命中时再使用 DHT |
| BT 资源 | qBittorrent，可选 | 需要自行启用并配置 qBittorrent Web UI |

磁力链接能否获得文件列表或开始下载，取决于公共元数据缓存、DHT、网络代理和可用节点。需要登录的网站可能还需要在设置中配置浏览器 Cookie。

项目暂不提供安装包、自动更新、云同步、移动端同步、自定义标签、时间点笔记、AI 识别或转写，也不提供直播电视功能。普通网页中的 HLS / `.m3u8` 媒体仍可由 `yt-dlp` 下载。

## 系统要求

推荐在 macOS 上使用。开始前需要安装：

- Python 3.11 或更高版本
- Node.js 20.19 以上、22.12 以上或更新版本，以及 npm
- FFmpeg
- aria2
- Xcode Command Line Tools 中的 `clang`，仅构建 macOS 桌面应用时需要
- qBittorrent，可选，仅在使用 qBittorrent Web API 时需要

使用 Homebrew 可安装主要依赖：

```bash
brew install python node ffmpeg aria2
```

如需构建桌面应用但系统没有 `clang`：

```bash
xcode-select --install
```

## 快速开始

```bash
git clone https://github.com/davidshangyin974/video-downloader.git
cd video-downloader
make install
make macos-app
open "dist/Video Downloader.app"
```

`make install` 会在 `backend/.venv` 创建 Python 虚拟环境，并安装前端依赖。桌面应用不会把 Python、FFmpeg、aria2 或 qBittorrent 打进应用包，而是使用项目目录和系统中已安装的依赖。

构建完成后，应用位于 `dist/Video Downloader.app`。移动或重命名项目目录后，需要重新运行 `make macos-app`。

## 基本使用

1. 打开应用，点击右上角的新建下载。
2. 粘贴一个链接并解析；也可以粘贴多行链接批量创建任务。
3. 网页媒体可选择格式；BT 资源在获得元数据后可选择需要的文件。
4. 在任务管理中查看进度、日志和失败原因。
5. 下载完成的内容会进入视频管理或音频管理，可在应用内播放和整理。

应用设置中可以配置下载目录、目录命名规则、并发数、限速、`yt-dlp` 参数、FFmpeg 参数、aria2 行为和可选的 qBittorrent Web UI。涉及 Cookie、代理和外部客户端的配置只保存在本机。

## 数据与文件位置

- SQLite 数据库：`data/video-downloader.sqlite3`
- 默认下载目录：`data/downloads`
- macOS 桌面应用日志：`~/Library/Logs/Video Downloader/backend.log`
- 桌面应用构建结果：`dist/Video Downloader.app`

数据库保存任务、进度、日志、媒体路径和经过筛选的结构化元数据。应用只监听本机地址，不会主动把媒体库上传到远程服务。版本检查和下载本身仍会访问对应的软件源、媒体源或 BT 网络。

## 开发模式

先安装依赖：

```bash
make install
```

然后分别启动后端与前端：

```bash
make dev-backend
```

```bash
make dev-frontend
```

前端默认运行在 `http://127.0.0.1:5173`，并将 `/api` 和 `/health` 请求代理到 `http://127.0.0.1:8000`。

主要目录：

```text
backend/       FastAPI、SQLite、下载引擎和测试
frontend/      React、TypeScript 和 Vite 前端
desktop/macos/ macOS 原生启动器
scripts/       构建脚本
data/          本地数据库和默认下载目录，不提交到 Git
```

后端 API 统一使用 `/api/v1/...` 前缀。前端通过 HTTP 和 SSE 与后端通信，不直接调用 Python 模块或下载器命令。

## 检查与测试

```bash
make check
make build
make test
```

需要检查输入解析和文件名安全边界时：

```bash
make test-fuzz
```

需要运行完整测试并检查关键后端代码的 98% 行覆盖率门禁时：

```bash
make test-coverage
```

## 常见问题

### 提示找不到 aria2c 或 FFmpeg

确认命令可以在当前终端中运行：

```bash
aria2c --version
ffmpeg -version
```

如果刚通过 Homebrew 安装，重新打开应用或终端后再试。

### 网站提示需要登录

先在浏览器中登录对应网站，再在应用设置中配置受支持的 `--cookies-from-browser` 参数。受 DRM 保护或账号本身无权访问的内容不在支持范围内。

### 磁力链接长时间没有文件列表

这通常表示公共缓存未命中且 DHT 暂时没有取得元数据。代理的 Fake-IP、UDP 受限、防火墙或节点不可用也会造成失败。让 aria2 和 DHT 流量直连后重试。

### 内置播放器无法播放下载的视频

可以先用系统播放器、IINA 或 VLC 打开。若音视频编码可播放但封装不兼容，可在应用中生成保留原文件的 MP4 兼容副本。

## 参与贡献

欢迎提交 Issue 或 Pull Request。提交代码前请：

1. 说明问题、使用场景和可复现步骤。
2. 保持修改范围聚焦，不改变现有 `/api/v1/...`、SQLite 和前端契约。
3. 运行与修改直接相关的测试；涉及前端时同时运行 `make check` 和 `make build`。
4. 不提交媒体文件、Cookie、账号信息、下载记录、数据库或其他私密数据。

## 第三方软件与使用边界

本项目是 `yt-dlp`、aria2、FFmpeg 等开源工具的本地界面与任务管理层。完整依赖和许可证记录见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，各项目的著作权与许可证仍归原权利人。

用户需自行遵守所在地法律、内容授权条件和第三方平台条款，并对提交的链接、下载内容及后续使用负责。本项目与 YouTube、Bilibili 或其他内容平台不存在隶属、授权或背书关系。
