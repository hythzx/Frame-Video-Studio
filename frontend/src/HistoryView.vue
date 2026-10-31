<script setup>
import { onMounted, ref } from 'vue'
import { Clock3, ExternalLink, History, LoaderCircle, RefreshCw } from 'lucide-vue-next'

const emit = defineEmits(['redownload'])
const loading = ref(false)
const error = ref('')
const items = ref([])

const PLATFORM_LABEL = { x: 'X', youtube: 'YouTube', bilibili: 'B站' }

function safeThumb(url) {
  // 存量记录可能仍是 http://：https 页面会拦截混合内容，展示时统一升级。
  return url ? url.replace(/^http:\/\//, 'https://') : ''
}

function prettyDuration(seconds) {
  if (!seconds) return '时长未知'
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60), s = total % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
           : `${m}:${String(s).padStart(2, '0')}`
}

function prettyTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d) ? iso : d.toLocaleString('zh-CN', { hour12: false })
}

function formatCount(formatsJson) {
  try { return JSON.parse(formatsJson || '[]').length } catch { return 0 }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/history', { cache: 'no-store' })
    if (!res.ok) throw new Error('历史加载失败')
    items.value = await res.json()
  } catch (e) { error.value = e.message || '历史加载失败' }
  finally { loading.value = false }
}

function redownload(url) {
  emit('redownload', url)
}

onMounted(load)
</script>

<template>
  <section class="history-workspace">
    <div class="history-heading">
      <div>
        <div class="eyebrow">RECENT EXTRACTIONS</div>
        <h1>历史记录<span class="accent">.</span></h1>
        <p>最近解析过的视频都会保存在这里，一键回填重新下载。</p>
      </div>
      <button type="button" class="primary-btn" :disabled="loading" @click="load">
        <LoaderCircle v-if="loading" :size="16" class="spin"/><RefreshCw v-else :size="16"/>
        刷新
      </button>
    </div>

    <p v-if="error" class="history-error">{{ error }}</p>
    <p v-else-if="!loading && !items.length" class="history-empty">
      还没有解析记录。回到「视频下载」解析一个链接试试。
    </p>

    <ul v-else class="history-list">
      <li v-for="item in items" :key="item.url" class="history-item">
        <img v-if="item.thumbnail" :src="safeThumb(item.thumbnail)" alt="" class="history-thumb" loading="lazy" referrerpolicy="no-referrer"/>
        <div v-else class="history-thumb history-thumb-empty"><History :size="22"/></div>
        <div class="history-meta">
          <div class="history-title-row">
            <span class="history-platform">{{ PLATFORM_LABEL[item.platform] || item.platform }}</span>
            <span class="history-title">{{ item.title }}</span>
          </div>
          <div class="history-sub">
            <span v-if="item.uploader">{{ item.uploader }}</span>
            <span class="history-duration"><Clock3 :size="12"/> {{ prettyDuration(item.duration) }}</span>
            <span>{{ formatCount(item.formats_json) }} 种画质</span>
            <span>{{ prettyTime(item.extracted_at) }}</span>
          </div>
        </div>
        <button type="button" class="primary-btn history-redownload" @click="redownload(item.url)">
          重新下载
        </button>
      </li>
    </ul>
  </section>
</template>
