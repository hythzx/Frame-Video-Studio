<script setup>
import { computed, ref } from 'vue'
import BookmarksView from './BookmarksView.vue'
import HistoryView from './HistoryView.vue'
import ChannelView from './ChannelView.vue'
import { ArrowDownToLine, ArrowRight, Check, CheckCircle2, Clipboard, Clock3, Download, ExternalLink, Film, Link2, LoaderCircle, LockKeyhole, Play, ShieldCheck, Sparkles, X, Youtube, Globe2, History } from 'lucide-vue-next'

const activePage = ref('download')
const link = ref('')
const loading = ref(false)
const downloading = ref(false)
const taskPhase = ref('')
const error = ref('')
const video = ref(null)
const selected = ref('')
const downloadMode = ref('video')
const audioFormat = ref('mp3')
const subtitleFormat = ref('srt')
const subtitleIndex = ref(0)
const subtitleTracks = computed(() => video.value?.subtitles || [])
const platforms = [
  { id: 'x', name: 'X / Twitter', short: 'X', sample: 'https://x.com/username/status/...' },
  { id: 'youtube', name: 'YouTube', short: 'YT', sample: 'https://www.youtube.com/watch?v=...' },
  { id: 'bilibili', name: '哔哩哔哩', short: 'B站', sample: 'https://www.bilibili.com/video/BV...' }
]
const detected = computed(() => {
  try {
    const host = new URL(link.value.trim()).hostname.toLowerCase()
    if (['x.com','www.x.com','twitter.com','www.twitter.com','mobile.x.com','mobile.twitter.com'].includes(host)) return 'x'
    if (['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','www.youtu.be'].includes(host)) return 'youtube'
    if (['bilibili.com','www.bilibili.com','m.bilibili.com','b23.tv'].includes(host)) return 'bilibili'
    if (new URL(link.value.trim()).protocol === 'https:' && !new URL(link.value.trim()).port) return 'other'
  } catch {}
  return ''
})
const platformName = id => platforms.find(p => p.id === id)?.name || (video.value?.platform_name || '其他平台')
const currentFormat = computed(() => video.value?.formats?.find(f => f.id === selected.value))
const formatSize = n => n ? (n / 1048576).toFixed(1) + ' MB' : '大小未知'
const prettyDuration = s => {
  if (!s && s !== 0) return '未知时长'
  const n = Math.floor(s)
  return (n >= 3600 ? [Math.floor(n / 3600)] : []).concat([Math.floor(n % 3600 / 60), n % 60])
    .map(x => String(x).padStart(2, '0')).join(':')
}
async function getError(res) {
  try { const data = await res.json(); return typeof data.detail === 'string' ? data.detail : '服务暂时不可用' } catch { return '无法连接到服务，请检查容器运行状态' }
}
async function analyze() {
  if (!link.value.trim()) { error.value = '请先粘贴视频链接'; return }
  if (!detected.value) { error.value = '请提供有效的 HTTPS 媒体链接'; return }
  loading.value = true
  video.value = null
  error.value = ''
  try {
    const res = await fetch('/api/extract', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: link.value.trim() }) })
    if (!res.ok) throw new Error(await getError(res))
    video.value = await res.json()
    selected.value = video.value.formats?.[0]?.id || ''
    downloadMode.value = video.value.formats?.length ? 'video' : (video.value.audio_available ? 'audio' : 'subtitle')
    subtitleIndex.value = 0
  } catch (e) { error.value = e.message || '解析失败' }
  finally { loading.value = false }
}
async function paste() {
  try { link.value = await navigator.clipboard.readText(); error.value = '' }
  catch { error.value = '无法读取剪贴板，请手动粘贴链接' }
}
const PHASE_TEXT = {
  queued: '排队中…', resolving: '解析中…', downloading: '下载中…',
  postprocessing: '合并中…',
}

async function createAndAwaitTask(payload) {
  const res = await fetch('/api/tasks', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': (crypto.randomUUID && crypto.randomUUID()) ||
        'task-' + Date.now() + '-' + Math.random().toString(36).slice(2),
    },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await getError(res))
  const { id } = await res.json()
  taskPhase.value = '排队中…'
  // 服务端 Worker 执行下载；页面保持打开以便完成后自动保存文件。
  for (;;) {
    await new Promise(r => setTimeout(r, 2000))
    const s = await fetch('/api/tasks/' + id, { cache: 'no-store' })
    if (!s.ok) throw new Error('任务状态查询失败')
    const task = await s.json()
    taskPhase.value = PHASE_TEXT[task.status] || task.status
    if (task.status === 'ready') return task.download_url
    if (task.status === 'failed') throw new Error(task.error || '下载失败')
    if (task.status === 'canceled') throw new Error('任务已取消')
  }
}

async function download() {
  if (downloading.value || !video.value) return
  if (downloadMode.value === 'video' && !selected.value) return
  if (downloadMode.value === 'subtitle' && !subtitleTracks.value[subtitleIndex.value]) return
  downloading.value = true
  error.value = ''
  try {
    const mode = downloadMode.value
    const track = subtitleTracks.value[subtitleIndex.value]
    if (mode === 'video') {
      // 视频下载走任务接口：长耗时不再受 HTTP 超时限制，浏览器原生保存（支持断点续传）。
      const url = await createAndAwaitTask({
        url: video.value.url, kind: 'video', format_id: selected.value,
      })
      window.location.href = url
      return
    }
    const endpoint = mode === 'audio' ? '/api/audio' : '/api/subtitle'
    const payload = { url: video.value.url }
    if (mode === 'audio') payload.audio_format = audioFormat.value
    else Object.assign(payload, {
      language: track.language, automatic: track.automatic, subtitle_format: subtitleFormat.value
    })
    const res = await fetch(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    })
    if (!res.ok) throw new Error(await getError(res))
    const blob = await res.blob()
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const ext = mode === 'audio' ? audioFormat.value : subtitleFormat.value
    a.href = href
    a.download = (video.value.platform || 'video') + '-' +
      String(video.value.id || 'download').replace(/[^a-zA-Z0-9_-]/g, '_') +
      (mode === 'subtitle' ? '-' + track.language : '') + '.' + ext
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 60000)
  } catch (e) { error.value = e.message || '下载失败' }
  finally { downloading.value = false; taskPhase.value = '' }
}
function redownloadFromHistory(url) {
  activePage.value = 'download'
  link.value = url
  analyze()
}
function reset() { video.value = null; link.value = ''; error.value = ''; selected.value = '' }
function useExample(platform) {
  if (loading.value) return
  // Focus on the corresponding URL shape without inserting a fake, unusable video ID.
  const input = document.querySelector('.url-box input')
  input?.focus()
  input?.setAttribute('placeholder', platform.sample)
}
</script>

<template>
  <div class="shell">
    <div class="orb orb-one"></div><div class="orb orb-two"></div>
    <header class="topbar wrap">
      <a class="brand" href="/" aria-label="Frame 首页">
        <span class="brand-icon"><Film :size="19" :stroke-width="2.4"/></span>
        <span>frame<span class="brand-dot">.</span></span>
        <span class="brand-caption">VIDEO STUDIO</span>
      </a>
      <div class="header-right">
        <span class="private-badge"><span class="green-dot"></span> PERSONAL STUDIO</span>
        <a class="github-link" href="https://github.com/hythzx/Frame-Video-Studio" target="_blank" rel="noreferrer">GitHub <ExternalLink :size="13"/></a>
      </div>
    </header>

    <main class="wrap">
      <nav class="main-navigation" aria-label="主导航">
        <button type="button" :class="{active:activePage==='download'}" @click="activePage='download'"><Film :size="16"/> 视频下载</button>
        <button type="button" :class="{active:activePage==='history'}" @click="activePage='history'"><History :size="16"/> 历史记录</button>
        <button type="button" :class="{active:activePage==='channels'}" @click="activePage='channels'"><Youtube :size="16"/> 频道作品</button>
        <button type="button" :class="{active:activePage==='bookmarks'}" @click="activePage='bookmarks'"><LockKeyhole :size="16"/> 我的收藏</button>
      </nav>
      <template v-if="activePage === 'download'">
      <section class="hero">
        <div class="eyebrow"><Sparkles :size="14"/> ONE PLACE FOR YOUR VIDEOS <span class="eyebrow-line"></span> FRAME STUDIO</div>
        <h1>Save what matters<span class="accent">.</span><br/><span class="soft">From anywhere you love.</span></h1>
        <p class="hero-sub">一个入口，收藏来自不同平台的精彩瞬间。<br class="mobile-br"/>粘贴媒体链接，自动识别网站和格式，选择画质并保存。</p>
        <div class="platform-strip" aria-label="支持的视频平台">
          <div class="platform-strip-label">POPULAR PLATFORMS · 也支持更多 yt-dlp 站点</div>
          <div class="platform-items">
            <button v-for="p in platforms" :key="p.id" class="platform-chip" :class="['platform-' + p.id, { 'platform-active': detected === p.id }]" type="button" @click="useExample(p)" :title="'支持 ' + p.name + ' 链接，点击查看输入格式'">
              <span class="platform-glyph">{{ p.short }}</span>
              <span>{{ p.name }}</span>
              <Check v-if="detected === p.id" :size="13" class="chip-check" />
            </button>
          </div>
        </div>
      </section>

      <div class="workspace">
        <div class="form-card">
          <div class="card-heading">
            <div class="step">01 <span>/ PASTE LINK</span></div>
            <span class="section-title">多平台视频提取</span>
          </div>
          <form @submit.prevent="analyze" class="input-form">
            <div class="url-box">
              <Link2 :size="20" class="input-icon"/>
              <input v-model="link" type="url" placeholder="粘贴任意受支持网站的 HTTPS 视频或音频链接…" aria-label="视频链接" :disabled="loading" required />
              <button type="button" class="paste-btn" @click="paste"><Clipboard :size="15"/> <span>粘贴</span></button>
            </div>
            <button class="primary-btn" type="submit" :disabled="loading">
              <LoaderCircle v-if="loading" :size="19" class="spin"/>
              <template v-else><span>解析视频</span><ArrowRight :size="19"/></template>
              <span v-if="loading">正在解析…</span>
            </button>
          </form>
          <div class="input-bottom">
            <p class="form-hint"><ShieldCheck :size="15"/> 自动识别平台 · 按需合并音视频 · 无需安装插件</p>
            <span v-if="detected" class="detected"><CheckCircle2 :size="14"/> 已识别 {{ platformName(detected) }}</span>
          </div>
        </div>

        <transition name="appear" mode="out-in">
          <section v-if="video" class="result-card" key="result">
            <div class="card-heading"><div class="step">02 <span>/ YOUR VIDEO</span></div><span class="success"><CheckCircle2 :size="16"/> 解析成功</span></div>
            <div class="result-grid">
              <div class="preview" :class="{ 'no-cover': !video.thumbnail }">
                <!-- B 站等平台封面有防盗链：no-referrer 才能加载；CSS 背景图不受 meta/属性 referrer 控制故用 img。 -->
                <img v-if="video.thumbnail" :src="video.thumbnail" referrerpolicy="no-referrer" alt="" class="preview-cover"/>
                <span class="preview-shade"></span>
                <span class="preview-play"><Play :size="23" fill="currentColor"/></span>
                <span class="preview-platform">{{ video.platform_name || platformName(video.platform) }}</span>
                <span class="duration"><Clock3 :size="13"/> {{ prettyDuration(video.duration) }}</span>
              </div>
              <div class="video-info">
                <div class="mini-label">MEDIA READY <span class="source-label">{{ video.platform_name || platformName(video.platform) }}</span></div>
                <h2>{{ video.title }}</h2>
                <p v-if="video.uploader" class="author">{{ video.uploader }}</p>
                <div class="rule"></div>
                <div class="download-modes" role="tablist" aria-label="下载类型">
                  <button v-if="video.formats?.length" type="button" :class="{active:downloadMode === 'video'}" @click="downloadMode='video'">视频</button>
                  <button v-if="video.audio_available" type="button" :class="{active:downloadMode === 'audio'}" @click="downloadMode='audio'">纯音频</button>
                  <button v-if="subtitleTracks.length" type="button" :class="{active:downloadMode === 'subtitle'}" @click="downloadMode='subtitle'">字幕</button>
                </div>
                <template v-if="downloadMode === 'video'">
                  <div class="mini-label format-header">选择视频画质 <span>{{ video.formats?.length || 0 }} 种格式</span></div>
                  <div class="format-options">
                    <button v-for="f in video.formats" :key="f.id" class="format-option" :class="{ active: selected === f.id }" @click="selected = f.id" type="button">
                      <span><strong>{{ f.label }}</strong><small>{{ f.source === 'dash' ? 'DASH · MP4' : f.source === 'hls' ? 'HLS · MP4' : 'MP4' }}{{ f.width ? ' · ' + f.width + ' × ' + f.height : '' }}</small></span>
                      <span class="format-right"><small>{{ formatSize(f.size) }}</small><span class="radio-check"><Check v-if="selected === f.id" :size="13"/></span></span>
                    </button>
                  </div>
                </template>
                <div v-else-if="downloadMode === 'audio'" class="media-options">
                  <label for="audio-format">音频格式</label>
                  <select id="audio-format" v-model="audioFormat"><option value="mp3">MP3 · 兼容性好</option><option value="m4a">M4A · AAC 音频</option></select>
                  <p>使用 FFmpeg 从可用音轨提取或转码，不会提升原始音质。</p>
                </div>
                <div v-else class="media-options">
                  <label for="subtitle-track">字幕语言</label>
                  <select id="subtitle-track" v-model.number="subtitleIndex">
                    <option v-for="(t, i) in subtitleTracks" :key="t.language + t.automatic" :value="i">{{ t.language }} · {{ t.automatic ? '自动字幕' : '人工字幕' }}</option>
                  </select>
                  <label for="subtitle-format">字幕文件格式</label>
                  <select id="subtitle-format" v-model="subtitleFormat"><option value="srt">SRT</option><option value="vtt">VTT</option></select>
                  <p>仅显示原平台提供的字幕轨道；不包含语音识别或翻译功能。</p>
                </div>
                <button class="primary-btn download-btn" :disabled="downloading || (downloadMode === 'video' && !selected)" @click="download">
                  <LoaderCircle v-if="downloading" :size="18" class="spin"/>
                  <Download v-else :size="18"/>
                  {{ downloading ? (taskPhase || '正在准备…') + ' · 保持页面打开以自动保存' : downloadMode === 'video' ? '下载视频 · ' + (currentFormat?.label || 'MP4') : downloadMode === 'audio' ? '下载音频 · ' + audioFormat.toUpperCase() : '下载字幕 · ' + subtitleFormat.toUpperCase() }}
                </button>
                <p v-if="downloadMode === 'video' && video.platform !== 'x'" class="merge-hint">高画质视频可能需要等待服务端合并音视频流。</p>
              </div>
            </div>
            <button class="again" @click="reset">解析另一个视频 <ArrowRight :size="15"/></button>
          </section>
          <section v-else class="empty-card" key="empty">
            <div class="empty-art">
              <div class="empty-circle"><Film :size="33" :stroke-width="1.5"/></div>
              <span class="orbit orbit-a"></span><span class="orbit orbit-b"></span>
            </div>
            <p class="empty-title">好视频，值得好好收藏</p>
            <p class="empty-sub">支持 X / Twitter、YouTube 和哔哩哔哩，解析结果将在这里显示。</p>
            <span class="empty-state"><span class="green-dot"></span> READY TO EXPLORE</span>
          </section>
        </transition>
        <p v-if="error" class="error" role="alert"><X :size="16"/> {{ error }}</p>
      </div>
      <div class="feature-row">
        <div><span class="feature-icon"><Globe2 :size="17"/></span><span><strong>通用媒体解析</strong><small>由 yt-dlp 识别大量媒体网站</small></span></div>
        <div><span class="feature-icon"><ArrowDownToLine :size="17"/></span><span><strong>高清画质</strong><small>多分辨率可选，自动合并音视频</small></span></div>
        <div><span class="feature-icon"><LockKeyhole :size="17"/></span><span><strong>私人收藏空间</strong><small>自托管部署，无需注册账号</small></span></div>
      </div>
      </template>
      <HistoryView v-else-if="activePage === 'history'" @redownload="redownloadFromHistory"/>
      <ChannelView v-else-if="activePage === 'channels'"/>
      <BookmarksView v-else/>
    </main>
    <footer class="footer wrap"><span>© {{ new Date().getFullYear() }} FRAME STUDIO</span><span>MADE FOR THE MOMENTS WORTH KEEPING <span class="footer-star">✳</span></span></footer>
  </div>
</template>
