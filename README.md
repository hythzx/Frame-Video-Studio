# Frame — Twitter Video Downloader

A polished, self-hosted video downloader for **X / Twitter, YouTube and Bilibili**, for personal use. Built with Vue 3, FastAPI, yt-dlp and Docker Compose. No database or analytics. An optional personal X cookie-backed bookmarks browser requires explicit access control.

## Features
- Paste a public post/video URL from X/Twitter, YouTube or Bilibili; extract title, thumbnail, duration and available video resolutions.
- X: download progressive MP4 formats with both audio and video.
- YouTube / Bilibili: download DASH video+audio streams and merge them into MP4 with ffmpeg.
- Dark, responsive Chinese-language interface.
- Temporary server-side downloads; files are deleted after transfer.
- Strict domain/post URL validation, format allowlist and maximum duration/size settings.

> **Limitations:** Public posts only; extraction depends on yt-dlp and may stop working if X changes its APIs or blocks anonymous requests. HLS-only and split audio/video formats are not supported in this lightweight first version. The download request buffers the full file in the browser. Only download media you're entitled to save. Never expose this service without access control.

## Deploy with Compose

```bash
git clone https://github.com/hythzx/Twitter-Video-Downloader.git
cd Twitter-Video-Downloader
cp .env.example .env
docker compose up -d --build
```

Open **http://localhost:8080** on the Docker host. The default bind address is `127.0.0.1`, so it is **not exposed to the LAN or internet**.

To expose it through a trusted reverse proxy, change `BIND_ADDRESS` in `.env` to the appropriate interface and add authentication + HTTPS at your reverse proxy. Do not expose it publicly without authentication.

### Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Docker port bind address |
| `PORT` | `8080` | Host port |
| `MAX_DURATION_SECONDS` | `3600` | Maximum allowed video length |
| `MAX_DOWNLOAD_MB` | `1024` | Approximate download limit |
| `TWITTER_COOKIES` | *(empty)* | Optional `auth_token=<v>; ct0=<v>` X login cookies; enables restricted/sensitive posts, sent as a header to X |
| `BILI_COOKIES` | *(empty)* | Optional Bilibili login cookies (`SESSDATA=<v>; bili_jct=<v>` at minimum) for premium quality and better CDNs |

The API stores working files in an in-memory `/tmp` tmpfs sized at 2 GB. Make sure Docker has sufficient RAM for your intended video size; for larger videos, replace the tmpfs with a bounded writable volume.

## Local development

Backend (requires Python 3.12 and ffmpeg):

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend (requires Node.js 24):

```bash
cd frontend
npm install
npm run dev
```

The Vite development server proxies `/api` to `http://localhost:8000`.

## API

- `GET /api/health`: health check.
- `POST /api/extract`: JSON `{"url":"https://x.com/user/status/123..."}`.
- `POST /api/download`: JSON `{"url":"...", "format_id":"..."}`, returning `video/mp4`.

The server re-extracts the post and verifies the selected format before downloading. MP4 download may take time for large files; the browser currently holds the whole result in memory.

## Updating

```bash
git pull
docker compose up -d --build
```

For extractor compatibility, rebuild regularly so that a current yt-dlp release gets installed. If you need private posts, login cookies, HLS merge support, asynchronous job queues or large-file streaming, those are separate features and intentionally not included by default.


## Docker Hub automatic publishing

GitHub Actions workflow: `.github/workflows/docker-publish.yml`

Configure GitHub repository Actions variable `DOCKERHUB_USERNAME` and Actions secret `DOCKERHUB_TOKEN`. These are the same names used by `data-analysis-agent`; secrets and variables are repository-scoped unless explicitly configured at the organization level.

Pushing to `main` builds and publishes:
- `<DOCKERHUB_USERNAME>/video-downloader-api:latest`
- `<DOCKERHUB_USERNAME>/video-downloader-web:latest`

Version tags such as `v1.0.0` also publish `1.0.0` and `1.0` tags. Both images are built for amd64 and arm64. You can run the workflow manually from the GitHub Actions tab.

### Deploy prebuilt images

```bash
cp .env.example .env
# Set DOCKERHUB_USERNAME=<your Docker Hub username> in .env
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

To upgrade prebuilt images:

```bash
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

Set `IMAGE_TAG=1.0.0` in your `.env` to pin an immutable release tag rather than following `latest`. Do not put the Docker Hub access token in `.env`; it is only required by GitHub Actions. The original `compose.yaml` is retained for local builds.

## Audio and subtitles

After pasting a supported X/Twitter, YouTube or Bilibili video link, the result panel offers up to three download tabs:

- **Video**: existing MP4 resolution selection.
- **Audio**: extract or convert the best available audio as MP3 or M4A using FFmpeg.
- **Subtitles**: choose one of the source's available human-written or automatic subtitle tracks, and save as SRT or VTT. Availability depends on the platform; this feature does not transcribe or translate speech.

Additional API endpoints:

```http
POST /api/audio
Content-Type: application/json

{"url":"https://www.youtube.com/watch?v=VIDEO_ID","audio_format":"mp3"}
```

```http
POST /api/subtitle
Content-Type: application/json

{"url":"https://www.youtube.com/watch?v=VIDEO_ID","language":"en","automatic":false,"subtitle_format":"srt"}
```

`/api/extract` now also returns `audio_available` and `subtitles` (objects with `language` and `automatic`). Only request a subtitle track from that list. Result files are served as attachments and temporary files are deleted after transfer. Large downloads still run synchronously; progress tracking is not yet implemented.


## Private X bookmarks (cookie-based; optional)

Frame includes a **My Bookmarks** menu to browse your own X bookmarks without the paid X developer API. It uses the unofficial [Twikit](https://github.com/d60/twikit) web client for **read-only** bookmark retrieval, then reuses yt-dlp to download video media.

> **Important:** This integration is unofficial and can break when X changes its internal API. X may rate-limit requests or restrict accounts. Read only your own bookmarks, avoid excessive requests, and use it subject to X's terms. No X password, account login automation or third-party cookie storage is involved.

### Setup

1. Log into **your own** X account in a browser. Using its developer tools on `x.com`, find the `auth_token` and `ct0` cookie **values** (optionally `twid`). Treat them like a login password. Do not paste them into a public issue, chat, screenshot, or GitHub file.
2. In your **server-local** `.env` file, set `TWITTER_COOKIES` to `auth_token=<value>; ct0=<value>`. The existing yt-dlp download functions will reuse these cookies.
3. Generate a separate private app access key on your own machine:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Save the generated value as `FRAME_ACCESS_TOKEN` in `.env`. At least 20 characters are required. **This is not your X Cookie**.
4. Redeploy: `docker compose -f compose.deploy.yaml up -d` (or `docker compose up -d` if using the local image setup).
5. Open Frame → **我的收藏**, enter the `FRAME_ACCESS_TOKEN`, and click **读取收藏**.

The browser sends only the Frame access key in the `X-Frame-Token` request header; X cookies stay on the server. The access key is kept in page memory and is not written to localStorage. The server does not persist a bookmarks database.

### Available operations

- Manually fetch one page at a time (20 bookmarks per page); next/previous cursor pagination.
- View original Post, author, text and media thumbnail; filter posts containing video/animated GIF.
- Download one video as MP4, or up to **5 selected video Posts** as a ZIP. A ZIP may include `failed-ids.txt` for items that were inaccessible or had no downloadable MP4.
- Requests are sequential and limited; the app does **not** automatically crawl every bookmark or change any bookmark.
- Batch downloads are synchronous and use server RAM-backed `/tmp` storage. Archive output is limited to 600 MiB and large videos may fail. This is not a persistent download task queue.

### Private bookmarks API

All private routes require `X-Frame-Token: <FRAME_ACCESS_TOKEN>`. Without a configured key, these endpoints are disabled.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/bookmarks?count=20&cursor=...` | Paginated private bookmarks (cursor optional) |
| `POST /api/bookmarks/download` | JSON `{"ids":["1234567890123456789"]}`; one item → MP4; multiple items → ZIP |

**Security:** Do not expose Frame to the public internet without HTTPS, authentication and rate controls. `FRAME_ACCESS_TOKEN` protects the new bookmarks endpoints; the existing general downloader endpoints are not protected by this key, so protect the **whole site** at your reverse proxy if you expose it beyond localhost. Do not use browser network traffic export files containing Cookie headers as issues or debugging attachments.
