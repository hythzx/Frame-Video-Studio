<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowDownToLine, Check, ExternalLink, Film, LoaderCircle, RefreshCw, Youtube } from 'lucide-vue-next'

const STORAGE_KEY = 'frame-youtube-channel-jobs-v1'
const channelUrl = ref('')
const section = ref('videos')
const channelTitle = ref('')
const items = ref([])
const nextStart = ref(null)
const selected = ref([])
const loading = ref(false)
const adding = ref(false)
const added = ref(0)
const error = ref('')
const notice = ref('')
const jobs = ref({})
let refreshTimer

const sections = [
  { value: 'videos', label: '视频' },
  { value: 'shorts', label: 'Shorts' },
  { value: 'streams', label: '直播回放' },
]
const selectedCount = computed(() => selected.value.length)
const jobList = computed(() => Object.values(jobs.value).sort((a, b) => b.created - a.created))
const activeStates = new Set(['queued', 'resolving', 'downloading', 'postprocessing'])
const stateText = {
  queued: '排队中', resolving: '解析中', downloading: '下载中', postprocessing: '合并中',
  ready: '可下载', failed: '失败', expired: '文件已过期', canceled: '已取消',
}
const visibleJobs = computed(() => jobList.value.slice(0, 60))

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds)) || !seconds) return ''
  const n = Math.round(Number(seconds))
  return [Math.floor(n / 3600), Math.floor(n / 60) % 60, n % 60]
    .filter((_, i) => i || n >= 3600)
    .map(x => String(x).padStart(2, '0')).join(':')
}
function formatDate(value) {
  const s = String(value || '')
  return /^\d{8}$/.test(s) ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6) : ''
}
function persistJobs() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(Object.entries(jobs.value).slice(-100)))) }
  catch { /* private mode or quota limits */ }
}
function readJobs() {
  try { jobs.value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {} }
  catch { jobs.value = {} }
}
async function apiError(res) {
  try {
    const data = await res.json()
    return typeof data.detail === 'string' ? data.detail : '请求失败'
  } catch { return '请求失败，请检查 API 服务' }
}
async function loadPage(reset = false) {
  if (!channelUrl.value.trim() || loading.value) return
  const from = reset ? 1 : nextStart.value
  if (!from) return
  loading.value = true
  error.value = ''
  if (reset) {
    items.value = []
    selected.value = []
    nextStart.value = null
    channelTitle.value = ''
  }
  try {
    const qs = new URLSearchParams({
      url: channelUrl.value.trim(), section: section.value, start: String(from),
    })
    const res = await fetch('/api/youtube/channels?' + qs, { cache: 'no-store' })
    if (!res.ok) throw new Error(await apiError(res))
    const data = await res.json()
    const have = new Set(items.value.map(x => x.id))
    items.value = items.value.concat((data.items || []).filter(x => !have.has(x.id)))
    nextStart.value = data.next_start
    channelTitle.value = data.channel_title || channelTitle.value || 'YouTube 频道'
    notice.value = !items.value.length ? '该栏目暂无可读取的视频，或当前网络无法访问这些作品。' : ''
  } catch (e) { error.value = e.message || '读取频道失败' }
  finally { loading.value = false }
}
function toggle(id) {
  if (selected.value.includes(id)) selected.value = selected.value.filter(x => x !== id)
  else if (selected.value.length < 20) selected.value = [...selected.value, id]
  else notice.value = '每次最多选择 20 条，避免一次排入过多任务。'
}
function selectLoaded() {
  const ids = items.value.map(x => x.id).slice(0, 20)
  selected.value = ids.every(id => selected.value.includes(id)) ? [] : ids
}
function uuid() {
  return (globalThis.crypto?.randomUUID?.() || 'job-' + Date.now() + '-' + Math.random().toString(36).slice(2))
}
async function queueVideo(ids) {
  if (!ids.length || adding.value) return
  adding.value = true
  added.value = 0
  error.value = ''
  notice.value = ''
  const batch = uuid()
  const pending = ids.filter(id => !activeStates.has(jobs.value[id]?.status) && jobs.value[id]?.status !== 'ready')
  if (!pending.length) { adding.value = false; notice.value = '这些作品已在下载队列中或已有可下载文件。'; return }
  const failures = []
  try {
    for (const id of pending.slice(0, 20)) {
      const url = 'https://www.youtube.com/watch?v=' + id
      try {
        const res = await fetch('/api/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key': batch + ':' + id },
          body: JSON.stringify({ url, kind: 'video', format_id: 'best' }),
        })
        if (!res.ok) throw new Error(await apiError(res))
        const task = await res.json()
        jobs.value = {
          ...jobs.value,
          [id]: { id, taskId: task.id, title: items.value.find(v => v.id === id)?.title || id,
                  status: task.status, created: Date.now(), downloadUrl: null, error: '' },
        }
        added.value += 1
        persistJobs()
      } catch (e) { failures.push(id + ': ' + (e.message || '提交失败')) }
    }
    selected.value = selected.value.filter(id => !pending.includes(id))
    notice.value = `已加入 ${added.value} 条下载任务；下载将在服务器后台继续。`
    if (failures.length) error.value = failures.slice(0, 3).join('；') + (failures.length > 3 ? ' 等' : '')
    await refreshJobs()
  } finally { adding.value = false }
}
async function refreshJobs() {
  const pending = jobList.value.filter(x => activeStates.has(x.status)).slice(0, 60)
  if (!pending.length) return
  const updates = await Promise.allSettled(pending.map(async job => {
    const res = await fetch('/api/tasks/' + encodeURIComponent(job.taskId), { cache: 'no-store' })
    if (!res.ok) return null
    const body = await res.json()
    return { ...job, status: body.status, downloadUrl: body.download_url,
             title: body.title || job.title, error: body.error || '' }
  }))
  const next = { ...jobs.value }
  let changed = false
  for (const entry of updates) {
    if (entry.status !== 'fulfilled' || !entry.value) continue
    next[entry.value.id] = entry.value
    changed = true
  }
  if (changed) { jobs.value = next; persistJobs() }
}
function clearCompleted() {
  jobs.value = Object.fromEntries(Object.entries(jobs.value).filter(([, j]) => activeStates.has(j.status)))
  persistJobs()
}

watch(section, () => { if (channelUrl.value.trim()) loadPage(true) })
onMounted(() => {
  readJobs()
  refreshJobs()
  refreshTimer = setInterval(refreshJobs, 5000)
})
onUnmounted(() => clearInterval(refreshTimer))
</script>

<template>
  <section class="channel-workspace">
    <div class="channel-heading">
      <div class="eyebrow">CREATOR LIBRARY / YOUTUBE</div>
      <h1>频道作品<span class="accent">.</span></h1>
      <p>粘贴 YouTube 博主主页，逐页浏览视频、Shorts 和直播回放，选择作品加入后台下载队列。</p>
    </div>

    <form class="channel-search" @submit.prevent="loadPage(true)">
      <Youtube :size="23" class="channel-youtube"/>
      <input v-model="channelUrl" type="url" required aria-label="YouTube 频道主页"
             placeholder="https://www.youtube.com/@creator" :disabled="loading"/>
      <button class="primary-btn" type="submit" :disabled="loading || adding">
        <LoaderCircle v-if="loading" class="spin" :size="17"/>
        <RefreshCw v-else :size="17"/>
        {{ loading ? '读取中…' : '加载作品' }}
      </button>
    </form>

    <div class="channel-tabs">
      <button v-for="tab in sections" :key="tab.value" type="button" :disabled="loading"
              :class="{ active: section === tab.value }" @click="section = tab.value">{{ tab.label }}</button>
    </div>
    <p v-if="error" class="channel-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="channel-notice">{{ notice }}</p>

    <div v-if="items.length" class="channel-tools">
      <div>
        <h2>{{ channelTitle }}</h2>
        <span>已加载 {{ items.length }} 条 · 已选择 {{ selectedCount }}/20</span>
      </div>
      <div class="channel-tool-buttons">
        <button type="button" class="bookmark-outline-btn" @click="selectLoaded">选择前 20 条 / 清除</button>
        <button type="button" class="primary-btn" :disabled="!selectedCount || adding" @click="queueVideo(selected)">
          <LoaderCircle v-if="adding" class="spin" :size="16"/><ArrowDownToLine v-else :size="16"/>
          {{ adding ? '已提交 ' + added + ' 条…' : '下载已选 ' + selectedCount + ' 条' }}
        </button>
      </div>
    </div>

    <div v-if="items.length" class="channel-list">
      <article v-for="item in items" :key="item.id" class="channel-item">
        <button type="button" class="channel-check" :class="{active:selected.includes(item.id)}"
                :disabled="adding || (selectedCount >= 20 && !selected.includes(item.id))"
                :aria-label="selected.includes(item.id) ? '取消选择' : '选择该作品'" @click="toggle(item.id)">
          <Check v-if="selected.includes(item.id)" :size="15"/><span v-else>+</span>
        </button>
        <img v-if="item.thumbnail" :src="item.thumbnail" alt="" loading="lazy" referrerpolicy="no-referrer" class="channel-thumb"/>
        <div v-else class="channel-thumb channel-thumb-empty"><Film :size="28"/></div>
        <div class="channel-info">
          <a :href="item.url" target="_blank" rel="noopener noreferrer" class="channel-title">{{ item.title }}</a>
          <div class="channel-sub">
            <span v-if="item.duration">{{ formatDuration(item.duration) }}</span>
            <span v-if="item.upload_date">{{ formatDate(item.upload_date) }}</span>
            <span>{{ item.id }}</span>
          </div>
          <div v-if="jobs[item.id]" class="channel-state">
            {{ stateText[jobs[item.id].status] || jobs[item.id].status }}
            <span v-if="jobs[item.id].error" :title="jobs[item.id].error">· {{ jobs[item.id].error }}</span>
          </div>
        </div>
        <div class="channel-item-actions">
          <a v-if="jobs[item.id]?.status === 'ready' && jobs[item.id]?.downloadUrl"
             class="primary-btn channel-download" :href="jobs[item.id].downloadUrl">
            <ArrowDownToLine :size="15"/> 保存
          </a>
          <button v-else type="button" class="bookmark-outline-btn"
                  :disabled="adding || (jobs[item.id] && activeStates.has(jobs[item.id].status))"
                  @click="queueVideo([item.id])">
            <LoaderCircle v-if="jobs[item.id] && activeStates.has(jobs[item.id].status)" :size="15" class="spin"/>
            <ArrowDownToLine v-else :size="15"/>
            {{ jobs[item.id] && activeStates.has(jobs[item.id].status) ? '处理中' : '下载' }}
          </button>
          <a :href="item.url" target="_blank" rel="noopener noreferrer" aria-label="在 YouTube 中打开"><ExternalLink :size="16"/></a>
        </div>
      </article>
    </div>

    <div v-if="nextStart && items.length" class="channel-more">
      <button type="button" class="bookmark-outline-btn" :disabled="loading" @click="loadPage(false)">
        <LoaderCircle v-if="loading" :size="16" class="spin"/><RefreshCw v-else :size="16"/>
        {{ loading ? '加载中…' : '加载更多作品' }}
      </button>
      <span>逐页读取，直到没有更多作品</span>
    </div>

    <div v-if="!items.length && !loading" class="channel-empty">
      <Youtube :size="40"/>
      <strong>从一个 YouTube 频道开始</strong>
      <span>支持 @用户名、频道 ID、/c/ 和 /user/ 主页链接。</span>
    </div>

    <div v-if="visibleJobs.length" class="channel-jobs">
      <div class="channel-jobs-head">
        <h2>下载任务 <span>（最近 {{ visibleJobs.length }} 条）</span></h2>
        <button type="button" class="bookmark-outline-btn" @click="clearCompleted">清除已完成记录</button>
      </div>
      <div v-for="job in visibleJobs" :key="job.taskId" class="channel-job">
        <span class="channel-job-title">{{ job.title }}</span>
        <span class="channel-job-status">{{ stateText[job.status] || job.status }}</span>
        <a v-if="job.status === 'ready' && job.downloadUrl" :href="job.downloadUrl" class="channel-job-link">保存 MP4</a>
      </div>
      <p>任务在服务器后台运行；此列表保存在当前浏览器，刷新页面后可继续查看。下载文件按服务器的保留期限清理。</p>
    </div>
    <p class="channel-note">频道作品通过 yt-dlp 读取。私密、已删除、会员专属或受区域限制的视频可能无法列出或下载；请合理控制批量任务数量。</p>
  </section>
</template>
