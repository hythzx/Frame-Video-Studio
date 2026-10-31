# Frame Video Studio

**A self-hosted media downloader powered by yt-dlp — video, audio and subtitles from many websites.**

**Frame** 是一款基于 **yt-dlp** 的个人自托管媒体下载工具。粘贴公网 HTTPS 媒体页面链接，由 yt-dlp 自动判断站点与媒体格式，再选择画质、下载视频或提取音频、字幕。**X / Twitter、YouTube、哔哩哔哩**保留专门适配，同时尝试解析 yt-dlp 支持的其他网站（如 Vimeo、TikTok、Instagram 等）。并非支持列表中的所有网站都保证可用。

> 本项目最初以 Twitter 视频下载为核心，现已扩展为多平台媒体下载工具，仓库已更名为 **Frame-Video-Studio**。Docker Hub 镜像名暂时仍为 `hythzx/video-downloader-api` 和 `hythzx/video-downloader-web`，以保持现有部署兼容。

## Site support / 网站兼容性

| 平台 | 视频下载 | 音频提取 | 字幕下载 | 其他能力 |
| --- | --- | --- | --- | --- |
| **X / Twitter** | 支持可获取的 MP4 视频流 | MP3、M4A（有可用音轨时） | 原平台提供时可用 | 可选：使用服务器端 Cookie 浏览自己的书签、选择下载 |
| **YouTube** | 选择画质；DASH 音视频流由 FFmpeg 合并为 MP4 | MP3、M4A | 人工字幕、自动字幕（源站提供时） | 支持常见视频与 Shorts 链接 |
| **哔哩哔哩 / Bilibili** | 选择画质；DASH 音视频流由 FFmpeg 合并为 MP4 | MP3、M4A | 原平台提供时可用 | 可配置登录 Cookie 访问需要登录的画质 |
| **其他 yt-dlp 网站** | 自动识别可用的 MP4、DASH、原生 HLS（m3u8）媒体流 | 有音轨时可用 | 源站提供时可用 | 依赖对应提取器、媒体编码与服务器访问权限 |

目前统一视频选择器以 **MP4 成品**为目标，支持兼容的 MP4 直链、DASH 音视频合并和原生 HLS（m3u8）流；WebM、特殊编码、DRM 和其他无法合并/封装为 MP4 的格式仍可能不受支持。实际分辨率、音轨、字幕取决于网站、登录态与 yt-dlp 提取器。完整的 [yt-dlp 提取器列表](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) 不等于所有站点都可保证下载成功。

## Features / 核心功能

- **通用媒体入口**：接受公网 HTTPS 媒体页面链接，由 yt-dlp 自动识别网站；展示标题、封面、作者、时长和兼容的下载格式。
- **视频下载**：选择画质，必要时由 FFmpeg 合并音视频；下载完成后由浏览器保存 MP4。
- **纯音频下载**：提取或转码为 **MP3 / M4A**。
- **字幕下载**：从源站提供的人工字幕或自动字幕中选择语言，导出 **SRT / VTT**；不进行语音识别或自动翻译。
- **视频异步任务（基础版）**：FastAPI 创建任务，独立 Worker 执行下载，前端轮询状态；成品存储在 Docker 持久卷，避免长时间占用一次 HTTP 下载请求。
- **解析历史**：使用 SQLite 保存最近最多 **200 条解析记录**，可以从历史页面重新选择视频。
- **X 私人书签（可选）**：使用服务器配置的 X Cookie，以非官方网页接口分页读取自己的书签，支持视频筛选、单条下载以及最多 **5 条**视频打包 ZIP。

- **YouTube 频道作品库**：粘贴博主个人主页，按「视频 / Shorts / 直播回放」分类逐页浏览，选择作品（每次最多 20 条）交给 Worker 异步下载，支持自动选择当前可用的最佳兼容 MP4 格式。

### YouTube channel library / 频道作品下载

在 Web 页面选择 **「频道作品」**，输入 YouTube 频道主页：

```text
https://www.youtube.com/@creator
https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
https://www.youtube.com/c/CreatorName
https://www.youtube.com/user/CreatorName
```

点击 **「加载作品」** 可查看该博主当前公开可访问的内容；分别切换「视频」「Shorts」「直播回放」栏目，并点击 **「加载更多作品」** 向后浏览，直到没有更多条目。

页面右侧提供 **「标题语言」** 选择：**简体中文（默认）**、繁體中文、English、原始语言／自动。切换后会从第一页重新读取当前栏目；后端通过 yt-dlp 的 `youtube:lang` 请求对应语言的标题元数据（选择原始语言时不指定该参数）。翻译取决于 YouTube 实际返回的数据，不保证所有视频都有对应语言的标题。该设置**只影响频道列表元数据**，不会翻译视频或改变下载地址、视频 ID。

在列表中点击单个 **「下载」**，或勾选最多 **20 条**作品后点击 **「下载已选」**。提交后立即创建 `/api/tasks` 任务（自动选择当前最佳兼容 MP4 格式），由独立 Worker 下载与合并；无需保持页面打开。任务状态、失败原因及已完成文件的保存入口显示在频道页面底部。最近任务索引保存在当前浏览器的 localStorage 中，下载文件仍保存在服务器持久卷并按服务端 TTL 清理。

**注意：** 频道是分页浏览，不会一键抓取全频道并自动无限量排队；公开视频可能因删除、私有、会员限制、年龄限制或地区政策而不可读取或下载。频道列表采用 yt-dlp 的 flat-playlist 元信息模式，不代表每条视频必然能成功下载。

**当前边界：** 异步任务目前只接入**视频**。纯音频、字幕和书签批量下载仍走同步接口，可能受到网络和代理超时影响。当前任务实现提供排队、状态查询、Worker 重启后的中断任务重新排队及已完成视频文件下载；**SSE 实时进度、取消/重试接口、自动过期清理尚未实现**。请勿将这些规划中的能力视为现有功能。

## Quick start / 快速部署

要求：Docker Engine 和 Docker Compose Plugin。默认从 Docker Hub 拉取预构建镜像，**无需在部署服务器安装 Node.js、Python 或手动构建 Docker 镜像**。

```bash
git clone https://github.com/hythzx/Frame-Video-Studio.git
cd Frame-Video-Studio

cp .env.example .env
docker compose pull
docker compose up -d
```

浏览器在部署主机访问 **http://127.0.0.1:8080**。

默认仅绑定 `127.0.0.1`，不会直接向局域网或互联网暴露。若从另一台设备访问，请在 `.env` 中调整 `BIND_ADDRESS`，并通过受保护的反向代理配置 HTTPS 和访问认证。

当前默认的 `compose.yaml` 包含三个服务：

| 服务 | 镜像 | 职责 |
| --- | --- | --- |
| `web` | `hythzx/video-downloader-web` | Vue 3 静态界面与 Nginx 反向代理 |
| `api` | `hythzx/video-downloader-api` | FastAPI、媒体解析、任务 API |
| `worker` | `hythzx/video-downloader-api` | 独立执行视频下载任务和 FFmpeg 处理 |

`api` 与 `worker` 共用 `frame-data` Docker Volume，将 SQLite 数据库、封面与异步视频下载产物保存在 `/data`；小型临时文件使用容器内的 `/tmp`。**不要删除 `frame-data` 卷，除非确实要清除相关数据和文件。**

> **Compose 文件说明：** 请优先使用仓库根目录的 `compose.yaml`。现有 `compose.deploy.yaml` 仍是旧版双服务部署示例，未包含 Worker 和持久数据卷，不适合当前异步视频下载流程。

### Environment variables / 环境变量

在服务器本地编辑 `.env`（不要提交真实 Cookie 或密钥）：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Web 端口绑定地址 |
| `PORT` | `8080` | Web 端口 |
| `MAX_DURATION_SECONDS` | `3600` | 视频时长限制 |
| `MAX_DOWNLOAD_MB` | `1024` | 下载大小限制 |
| `TWITTER_COOKIES` | 空 | 可选，X 登录 Cookie；私人书签功能需配置 `auth_token` 和 `ct0` |
| `BILI_COOKIES` | 空 | 可选，Bilibili 登录 Cookie |
| `FRAME_ACCESS_TOKEN` | 空 | 启用私人书签访问所需的独立密钥，至少 20 个字符 |
| `WORKER_CONCURRENCY` | `2`（Worker 内默认） | 后台视频任务执行线程数 |

服务器使用 Cookie 时，请限制 `.env` 文件访问权限，例如 `chmod 600 .env`。使用任何登录 Cookie 都可能遇到过期、访问限制或风控。

### Update / 更新

```bash
git pull origin main
docker compose pull
docker compose up -d
```

检查状态和日志：

```bash
docker compose ps
docker compose logs -f api worker web
```

## How it works / 下载流程

```text
Vue 3 + Nginx
      │
      ├── 视频链接 ──→ FastAPI 解析（yt-dlp）
      │                     │
      │                     └── 创建视频任务 → SQLite → Worker
      │                                                │
      │                                           yt-dlp + FFmpeg
      │                                                │
      │                                        /data/files（持久化）
      │                                                │
      │                                        浏览器下载 MP4
      │
      ├── 纯音频 / 字幕 ──→ 同步处理并返回媒体文件
      ├── 历史记录 ─────→ SQLite（最近 200 条解析）
      ├── X 我的收藏 ───→ Cookie + 非官方 X 接口
      └── YouTube 频道 ──→ yt-dlp 平铺频道列表 → 单条/批量创建视频任务
```

**为什么视频使用任务模式？** 下载与音视频合并可能持续较长时间。前端首先提交任务，再查询 `queued / resolving / downloading / ready / failed` 等状态；完成后请求文件，减少长时间等待单个 HTTP 响应导致超时的风险。

当前前端仍需保持页面打开以等待自动保存，尚未提供完整的任务中心和后台进度通知。设计演进可参阅 [下载任务与超时治理设计](docs/download-task-architecture.md)（部分阶段已实现，文档中仍含早期设计基线）。

## Optional: My X Bookmarks / 我的 X 收藏

这一功能**仅面向你自己的账号**，不是其他平台的收藏同步能力，也不需要购买 X 官方开发者 API。它通过服务器端 X Cookie 调用非官方网页接口，属于可选功能，**不保证 X 修改接口后仍可正常工作**。

1. 在自己的浏览器登录 `x.com`，从浏览器开发者工具获取当前账号的 `auth_token` 和 `ct0` Cookie 值。**不要将 Cookie 发送到聊天、Issue 或 GitHub 仓库。**
2. 在服务器本地 `.env` 中配置 `TWITTER_COOKIES`，例如：`auth_token=<value>; ct0=<value>`。
3. 在自己的设备上生成 Frame 的独立访问密钥：

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   将结果保存到 `.env` 的 `FRAME_ACCESS_TOKEN`；重建容器：`docker compose up -d`。
4. 进入 Web 页面 **「我的收藏」**，输入 **Frame 访问密钥（不是 X Cookie）**，手动读取书签并选择下载。

Cookie 仅配置在服务端环境变量中；浏览器只发送 Frame 的独立访问密钥。可分页浏览书签、只显示视频推文、单独下载 MP4 或最多选 5 条打包 ZIP。功能不会自动遍历、定时同步或修改 X 书签。

非官方 X 接口可能限流或失效；建议低频使用。Frame 访问密钥只保护私人书签相关接口，**不等于整个网站已开启登录鉴权**。

## API / 主要接口

| 方法 | 接口 | 作用 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务健康检查 |
| `POST` | `/api/extract` | 提取标题、封面、可用画质、音频及字幕信息 |
| `POST` | `/api/tasks` | **视频**下载任务创建（202，频道作品可用 `format_id: best`） |
| `GET` | `/api/tasks/{id}` | 查看任务状态 |
| `GET` | `/api/tasks/{id}/file` | 下载已完成任务的 MP4 |
| `GET` | `/api/history` | 查询最近解析历史 |
| `GET` | `/api/youtube/channels` | 频道视频、Shorts、直播回放分页列表（`url`、`section`、`start`、`lang`；默认 `zh-CN`） |
| `POST` | `/api/audio` | 纯音频下载（MP3 / M4A） |
| `POST` | `/api/subtitle` | 字幕下载（SRT / VTT） |
| `GET` | `/api/bookmarks` | Cookie 读取 X 私人书签（需 Frame Token） |
| `POST` | `/api/bookmarks/download` | X 收藏视频批量下载（需 Frame Token） |
| `POST` | `/api/download` | 旧版同步 MP4 下载接口，暂时保留兼容 |

视频任务请求示例：

```bash
curl -X POST http://127.0.0.1:8080/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","kind":"video","format_id":"FORMAT_ID_FROM_EXTRACT"}'
```

请先通过 `/api/extract` 获取实际可用的 `format_id`，不要将示例值直接作为真实格式。音频与字幕接口的参数见 [API Docker Hub README](docs/dockerhub-api.md)。

## Development / 本地开发

- **Backend**：Python 3.12、FastAPI、yt-dlp、FFmpeg、SQLite。
- **Frontend**：Vue 3、Vite 6、Node.js 24。
- **Deployment**：Docker Compose、Nginx，两个发布镜像（三个运行服务）。

```bash
# Backend (ensure FFmpeg is installed)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

Vite 开发服务器将 `/api` 代理到 `http://localhost:8000`。异步视频任务需要另外启动 Worker 并配置 API 与 Worker 共用的可写 `FRAME_DATA_DIR`；仅启动上述两个开发进程不会执行排队下载。

## Docker Hub & CI/CD

GitHub Actions 工作流：[docker-publish.yml](.github/workflows/docker-publish.yml)。推送 `main`、创建 `v*` 版本标签或手动运行，可构建并发布下列镜像：

- `hythzx/video-downloader-api`
- `hythzx/video-downloader-web`

构建目标为 `linux/amd64` 和 `linux/arm64`。构建流程使用 GitHub Actions 变量 `DOCKERHUB_USERNAME` 和 Secret `DOCKERHUB_TOKEN`；这些凭据只供发布镜像使用，不需要配置在部署服务器中。

Docker Hub 介绍文档：[API](docs/dockerhub-api.md) · [Web](docs/dockerhub-web.md)。

## License / 开源协议

本项目以 **MIT License** 发布，版权声明为 **Copyright (c) 2026 赵远（Nick zhao）**。你可以在保留原版权及许可声明的前提下使用、修改、分发及用于商业项目；软件按原样提供，不作担保。

完整协议见 [LICENSE](LICENSE)。项目所使用的 yt-dlp、Vue 等第三方依赖分别遵循其自身许可证。

## Limitations & security / 使用须知

- 项目以个人自托管为主。下载是否成功受网站结构、网络、Cookie、地区、付费权限和 yt-dlp 支持情况影响。通用模式不是对所有网站、格式或 HLS/DRM 内容的可用性保证。
- 通用 URL 模式会检查 HTTPS 链接、禁止本机/IP 直连及非公网 DNS 地址，并检查 yt-dlp 常规网络请求和封面重定向；这不是完整的 SSRF 沙箱。**如需部署在不可信网络，仍必须使用容器级出站防火墙/网络隔离，并保护站点访问。**
- 只下载你有权保存的媒体；本工具不用于绕过 DRM 或未经授权的内容访问。
- 对外开放时，请为**整个站点**设置 HTTPS、身份认证和必要的请求限制。私人书签使用的 `FRAME_ACCESS_TOKEN` 不保护其他普通下载、历史及任务接口。
- 不要在日志、浏览器调试数据、公开仓库或求助截图中暴露 X/Bilibili Cookie。
- 异步视频任务会持久存储完成文件，现阶段未实现自动清理。请关注 Docker Volume 的磁盘占用。同步音频、字幕与书签 ZIP 仍依赖临时目录。

---

**Frame Video Studio** — One place to save the media you want to keep.
