<script setup>
import { computed, ref } from 'vue'
import { ArrowDownToLine, ArrowRight, Check, CheckCircle2, Clipboard, Clock3, Download, ExternalLink, Film, Link2, LoaderCircle, LockKeyhole, Play, ShieldCheck, Sparkles, X, Youtube, Globe2 } from 'lucide-vue-next'

const link = ref('')
const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const video = ref(null)
const selected = ref('')
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
  } catch {}
  return ''
})
const platformName = id => platforms.find(p => p.id === id)?.name || '视频'
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
  if (!detected.value) { error.value = '目前支持 X / Twitter、YouTube 和哔哩哔哩的视频链接'; return }
  loading.value = true
  video.value = null
  error.value = ''
  try {
    const res = await fetch('/api/extract', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: link.value.trim() }) })
    if (!res.ok) throw new Error(await getError(res))
    video.value = await res.json()
    selected.value = video.value.formats?.[0]?.id || ''
  } catch (e) { error.value = e.message || '解析失败' }
  finally { loading.value = false }
}
async function paste() {
  try { link.value = await navigator.clipboard.readText(); error.value = '' }
  catch { error.value = '无法读取剪贴板，请手动粘贴链接' }
}
async function download() {
  if (!selected.value || downloading.value) return
  downloading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/download', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: video.value.url, format_id: selected.value }) })
    if (!res.ok) throw new Error(await getError(res))
    const blob = await res.blob()
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = (video.value.platform || 'video') + '-' + String(video.value.id || 'download').replace(/[^a-zA-Z0-9_-]/g, '_') + '.mp4'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 60000)
  } catch (e) { error.value = e.message || '下载失败' }
  finally { downloading.value = false }
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
        <a class="github-link" href="https://github.com/hythzx/Twitter-Video-Downloader" target="_blank" rel="noreferrer">GitHub <ExternalLink :size="13"/></a>
      </div>
    </header>

    <main class="wrap">
      <section class="hero">
        <div class="eyebrow"><Sparkles :size="14"/> ONE PLACE FOR YOUR VIDEOS <span class="eyebrow-line"></span> FRAME STUDIO</div>
        <h1>Save what matters<span class="accent">.</span><br/><span class="soft">From anywhere you love.</span></h1>
        <p class="hero-sub">一个入口，收藏来自不同平台的精彩瞬间。<br class="mobile-br"/>粘贴视频链接，自动识别来源，选择画质并保存。</p>
        <div class="platform-strip" aria-label="支持的视频平台">
          <div class="platform-strip-label">SUPPORTED PLATFORMS</div>
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
              <input v-model="link" type="url" placeholder="粘贴 X / YouTube / 哔哩哔哩视频链接…" aria-label="视频链接" :disabled="loading" required />
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
              <div class="preview" :style="video.thumbnail ? { backgroundImage: 'linear-gradient(0deg,rgba(5,9,18,.65),transparent 65%),url(' + JSON.stringify(video.thumbnail) + ')' } : {}">
                <span class="preview-play"><Play :size="23" fill="currentColor"/></span>
                <span class="preview-platform">{{ platformName(video.platform) }}</span>
                <span class="duration"><Clock3 :size="13"/> {{ prettyDuration(video.duration) }}</span>
              </div>
              <div class="video-info">
                <div class="mini-label">VIDEO READY <span class="source-label">{{ platformName(video.platform) }}</span></div>
                <h2>{{ video.title }}</h2>
                <p v-if="video.uploader" class="author">{{ video.uploader }}</p>
                <div class="rule"></div>
                <div class="mini-label format-header">选择视频画质 <span>{{ video.formats?.length || 0 }} 种格式</span></div>
                <div class="format-options">
                  <button v-for="f in video.formats" :key="f.id" class="format-option" :class="{ active: selected === f.id }" @click="selected = f.id" type="button">
                    <span><strong>{{ f.label }}</strong><small>{{ f.width ? f.width + ' × ' + f.height : 'MP4' }}</small></span>
                    <span class="format-right"><small>{{ formatSize(f.size) }}</small><span class="radio-check"><Check v-if="selected === f.id" :size="13"/></span></span>
                  </button>
                </div>
                <button class="primary-btn download-btn" :disabled="downloading || !selected" @click="download">
                  <LoaderCircle v-if="downloading" :size="18" class="spin"/>
                  <Download v-else :size="18"/>
                  {{ downloading ? '正在准备视频，请勿关闭页面…' : '下载视频 · ' + (currentFormat?.label || 'MP4') }}
                </button>
                <p v-if="video.platform !== 'x'" class="merge-hint">高画质视频可能需要等待服务端合并音视频流。</p>
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
        <div><span class="feature-icon"><Globe2 :size="17"/></span><span><strong>多平台解析</strong><small>一个入口，识别三大视频平台</small></span></div>
        <div><span class="feature-icon"><ArrowDownToLine :size="17"/></span><span><strong>高清画质</strong><small>多分辨率可选，自动合并音视频</small></span></div>
        <div><span class="feature-icon"><LockKeyhole :size="17"/></span><span><strong>私人收藏空间</strong><small>自托管部署，无需注册账号</small></span></div>
      </div>
    </main>
    <footer class="footer wrap"><span>© {{ new Date().getFullYear() }} FRAME STUDIO</span><span>MADE FOR THE MOMENTS WORTH KEEPING <span class="footer-star">✳</span></span></footer>
  </div>
</template>
