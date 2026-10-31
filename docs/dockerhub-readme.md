# Frame Video Studio

![Frame Video Studio icon](https://raw.githubusercontent.com/hythzx/Frame-Video-Studio/main/frontend/public/favicon.svg)

**A self-hosted media downloader powered by yt-dlp.**

Frame Video Studio uses **yt-dlp to recognize many media websites** from one interface. X / Twitter, YouTube and Bilibili receive dedicated adaptations; sites such as Vimeo, TikTok and Instagram can also be attempted when their yt-dlp extractors and media formats are compatible. Paste an HTTPS media page URL, inspect available formats, and download the content you want to keep. Powered by **yt-dlp, FastAPI, FFmpeg, Vue 3, and Nginx**.

**[GitHub Repository](https://github.com/hythzx/Frame-Video-Studio)** · **[Documentation](https://github.com/hythzx/Frame-Video-Studio#readme)**

## Docker images

Frame Video Studio is published as two images. The same API image is used to run a separate background worker.

| Image | Role |
| --- | --- |
| [`hythzx/video-downloader-web`](https://hub.docker.com/r/hythzx/video-downloader-web) | Vue 3 frontend served by Nginx |
| [`hythzx/video-downloader-api`](https://hub.docker.com/r/hythzx/video-downloader-api) | FastAPI backend, yt-dlp/FFmpeg processing, and background Worker |

**Deploy all three services: Web + API + Worker.** The existing Docker image names are intentionally retained for compatibility, even though the GitHub repository is now called **Frame-Video-Studio**.

## Features

- **Generic media extraction:** yt-dlp automatically detects compatible sites; dedicated adapters remain for X / Twitter, YouTube, and Bilibili.
- **Video downloads:** Choose an available resolution and save MP4 media. Where required, FFmpeg merges separate video and audio streams.
- **Audio extraction:** Export **MP3** or **M4A** when a usable audio track is available.
- **Subtitle downloads:** Save source-provided human or automatically generated captions as **SRT** or **VTT**.
- **Background video jobs:** Create video download tasks and check their status instead of keeping a single HTTP request open for the entire download.
- **Recent extraction history:** Browse recently parsed media and reopen an item.
- **Optional private X bookmarks:** Browse your own X bookmarks using server-side login cookies, filter video posts, and download selected video posts individually or in small ZIP batches.
- **Self-hosted storage:** SQLite metadata and finished background video downloads use a persistent Docker volume.

### Supported platforms

| Platform | Video | Audio | Subtitles |
| --- | --- | --- | --- |
| X / Twitter | Available MP4 formats | MP3 / M4A if an audio stream is available | If provided by the platform |
| YouTube | Resolution selection; FFmpeg merging when needed | MP3 / M4A | Human and automatic captions if available |
| Bilibili | Resolution selection; FFmpeg merging when needed | MP3 / M4A | If provided by the platform |
| Other yt-dlp sites | Compatible MP4, DASH and native HLS formats | Where audio is available | Where available |

The supported-sites list is not a guarantee: formats, permissions, connectivity and extractor changes can prevent downloads. Frame currently produces **MP4** files for compatible MP4, DASH and native HLS streams; other containers and incompatible codecs may fail. **DRM is not supported.**

## Quick start with Docker Compose

**Recommended:** Use the current Compose configuration from the repository. It contains the required **`web`**, **`api`**, and **`worker`** services and a shared persistent volume.

```bash
git clone https://github.com/hythzx/Frame-Video-Studio.git
cd Frame-Video-Studio

cp .env.example .env
docker compose pull
docker compose up -d
```

Open **http://127.0.0.1:8080** on the Docker host.

The Web UI binds to **localhost only** by default. For access from another device, update `BIND_ADDRESS` and configure HTTPS and authentication using a trusted reverse proxy.

Check services and logs:

```bash
docker compose ps
docker compose logs -f api worker web
```

Update to current images:

```bash
git pull origin main
docker compose pull
docker compose up -d
```

> **Important:** Use the repository's main [`compose.yaml`](https://github.com/hythzx/Frame-Video-Studio/blob/main/compose.yaml). The legacy `compose.deploy.yaml` does not include the background Worker or persistent data volume and is not suitable for the current asynchronous video workflow.

## Configuration

Edit the **server-local** `.env` file as needed.

| Variable | Default | Description |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Web interface bind address |
| `PORT` | `8080` | Web interface port |
| `MAX_DURATION_SECONDS` | `3600` | Maximum accepted video duration |
| `MAX_DOWNLOAD_MB` | `1024` | Configured maximum download size |
| `TWITTER_COOKIES` | Empty | Optional X login cookies; required for private bookmarks |
| `BILI_COOKIES` | Empty | Optional Bilibili login cookies |
| `FRAME_ACCESS_TOKEN` | Empty | Separate secret (at least 20 characters) protecting the private X bookmarks endpoints |

For private X bookmarks, configure your own account's `auth_token` and `ct0` under `TWITTER_COOKIES` and generate a separate Frame access token locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Store credentials only in the server's `.env` file. **Never commit credentials or share them in public logs or screenshots.** The X bookmarks integration uses unofficial web endpoints, may be rate-limited, and can stop working when X changes its website.

## How the containers work

```text
Browser
  │
  ▼
Web (Vue 3 + Nginx)
  │
  ▼
API (FastAPI + yt-dlp)
  ├── Media extraction and recent history
  ├── Audio and subtitle downloads
  └── Video download task → SQLite
                               │
                               ▼
                      Worker (yt-dlp + FFmpeg)
                               │
                               ▼
                      Persistent /data volume
                               │
                               ▼
                      Completed MP4 download
```

The **API** and **Worker** share the `frame-data` Docker volume. Keep that volume when updating containers; removing it deletes application data and stored downloads.

### Important current limitations

- **Only video** has been migrated to the background-task workflow. Audio, subtitles, and bookmark ZIP downloads still run through synchronous requests and may time out on slow connections.
- Video jobs currently support status polling and completed-file downloads; **SSE progress streaming, task cancellation/retry, and automatic cleanup of old task files are not yet implemented**.
- Private bookmark browsing is optional, read-only, and based on **unofficial X web interfaces**. It requires your own valid account cookies and does not guarantee continued availability.
- The application is intended for **personal, self-hosted use**. The `FRAME_ACCESS_TOKEN` protects only private bookmark endpoints, **not the entire site**. Add authentication, HTTPS, and appropriate rate controls before exposing it on another network.

## API overview

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/extract` | Media metadata, available formats, audio and captions |
| `POST` | `/api/tasks` | Create a background **video** download task |
| `GET` | `/api/tasks/{id}` | Query task status |
| `GET` | `/api/tasks/{id}/file` | Download a completed MP4 |
| `GET` | `/api/history` | Recent extraction history |
| `POST` | `/api/audio` | Download MP3 / M4A |
| `POST` | `/api/subtitle` | Download SRT / VTT |
| `GET` | `/api/bookmarks` | Optional private X bookmarks (access token required) |

For details, see the [project README](https://github.com/hythzx/Frame-Video-Studio/blob/main/README.md).

## Source, releases and license

- **Source code:** https://github.com/hythzx/Frame-Video-Studio
- **Docker Compose:** https://github.com/hythzx/Frame-Video-Studio/blob/main/compose.yaml
- **Build pipeline:** https://github.com/hythzx/Frame-Video-Studio/actions
- **Architectures:** `linux/amd64` and `linux/arm64`
- **Tags:** `latest` from the `main` branch, commit SHA tags, and semantic version tags for `v*` releases

**License: [MIT License](https://github.com/hythzx/Frame-Video-Studio/blob/main/LICENSE) · Copyright © 2026 hythzx.** You may use, modify, redistribute and use Frame Video Studio commercially subject to the MIT terms. Third-party dependencies retain their own licenses. Download only content that you have permission to access and save.

---

**Frame Video Studio — Save media from the platforms you love, in one place.**
