# Frame Video Studio — Web

![Frame Video Studio icon](https://raw.githubusercontent.com/hythzx/Frame-Video-Studio/main/frontend/public/favicon.svg)

**A responsive self-hosted downloader for X / Twitter, YouTube and Bilibili.**

This is the **Web image** of [Frame Video Studio](https://github.com/hythzx/Frame-Video-Studio), built with **Vue 3 and Nginx**. Paste a link, explore available media formats, and save videos, audio and subtitle files from one unified interface.

> The Web image requires the companion `hythzx/video-downloader-api` service. Asynchronous video downloads also require a **Worker** using that same API image.

## Docker image

```bash
docker pull hythzx/video-downloader-web:latest
```

The container serves HTTP on **port 80**. The Docker Hub image name remains unchanged after the GitHub repository was renamed.

## Features

- **Three platforms:** X / Twitter, YouTube and Bilibili, detected from the pasted link.
- **Video:** available resolutions with MP4 download and server-side DASH merging where needed.
- **Audio:** MP3 and M4A extraction.
- **Subtitles:** SRT or VTT from available human or automatic caption tracks.
- **Video download jobs:** background Worker downloads with status polling and browser-native file downloads after completion.
- **Recent history:** records of the latest media extractions, with an option to reselect an item.
- **My X Bookmarks (optional):** browse personal bookmarks using server-side cookies, filter video posts and select up to five posts for ZIP download.
- Dark, responsive interface with no third-party analytics.

Video tasks are asynchronous, but audio, subtitles and X bookmark ZIP downloads still use synchronous API calls in the current version. Real-time SSE progress, cancel/retry UI and task-expiry cleanup are not yet available.

## Quick start

The recommended installation uses the **current three-service Compose stack**, including `web`, `api`, `worker` and the persistent `frame-data` volume:

```bash
git clone https://github.com/hythzx/Frame-Video-Studio.git
cd Frame-Video-Studio
cp .env.example .env
docker compose pull
docker compose up -d
```

Open **http://127.0.0.1:8080** on the Docker host.

[Current Docker Compose file](https://github.com/hythzx/Frame-Video-Studio/blob/main/compose.yaml).

### How Web connects to the backend

The included Nginx config forwards `/api/` to the internal Docker DNS name `api:8000`. The service must be reachable as `api` on the Compose network. This is configured at the Nginx layer, not through a browser-side `API_URL` environment variable.

Keep `TWITTER_COOKIES`, `BILI_COOKIES` and `FRAME_ACCESS_TOKEN` in the backend/server environment; they are **not** passed to the Web image.

## Deployment settings

| Variable | Default | Description |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface used for the Web port |
| `PORT` | `8080` | Published Web port |
| Container port | `80` | Internal Nginx HTTP port |

For remote access, configure HTTPS, site-wide authentication and rate limiting at your reverse proxy.

## Troubleshooting

- **Page loads but API calls fail:** check the `api` container and Docker DNS/network connection.
- **Video tasks remain queued:** check the `worker` service and the shared `frame-data` volume.
- **502 Bad Gateway:** inspect `docker compose logs api web`.
- **Media unavailable:** site restrictions, expired cookies or yt-dlp extractor changes can prevent downloads.
- **Clipboard not available:** browser clipboard permissions can require HTTPS or localhost.

Check service logs:

```bash
docker compose ps
docker compose logs -f api worker web
```

To update:

```bash
git pull origin main
docker compose pull
docker compose up -d
```

## Source and releases

- **GitHub:** https://github.com/hythzx/Frame-Video-Studio
- **API image:** `hythzx/video-downloader-api`
- **Compose:** https://github.com/hythzx/Frame-Video-Studio/blob/main/compose.yaml

GitHub Actions builds images for **linux/amd64** and **linux/arm64** and publishes `latest`, commit SHA tags and version tags on releases.

Download only media you are permitted to save. The private X bookmarks feature depends on unofficial web endpoints that may change or enforce rate limits.
