# Frame Video Studio — Web

**A clean, responsive video downloader interface for X / Twitter, YouTube and Bilibili.**

This is the frontend image of [Frame Video Studio](https://github.com/hythzx/Twitter-Video-Downloader). The Vue 3 app is compiled into static assets and served by **Nginx**. It offers platform-aware link entry, video metadata and thumbnail preview, resolution selection and a download button.

> This is the **Web image only**. It needs the companion **video-downloader-api** service to extract and download media.

## Image

```bash
docker pull <dockerhub-username>/video-downloader-web:latest
```

Replace `<dockerhub-username>` with the image publisher's Docker Hub username. The container listens on **port 80**.

## Highlights

- One interface for **X / Twitter, YouTube and Bilibili**.
- Automatic source identification from pasted video links.
- Responsive dark UI, with video thumbnail, title, duration and format selection.
- Download requests are handled by the API; no login or database is required by the frontend.
- Built as a static Vue app and served by lightweight Nginx.

## Recommended deployment

The web image's Nginx configuration proxies requests from `/api/` to a Docker network hostname named **`api`** on port **8000**. For that reason, **the recommended setup is the repository's two-service Compose deployment**:

```bash
git clone https://github.com/hythzx/Twitter-Video-Downloader.git
cd Twitter-Video-Downloader
cp .env.example .env
# Edit .env and set DOCKERHUB_USERNAME=<dockerhub-username>
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

Then open:

```text
http://localhost:8080
```

By default the frontend is bound to `127.0.0.1:8080` on the Docker host. Change `BIND_ADDRESS` and `PORT` in `.env` as needed, and add HTTPS and access control before exposing it to other networks.

## Docker Compose example

The following example shows how the two images are wired together:

```yaml
services:
  api:
    image: <dockerhub-username>/video-downloader-api:latest
    restart: unless-stopped
    environment:
      MAX_DURATION_SECONDS: "3600"
      MAX_DOWNLOAD_MB: "1024"
    read_only: true
    tmpfs:
      - /tmp:size=2g,mode=1777

  web:
    image: <dockerhub-username>/video-downloader-web:latest
    restart: unless-stopped
    depends_on:
      - api
    ports:
      - "127.0.0.1:8080:80"
```

The complete [production Compose file](https://github.com/hythzx/Twitter-Video-Downloader/blob/main/compose.deploy.yaml) also includes additional container security settings.

**Important:** Running the web container alone can serve the static page, but extracting and downloading videos will fail without the `api` service. The API hostname is configured in the image's Nginx configuration; it is **not** a runtime `API_URL` environment variable.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| Container port | `80` | HTTP frontend listener |
| `PORT` (Compose) | `8080` | Published host port |
| `BIND_ADDRESS` (Compose) | `127.0.0.1` | Host interface exposed |
| `IMAGE_TAG` (Compose) | `latest` | Tag selected for both images |

Video extraction settings and optional platform cookies belong to the **API service**, not this web container.

## Troubleshooting

- **Web page loads but extraction fails:** Confirm the API service is running and reachable as `api:8000` from the web container's network.
- **502 Bad Gateway:** Check `docker compose -f compose.deploy.yaml logs api web` and verify the API health check.
- **Clipboard paste unavailable:** Browser clipboard APIs can require a secure context (HTTPS or localhost). Paste directly into the field if needed.
- **Video unavailable:** The backend may be limited by platform access rules or the available yt-dlp extractor formats.

## Update

```bash
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

For reproducible releases, set `IMAGE_TAG` to a published version, such as `1.0.0`, rather than always tracking `latest`.

## Tags and source

GitHub Actions publishes `latest` from `main`, commit SHA tags, and semantic version tags from `v*` Git tags. Images are built for **linux/amd64** and **linux/arm64**.

**Source:** https://github.com/hythzx/Twitter-Video-Downloader

**Companion image:** `<dockerhub-username>/video-downloader-api`
