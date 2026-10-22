<script setup>
import { computed, ref } from 'vue'
import { ArrowDownToLine, ArrowRight, Check, CheckCircle2, Clipboard, Clock3, Download, ExternalLink, Film, Link2, LoaderCircle, LockKeyhole, Play, ShieldCheck, Sparkles, X } from 'lucide-vue-next'

const link = ref('')
const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const video = ref(null)
const selected = ref('')
const copied = ref(false)
const currentFormat = computed(() => video.value?.formats.find(f => f.id === selected.value))
const formatSize = n => n ? (n / 1048576).toFixed(1) + ' MB' : '大小未知'
const prettyDuration = s => {
  if (!s) return '未知时长'
  return [Math.floor(s / 3600), Math.floor(s % 3600 / 60), Math.floor(s % 60)]
    .filter((n, i) => i > 0 || s >= 3600)
    .map(n => String(n).padStart(2, '0')).join(':')
}
async function getError(res) {
  try { const data = await res.json(); return data.detail || '服务暂时不可用' } catch { return '无法连接到服务，请检查容器运行状态' }
}
async function analyze() {
  if (!link.value.trim()) { error.value = '请先粘贴一条推文链接'; return }
  loading.value = true
  video.value = null
  error.value = ''
  try {
    const res = await fetch('/api/extract', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: link.value.trim() }) })
    if (!res.ok) throw new Error(await getError(res))
    video.value = await res.json()
    selected.value = video.value.formats[0]?.id || ''
  } catch (e) { error.value = e.message || '解析失败' }
  finally { loading.value = false }
}
async function paste() {
  try {
    link.value = await navigator.clipboard.readText()
    error.value = ''
  } catch { error.value = '无法读取剪贴板，请手动粘贴链接' }
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
    a.download = 'x-video-' + video.value.id + '.mp4'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 60000)
  } catch (e) { error.value = e.message || '下载失败' }
  finally { downloading.value = false }
}
function reset() { video.value = null; link.value = ''; error.value = ''; selected.value = '' }
</script>

<template>
  <div class="shell">
    <div class="orb orb-one"></div><div class="orb orb-two"></div>
    <header class="topbar wrap">
      <a class="brand" href="/" aria-label="Frame 首页">
        <span class="brand-icon"><Film :size="19" :stroke-width="2.4"/></span>
        <span>frame<span class="brand-dot">.</span></span>
      </a>
      <div class="header-right">
        <span class="private-badge"><span class="green-dot"></span> PERSONAL STUDIO</span>
        <a class="github-link" href="https://github.com/hythzx/Twitter-Video-Downloader" target="_blank" rel="noreferrer">GitHub <ExternalLink :size="13"/></a>
      </div>
    </header>

    <main class="wrap">
      <section class="hero">
        <div class="eyebrow"><Sparkles :size="14"/> YOUR VIDEO, YOUR WAY <span class="eyebrow-line"></span> 01 / 02</div>
        <h1>Capture the moment<span class="accent">.</span><br/><span class="soft">Keep it forever.</span></h1>
        <p class="hero-sub">把精彩的瞬间，留在你的收藏里。<br class="mobile-br"/>粘贴一条 X / Twitter 链接，轻松提取高清视频。</p>
      </section>

      <div class="workspace">
        <div class="form-card">
          <div class="card-heading">
            <div class="step">01 <span>/ INPUT</span></div>
            <span class="section-title">粘贴视频链接</span>
          </div>
          <form @submit.prevent="analyze" class="input-form">
            <div class="url-box">
              <Link2 :size="20" class="input-icon"/>
              <input v-model="link" type="url" placeholder="https://x.com/username/status/..." aria-label="推文视频链接" :disabled="loading" required />
              <button type="button" class="paste-btn" @click="paste"><Clipboard :size="15"/> <span>粘贴</span></button>
            </div>
            <button class="primary-btn" type="submit" :disabled="loading">
              <LoaderCircle v-if="loading" :size="19" class="spin"/>
              <template v-else><span>解析视频</span><ArrowRight :size="19"/></template>
              <span v-if="loading">正在查找视频…</span>
            </button>
          </form>
          <p class="form-hint"><ShieldCheck :size="15"/> 无需登录 · 无需安装插件 · 仅处理可公开访问的视频</p>
        </div>

        <transition name="appear" mode="out-in">
          <section v-if="video" class="result-card" key="result">
            <div class="card-heading"><div class="step">02 <span>/ RESULT</span></div><span class="success"><CheckCircle2 :size="16"/> 解析成功</span></div>
            <div class="result-grid">
              <div class="preview" :style="video.thumbnail ? { backgroundImage: 'linear-gradient(0deg,rgba(5,9,18,.65),transparent 65%),url(' + video.thumbnail + ')' } : {}">
                <span class="preview-play"><Play :size="23" fill="currentColor"/></span>
                <span class="duration"><Clock3 :size="13"/> {{ prettyDuration(video.duration) }}</span>
              </div>
              <div class="video-info">
                <div class="mini-label">VIDEO FOUND</div>
                <h2>{{ video.title }}</h2>
                <p v-if="video.uploader" class="author">@{{ video.uploader.replace(/^@/, '') }}</p>
                <div class="rule"></div>
                <div class="mini-label format-header">选择下载画质 <span>{{ video.formats.length }} 种格式</span></div>
                <div class="format-options">
                  <button v-for="f in video.formats" :key="f.id" class="format-option" :class="{ active: selected === f.id }" @click="selected = f.id">
                    <span><strong>{{ f.label }}</strong><small>{{ f.width ? f.width + ' × ' + f.height : 'MP4' }}</small></span>
                    <span class="format-right"><small>{{ formatSize(f.size) }}</small><span class="radio-check"><Check v-if="selected === f.id" :size="13"/></span></span>
                  </button>
                </div>
                <button class="primary-btn download-btn" :disabled="downloading || !selected" @click="download">
                  <LoaderCircle v-if="downloading" :size="18" class="spin"/>
                  <Download v-else :size="18"/>
                  {{ downloading ? '正在准备下载，请勿关闭页面…' : '下载视频 · ' + (currentFormat?.label || 'MP4') }}
                </button>
              </div>
            </div>
            <button class="again" @click="reset">提取另一条视频 <ArrowRight :size="15"/></button>
          </section>
          <section v-else class="empty-card" key="empty">
            <div class="empty-art">
              <div class="empty-circle"><Film :size="33" :stroke-width="1.5"/></div>
              <span class="orbit orbit-a"></span><span class="orbit orbit-b"></span>
            </div>
            <p class="empty-title">你的下一段收藏，从这里开始</p>
            <p class="empty-sub">解析完成后，视频信息与可选画质将在这里呈现。</p>
            <span class="empty-state"><span class="green-dot"></span> READY WHEN YOU ARE</span>
          </section>
        </transition>
        <p v-if="error" class="error" role="alert"><X :size="16"/> {{ error }}</p>
      </div>
      <div class="feature-row">
        <div><span class="feature-icon"><ArrowDownToLine :size="17"/></span><span><strong>多画质选择</strong><small>选择适合你的 MP4 分辨率</small></span></div>
        <div><span class="feature-icon"><LockKeyhole :size="17"/></span><span><strong>为个人使用而生</strong><small>无账号、无追踪、无数据库</small></span></div>
        <div><span class="feature-icon"><Sparkles :size="17"/></span><span><strong>轻量便捷</strong><small>由 yt-dlp 提供解析能力</small></span></div>
      </div>
    </main>
    <footer class="footer wrap"><span>© {{ new Date().getFullYear() }} FRAME STUDIO</span><span>BUILT FOR THE MOMENTS WORTH KEEPING <span class="footer-star">✳</span></span></footer>
  </div>
</template>
