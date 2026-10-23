# Frame Video Downloader — API

**Self-hosted video extraction and download API for X / Twitter, YouTube and Bilibili.**

This is the backend image of [Frame Video Studio](https://github.com/hythzx/Twitter-Video-Downloader). Built with **FastAPI, yt-dlp and FFmpeg**, it extracts public video metadata, lists available resolutions, downloads the selected format and merges separate audio/video streams when supported.

> This is the **API image only**, not a web interface. For the complete UI, deploy it together with \`video-downloader-web\`.

## Image

```bash
docker pull <dockerhub-username>/video-downloader-api:latest
```

Replace `<dockerhub-username>` with the image publisher's Docker Hub username. The container listens on **port 8000**.

**Supported platforms**

| Platform | Processing |
| --- | --- |
| X / Twitter | Extracts supported downloadable MP4 video formats |
| YouTube | Selects DASH video and audio streams and merges with FFmpeg |
| Bilibili | Selects DASH video and audio streams and merges with FFmpeg |

Availability depends on yt-dlp, region, platform restrictions, access rights and whether a compatible media format is provided.

## Recommended deployment

The API is designed to work with the companion web image over a private Compose network. Use the repository's production Compose file:

```bash
git clone https://github.com/hythzx/Twitter-Video-Downloader.git
cd Twitter-Video-Downloader
cp .env.example .env
# Edit .env and set DOCKERHUB_USERNAME=<dockerhub-username>
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

Visit `http://localhost:8080` on the Docker host. The default deployment binds the web port to `127.0.0.1` and does not publish the API port.

## Standalone API example

For local testing only:

```bash
docker run --rm \
  --name frame-api \
  --read-only \
  --tmpfs /tmp:rw,size=2g,mode=1777 \
  -p 127.0.0.1:8000:8000 \
  -e MAX_DURATION_SECONDS=3600 \
  -e MAX_DOWNLOAD_MB=1024 \
  <dockerhub-username>/video-downloader-api:latest
```

Test the health endpoint:

```bash
curl http://127.0.0.1:8000/api/health
```

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/extract` | Extract metadata and available format IDs |
| `POST` | `/api/download` | Download a selected format as an MP4 response |

**Extract example**

```bash
curl -X POST http://127.0.0.1:8000/api/extract \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=YOUR_VIDEO_ID"}'
```

The response includes `platform`, `id`, `title`, `thumbnail`, `duration`, `uploader`, `url`, and a `formats` array. Use a returned `formats[].id` to request a download:

```bash
curl -L -X POST http://127.0.0.1:8000/api/download \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=YOUR_VIDEO_ID","format_id":"FORMAT_ID_FROM_EXTRACT"}' \
  --output video.mp4
```

## Environment variables

| Name | Default | Notes |
| --- | --- | --- |
| `MAX_DURATION_SECONDS` | `3600` | Reject videos exceeding this duration |
| `MAX_DOWNLOAD_MB` | `1024` | Maximum configured output size in MiB |
| `TWITTER_COOKIES` | Unset | Optional X cookies; keep credentials secret |
| `BILI_COOKIES` | Unset | Optional Bilibili cookies; keep credentials secret |
| `PYTHONUNBUFFERED` | `1` in Compose | Unbuffered Python logs |

Some platforms may require valid login cookies or additional platform verification. Cookies do not guarantee access and should never be committed to Git.

## Security and limitations

- Intended for **personal use** and authorized media only.
- Do not expose the API publicly without an authentication layer, rate limiting and HTTPS.
- Downloads are written to a temporary directory and removed after the response is sent.
- Large files may require substantial temporary storage and time for audio/video merging. The supplied Compose configuration uses a 2 GiB `/tmp` tmpfs.
- Extraction can fail when a platform changes its behavior or blocks the server's IP.
- The service does not contain a database or permanent video library.

## Tags and source

GitHub Actions publishes `latest` from `main`, commit SHA tags, and semantic version tags from `v*` Git tags. Images are built for **linux/amd64** and **linux/arm64**.

**Source:** https://github.com/hythzx/Twitter-Video-Downloader

**Deployment reference:** [compose.deploy.yaml](https://github.com/hythzx/Twitter-Video-Downloader/blob/main/compose.deploy.yaml)
