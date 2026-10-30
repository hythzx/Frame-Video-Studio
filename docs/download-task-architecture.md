# Frame Video Studio — 下载任务与超时治理设计

状态：**设计提案（尚未实施）**  
适用范围：X/Twitter、YouTube、Bilibili；视频、纯音频、字幕、书签批量下载  
目标：解除长时间 HTTP 请求耦合，实现进度、重试、断点续传、文件持久化和统一任务管理。

## 1. 现状与问题定位

代码基线（2026-10-30）：

- 前端 `frontend/src/App.vue`：`fetch('/api/download')`，待服务端返回后 `res.blob()` 完整读入浏览器，再创建 ObjectURL 下载。
- 后端 `backend/app/main.py`：请求处理函数 `asyncio.to_thread(download_video)` 等待 yt-dlp 下载完成；FFmpeg 合并后才返回 `FileResponse`；响应结束立即清理临时目录。音频/字幕同样同步处理。
- `backend/app/bookmarks.py`：最多 5 条逐个下载、打 ZIP，HTTP 请求等整个批处理完成。
- `frontend/nginx.conf`：`proxy_read_timeout 600s`、`proxy_buffering off`；在 yt-dlp 阶段，后端可能长期无响应。
- `compose*.yaml`：API 根文件系统只读，`/tmp` 为 2GiB tmpfs，没有持久化文件与任务元数据。

### 不能仅靠加大 Nginx 超时解决

`proxy_read_timeout` 约束的是相邻两次上游读取操作的空闲间隔，而非请求总耗时。后端下载时没有向 Nginx 发送数据，超出此间隔就可能 504；即使调大超时，也仍会受外层反代、浏览器网络、源站失败以及内存和 tmpfs 限制。实际线上超时原因应通过日志区分，不能将所有失败归咎于 Nginx。

推荐先采集：
- Nginx access/error 日志：HTTP 状态、`request_time`、`upstream_response_time`、`upstream_status`、是否 `upstream timed out`。
- API 日志：`request_id`、平台、视频 ID、阶段耗时（提取、下载、合并、传输），不要记录 Cookie、真实下载地址或凭据。
- yt-dlp 归一化错误：超时、429/403、Cookie 失效、不可用格式、磁盘不足、FFmpeg 失败；日志需要脱敏。

## 2. 目标架构

单机轻量部署，不增加 Redis / Celery / PostgreSQL：

```text
Vue UI
  ├─ POST /api/tasks → 202 {id, status}
  ├─ GET /api/tasks/{id} / SSE /api/tasks/{id}/events
  └─ GET /api/tasks/{id}/file → 浏览器原生下载/Range
                │
          Nginx → FastAPI（只处理短请求及文件传输）
                         │
                         ├─ SQLite WAL：任务状态、进度、错误和过期时间
                         └─ Docker named volume：/data/files, /data/work
                                      ↑
                         Worker（同一后端镜像独立进程）
                           ├─ yt-dlp：下载与 progress_hooks
                           ├─ FFmpeg：合并、转换
                           └─ 清理、重试与并发控制
```

API 和 Worker 共享本机 Docker named volume `frame-data:/data`。SQLite 仅在同一 Docker 主机本地卷使用，不置于 NFS/SMB。初始只启动一个 worker 进程；下载并发建议 2，合并/转码并发建议 1。API 不执行耗时下载。不要使用内存中的 `asyncio.create_task` 或 `FastAPI BackgroundTasks` 作为持久任务队列：重启会丢失任务执行状态。

## 3. 对外接口契约

### 创建任务

```http
POST /api/tasks
Content-Type: application/json
Idempotency-Key: <unique-request-key>
```

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "kind": "video",
  "format_id": "137+140"
}
```

`kind` 可取 `video | audio | subtitle | bookmark_batch`。音频另带 `audio_format`，字幕另带 `language, subtitle_format, automatic`，收藏批量下载带 `ids` 并保持现有限额（最多 5 条）。校验沿用现有 URL 规范化和服务端格式重新确认，绝不接受任意 CDN 文件 URL、输出路径或命令行参数。

成功：`202 Accepted`，返回任务 ID 和状态查询地址；拒绝：`400/422/429`。重试创建请求必须使用相同幂等键，不重复提交任务。

### 查询 / 进度 / 操作 / 下载

| 方法 | 路径 | 含义 |
| --- | --- | --- |
| `GET` | `/api/tasks/{id}` | 当前状态、阶段、速率、字节数、已用时、错误码 |
| `GET` | `/api/tasks/{id}/events` | SSE 事件流；支持 reconnect、心跳和 Last-Event-ID |
| `POST` | `/api/tasks/{id}/cancel` | 取消排队或运行中的任务 |
| `POST` | `/api/tasks/{id}/retry` | 对失败/已中断任务重新排队（有次数上限） |
| `GET` | `/api/tasks/{id}/file` | 仅在 ready 时原生文件下载，支持 Range |
| `GET` | `/api/tasks` | 分页历史、可筛选状态、仅列当前用户自己的任务 |

旧 `POST /api/download`、`/api/audio`、`/api/subtitle` 先保留兼容期；前端切换到任务接口后再移除，不在同一次上线中直接废弃。

所有任务、事件和文件接口必须统一鉴权；书签任务沿用已有的 Frame 访问策略，但不能让私密文件因可猜 ID 泄露。建议在反代层保护全站，且应用层采用安全会话或一次性短时下载票据。**不要**将长期 `FRAME_ACCESS_TOKEN` 放在下载 URL 查询字符串中。原生 `<a href>` 不能带自定义认证头，故可增加 `POST /api/tasks/{id}/download-ticket`：校验权限后生成仅限该文件、短有效期的签名票据，返回给浏览器原生下载。

### 状态机

```text
queued → resolving → downloading → postprocessing → ready
   │         │             │              │
   └─────────┴─────────────┴──────────────┴→ failed / canceled
                     failed → retrying → queued
        running interrupted by restart → interrupted → queued/failed
                     ready → expired（保留元数据，清理文件）
```

- 阶段进度不要伪装成单一精确百分比。下载阶段使用 `downloaded_bytes / total_bytes`（未知总量时显示不确定进度）。
- `progress_hooks` 报告下载字节、速率、ETA；`postprocessor_hooks` 标记开始/完成合并或转换。FFmpeg 无细粒度进度时显示“正在合并”，不报虚构百分比。
- SSE 定期发心跳（例如 15 秒），浏览器断线重连；提供 `GET` 状态轮询作为回退。事件更新节流（如 500–1000ms），避免每个 fragment 写 SQLite。

## 4. 持久化与文件传输

目录建议：

```text
/data/
  tasks.sqlite
  work/<task-id>/              # .part、临时音视频流、处理中间产物
  files/<task-id>/output.mp4   # 完成后原子移动/重命名
```

- 任务达到 `ready` 前，不允许访问未完整生成的文件。
- 已完成文件保留默认 24 小时（可配置），可以重复下载和浏览器断点续传；到期后由清理器删除。正在传输的文件不可被清理，使用下载租约/引用计数或合理的延时删除。
- 使用浏览器原生文件下载，不再 `res.blob()`；`FileResponse` 可支持 `Accept-Ranges: bytes`、`206 Partial Content`，前提是路径安全、文件存在且代理正确转发 Range。
- 通过明确的 `Content-Disposition` 文件名、`Content-Length`、`Content-Type` 返回媒体。不得在 API 中公开实际容器文件路径。
- `MAX_DOWNLOAD_MB` 检查单个文件和最终合并产物；限制 `/data` 总空间、最大队列长度、并发数与任务存活时间，并为 FFmpeg 保留中间文件额外空间。
- 将原先的 `/tmp:size=2g` 大文件缓存迁移到磁盘卷，避免大文件与音视频合并消耗大量 RAM。仅少量小临时文件可保留在 tmpfs。
- 可选：以后由 Nginx `X-Accel-Redirect` 承担本机静态文件传输，但第一版用 Starlette FileResponse 即可。

## 5. 失败与恢复策略

| 事件 | 策略 |
| --- | --- |
| CDN 连接超时、临时 5xx | 有界指数退避 + 抖动，最多 3 次（内层 yt-dlp 重试需一起计算） |
| 429 / 账号风控 | 停止快速重试，显示冷却状态，必要时等待用户处理 |
| 401/403、Cookie 失效 | 失败并提示更新凭据，不进行无意义重试 |
| 视频不存在/版权或地域限制/无可用格式 | 标记不可重试的失败 |
| FFmpeg 合并失败 | 保留错误类别；重试可复用已完成的独立流 |
| API/Worker/Docker 重启 | 启动时扫描 running，设 interrupted 并安全重排队或失败；校验 .part、格式及剩余磁盘空间 |
| 浏览器离开/网络断开 | 任务继续；重进页面可从 SQLite 恢复状态和下载 |
| 用户取消 | Worker 将取消指令传递给下载钩子/子进程；终态必须准确，清理中间产物 |

要区分“**源站 → 服务器**”下载续传与“**服务器 → 浏览器**”Range 续传：二者相互独立。yt-dlp 端启用 `continuedl` 并仅在源站支持时复用部分文件；在浏览器端，FileResponse 支持 Range。不要把断点续传承诺为所有源站必然可用。

## 6. 前端行为

- 点击下载：立即创建任务、显示队列状态，并进入下载任务区。
- 显示阶段：排队、解析、下载中、合并中、已完成、失败、已取消。
- 显示实际进度（可得时）、速度、已下载/总大小、ETA；恢复连接后显示历史任务。
- `ready` 后出现“保存文件”按钮，通过经过授权的原生 GET 下载。
- 支持取消、重试、失败原因；用户切换导航、关闭页面时下载仍在 Worker 中继续。
- 收藏批量下载以父任务管理 1–5 个子项，显示单项成功/失败；形成 ZIP 时只包括成功的条目和失败 ID 清单。

## 7. 部署规划

- 新增独立 `worker` 服务，复用 API 镜像但覆盖启动命令。
- API 与 worker 挂载相同的本机 `frame-data` 持久卷。
- API 保持 `read_only: true`；仅挂载 `/data` 为可写；Worker 也写入 `/data`。
- 保留已有 Cookie 环境变量（只提供给真正需要 yt-dlp/Twikit 的后端/worker，不应传给 Web）。
- Nginx 将任务创建、状态查询和文件传输分开配置；SSE 关闭响应缓冲，文件下载可开启合理缓冲/正常流式传输。
- 若未来横向扩容到多台主机或远端 Worker，再考虑 PostgreSQL + Redis/对象存储；当前无需增加基础设施。

## 8. 验收标准

1. 模拟源站下载 12 分钟：创建任务在 2 秒内返回 202；不会出现原接口那种等待超时。
2. 模拟 FFmpeg 合并 3 分钟：状态持续更新，且最终只有完成后的文件可下载。
3. 关闭浏览器重新打开：任务状态和完成文件可恢复。
4. Worker 重启中断任务：不会永远卡在 running，不会产生重复输出或泄漏 .part。
5. 文件下载中断后重试：支持符合 HTTP 标准的 206 Range 和 416 非法 Range 行为。
6. 源站 429/401/403、429 cooldown、无可用格式、磁盘不足：有明确可行动错误且无无限重试。
7. 大文件下载无浏览器端 Blob 整体加载；API 进程内存保持稳定。
8. 音频、字幕、书签 ZIP 统一通过任务系统，旧接口兼容期内无回归。
9. 未授权的任务状态、事件和文件下载均不可访问；Cookie 和 Token 不出现在日志/URL。
10. 任务超时、磁盘配额、TTL 清理、文件被下载时清理竞争均有自动化测试。

## 9. 实施阶段

**阶段 A（可验证的最小闭环）**：SQLite + volume + 1 worker + 视频任务接口 + 查询 + 原生文件下载；让 10 分钟以上的源站耗时不再阻塞 HTTP 请求。

**阶段 B（可用性）**：yt-dlp hooks + SSE + 失败分类 + 取消/重试 + Worker 重启恢复 + 文件过期清理。

**阶段 C（完整迁移）**：音频、字幕、书签批量任务统一迁移；前端任务列表、历史记录、回归测试；旧同步下载接口分期退役。

**设计与诊断依据**
- [Nginx proxy_read_timeout](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)
- [Starlette FileResponse Range](https://www.starlette.dev/responses/#fileresponse)
- [yt-dlp embedding / progress_hooks](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp)
- [FastAPI BackgroundTasks limitations](https://fastapi.tiangolo.com/tutorial/background-tasks/)
