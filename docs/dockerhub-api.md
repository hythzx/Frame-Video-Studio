# Frame Video Studio — API

**Self-hosted yt-dlp-powered media extraction, download and processing API for many websites.**

[Frame Video Studio](https://github.com/hythzx/Frame-Video-Studio) has dedicated adapters for **X / Twitter, YouTube and Bilibili**, and also attempts other websites recognized by yt-dlp (where compatible MP4, DASH or native HLS streams are available). The backend is built with **FastAPI, yt-dlp and FFmpeg** and powers video format selection, audio extraction, subtitle downloads, download tasks, recent extraction history and an optional private X bookmarks viewer.

> This is the **backend image**. Deploy it alongside the [Web image](https://hub.docker.com/r/hythzx/video-downloader-web). The same API image also runs the dedicated background **Worker** service.

## Docker image

```bash
docker pull hythzx/video-downloader-api:latest
```

The API container listens on **port 8000**. GitHub Actions builds amd64 and arm64 images. The Docker Hub image name remains unchanged after the GitHub repository was renamed.

## Supported media features

| Platform | Video | Audio | Subtitles |
| --- | --- | --- | --- |
| **X / Twitter** | Download available MP4 formats | MP3 / M4A when an audio track is accessible | Where provided by X |
| **YouTube** | Resolution selection; DASH video/audio merging via FFmpeg | MP3 / M4A | Human and automatic captions where provided |
| **Bilibili** | Resolution selection; DASH video/audio merging via FFmpeg | MP3 / M4A | Where available |
| **Other yt-dlp sites** | Compatible MP4 / DASH / native HLS streams | Where available | Where available |

Actual formats depend on platform access, extractor compatibility, region and cookies. The supported-sites list does not guarantee operation, and output video is currently MP4-focused. The application does not bypass DRM.

## Recommended deployment

The current default Compose file runs **three services**: `web`, `api` and `worker`. API and Worker share a Docker volume for SQLite metadata, cover images and completed video files.

```bash
git clone https://github.com/hythzx/Frame-Video-Studio.git
cd Frame-Video-Studio
cp .env.example .env
docker compose pull
docker compose up -d
```

Visit **http://127.0.0.1:8080** on the Docker host. The default binding is local-only.

[View the current Compose configuration](https://github.com/hythzx/Frame-Video-Studio/blob/main/compose.yaml).

## Main API endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/extract` | Extract media metadata and available formats |
| `POST` | `/api/tasks` | Create an asynchronous **video** download job (HTTP 202) |
| `GET` | `/api/tasks/{id}` | Read video job status |
| `GET` | `/api/tasks/{id}/file` | Download a completed MP4 |
| `GET` | `/api/history` | Recent media extraction history |
| `GET` | `/api/youtube/channels` | Read a bounded page of YouTube channel uploads, Shorts, or stream archives |
| `POST` | `/api/audio` | Extract MP3 / M4A (**synchronous**) |
| `POST` | `/api/subtitle` | Export SRT / VTT (**synchronous**) |
| `POST` | `/api/download` | Legacy synchronous video download |
| `GET` | `/api/bookmarks` | Optional private X bookmarks (requires an access token) |
| `POST` | `/api/bookmarks/download` | Optional X bookmark video download / ZIP |

Example extraction:

```bash
curl -X POST http://127.0.0.1:8080/api/extract \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'
```

Create a video download task using a `format_id` returned by extraction:

```bash
curl -X POST http://127.0.0.1:8080/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","kind":"video","format_id":"FORMAT_ID_FROM_EXTRACT"}'
```

Video downloads run in the Worker and return a task ID immediately. Audio, subtitles and bookmark ZIP downloads are not yet migrated to this task system; long synchronous requests may still encounter timeouts.

## Environment and storage

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_DURATION_SECONDS` | `3600` | Maximum allowed media duration |
| `MAX_DOWNLOAD_MB` | `1024` | Download size limit |
| `TWITTER_COOKIES` | Unset | Optional X login cookies; required for bookmark browsing |
| `BILI_COOKIES` | Unset | Optional Bilibili login cookies |
| `FRAME_ACCESS_TOKEN` | Unset | Independent access key protecting private bookmark APIs |
| `WORKER_CONCURRENCY` | `2` | Background video task concurrency in the Worker |

Default `compose.yaml` mounts the named volume `frame-data` at `/data`. The API and Worker use this volume for SQLite and completed video files, with a smaller tmpfs for ephemeral work. Do not remove that volume unless intentionally deleting the data.

## Optional X bookmarks

The application can read **your own X bookmarks** using server-side `auth_token` and `ct0` cookies through an **unofficial X web interface**. This is not the official X API; its behavior and rate limits may change.

The bookmarks routes require `X-Frame-Token: <FRAME_ACCESS_TOKEN>`. Configure the cookies and a randomly generated Frame access token in your **server-local** `.env`. Do not place real cookies or tokens in Git, logs or screenshots. The access token only guards bookmark routes: protect the **entire application** with HTTPS and authentication before exposing it beyond localhost.

[See the setup guide](https://github.com/hythzx/Frame-Video-Studio/blob/main/README.md).

## License

[MIT License](https://github.com/hythzx/Frame-Video-Studio/blob/main/LICENSE) · Copyright (c) 2026 赵远（Nick zhao）. Third-party components follow their own licenses.

## Source and releases

- **GitHub:** https://github.com/hythzx/Frame-Video-Studio
- **Compose:** https://github.com/hythzx/Frame-Video-Studio/blob/main/compose.yaml
- **Web image:** `hythzx/video-downloader-web`

GitHub Actions publishes `latest` for the default branch, commit SHA tags, and semantic version tags for `v*` releases on **linux/amd64** and **linux/arm64**.

Use this software only for media you are authorized to download.
