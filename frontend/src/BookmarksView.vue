<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowDownToLine, ArrowLeft, ArrowRight, Bookmark, Check, ExternalLink, Film, LoaderCircle, LockKeyhole, RefreshCw, ShieldCheck } from 'lucide-vue-next'

const TOKEN_STORAGE_KEY = 'frame-access-token'
const accessToken = ref(localStorage.getItem(TOKEN_STORAGE_KEY) || '')
// 单人自用：密钥只记忆在本机浏览器，接口保护不变（每次请求仍需携带）。
watch(accessToken, value => {
  if (value.trim()) localStorage.setItem(TOKEN_STORAGE_KEY, value.trim())
  else localStorage.removeItem(TOKEN_STORAGE_KEY)
})
const loading = ref(false)
const downloading = ref(false)
const taskPhase = ref('')

const PHASE_TEXT = {
  queued: '排队中…', resolving: '解析中…', downloading: '逐条下载中…',
  postprocessing: '打包中…',
}
const error = ref('')
const notice = ref('')
const items = ref([])
const nextCursor = ref(null)
const cursors = ref([null])
const pageIndex = ref(0)
const selected = ref([])
const videosOnly = ref(true)

const visibleItems = computed(() => videosOnly.value ? items.value.filter(x => x.has_video) : items.value)
const selectedCount = computed(() => selected.value.length)
const selectedHere = computed(() => visibleItems.value.filter(x => x.has_video && selected.value.includes(x.id)).length)

async function apiError(response) {
  try { const body = await response.json(); return typeof body.detail === 'string' ? body.detail : '操作失败' }
  catch { return '请求失败，请检查服务是否可用。' }
}

async function fetchPage(cursor = null, index = 0) {
  if (!accessToken.value.trim()) { error.value = '请输入 Frame 访问密钥（不是 X Cookie）'; return }
  loading.value = true
  error.value = ''
  notice.value = ''
  try {
    const params = new URLSearchParams({ count: '20' })
    if (cursor) params.set('cursor', cursor)
    const res = await fetch('/api/bookmarks?' + params.toString(), {
      headers: { 'X-Frame-Token': accessToken.value.trim() },
      cache: 'no-store',
    })
    if (!res.ok) throw new Error(await apiError(res))
    const data = await res.json()
    items.value = data.items || []
    nextCursor.value = data.next_cursor || null
    pageIndex.value = index
    cursors.value[index] = cursor
    notice.value = items.value.length ? '' : '这一页没有可显示的收藏。'
  } catch (e) { error.value = e.message || '获取收藏失败' }
  finally { loading.value = false }
}
function nextPage() {
  if (!nextCursor.value || loading.value) return
  fetchPage(nextCursor.value, pageIndex.value + 1)
}
function previousPage() {
  if (pageIndex.value <= 0 || loading.value) return
  fetchPage(cursors.value[pageIndex.value - 1] || null, pageIndex.value - 1)
}
function toggle(id) {
  if (selected.value.includes(id)) selected.value = selected.value.filter(x => x !== id)
  else if (selected.value.length < 5) selected.value = [...selected.value, id]
  else notice.value = '单次最多选择 5 条视频，以降低账号限流和服务器资源消耗。'
}
function togglePage() {
  const ids = visibleItems.value.filter(x => x.has_video).map(x => x.id)
  if (ids.length && ids.every(id => selected.value.includes(id))) {
    selected.value = selected.value.filter(id => !ids.includes(id))
  } else {
    selected.value = Array.from(new Set([...selected.value, ...ids])).slice(0, 5)
  }
}
async function save(ids) {
  if (!ids.length || downloading.value) return
  if (!accessToken.value.trim()) { error.value = '请输入 Frame 访问密钥'; return }
  downloading.value = true
  error.value = ''
  notice.value = ''
  try {
    // 任务化：Worker 在服务端后台逐条下载打 ZIP，浏览器关闭也不丢任务。
    const key = (crypto.randomUUID && crypto.randomUUID()) ||
      'batch-' + Date.now() + '-' + Math.random().toString(36).slice(2)
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Frame-Token': accessToken.value.trim(),
        'Idempotency-Key': key,
      },
      body: JSON.stringify({ kind: 'bookmark_batch', ids }),
    })
    if (!res.ok) throw new Error(await apiError(res))
    const { id } = await res.json()
    taskPhase.value = '排队中…'
    for (;;) {
      await new Promise(r => setTimeout(r, 2000))
      const s = await fetch('/api/tasks/' + id, { cache: 'no-store' })
      if (!s.ok) throw new Error('任务状态查询失败')
      const task = await s.json()
      taskPhase.value = PHASE_TEXT[task.status] || task.status
      if (task.status === 'ready') {
        notice.value = '打包完成，已开始保存。ZIP 中的 failed-ids.txt 会记录失败的推文 ID。'
        window.location.href = '/api/tasks/' + id + '/file'
        return
      }
      if (task.status === 'failed') throw new Error(task.error || '批量下载失败')
      if (task.status === 'canceled') throw new Error('任务已取消')
    }
  } catch (e) { error.value = e.message || '下载失败' }
  finally { downloading.value = false; taskPhase.value = '' }
}
</script>

<template>
  <section class="bookmark-workspace">
    <div class="bookmark-heading">
      <div>
        <div class="eyebrow">YOUR PRIVATE COLLECTION / X</div>
        <h1>我的收藏<span class="accent">.</span></h1>
        <p>通过服务器配置的 X Cookie 读取自己的书签。只读取，不会修改 X 收藏。</p>
      </div>
      <span class="bookmark-private"><LockKeyhole :size="16"/> PRIVATE</span>
    </div>

    <div class="bookmark-auth">
      <div class="bookmark-auth-head"><ShieldCheck :size="18"/> 安全访问</div>
      <div class="bookmark-token-row">
        <input v-model="accessToken" type="password" autocomplete="off" placeholder="输入 FRAME_ACCESS_TOKEN 访问密钥" aria-label="Frame 访问密钥" @keyup.enter="fetchPage(null, 0)" />
        <button type="button" class="primary-btn" :disabled="loading" @click="fetchPage(null, 0)">
          <LoaderCircle v-if="loading" class="spin" :size="16"/><RefreshCw v-else :size="16"/>
          {{ loading ? '读取中…' : '读取收藏' }}
        </button>
      </div>
      <p>X Cookie 仅保存在服务器环境变量中；访问密钥输入一次后会记住在你的浏览器本地（localStorage），每次请求仍需携带、不会发给任何第三方。清除浏览器站点数据即可忘记密钥。</p>
    </div>

    <div class="bookmark-toolbar">
      <label class="bookmark-switch"><input v-model="videosOnly" type="checkbox"/> 只看有视频的推文</label>
      <span class="bookmark-count">本页 {{ visibleItems.length }} 条 · 已选 {{ selectedCount }}/5</span>
    </div>

    <div v-if="items.length" class="bookmark-actions">
      <button type="button" class="bookmark-outline-btn" @click="togglePage">选择/取消本页视频</button>
      <button type="button" class="primary-btn bookmark-batch-btn" :disabled="!selectedCount || downloading" @click="save(selected)">
        <LoaderCircle v-if="downloading" class="spin" :size="16"/><ArrowDownToLine v-else :size="16"/>
        {{ downloading ? (taskPhase || '正在准备…') + ' · 保持页面打开' : '下载已选 ' + selectedCount + ' 条' }}
      </button>
    </div>

    <p v-if="error" class="error bookmark-message" role="alert">{{ error }}</p>
    <p v-if="notice" class="bookmark-notice" role="status">{{ notice }}</p>
    <div v-if="!items.length && !loading" class="empty-card bookmark-empty">
      <Bookmark :size="35" color="#9eb58d"/>
      <strong>从你的 X 收藏开始</strong>
      <span>配置服务器 Cookie 和访问密钥后，点击“读取收藏”。</span>
    </div>

    <div class="bookmark-grid">
      <article v-for="item in visibleItems" :key="item.id" class="bookmark-card">
        <div class="bookmark-card-image" :style="item.thumbnail ? { backgroundImage: 'url(' + JSON.stringify(item.thumbnail) + ')' } : {}">
          <span class="bookmark-type">{{ item.has_video ? 'VIDEO' : 'POST' }}</span>
          <Film v-if="!item.thumbnail" :size="26"/>
        </div>
        <div class="bookmark-card-body">
          <div class="bookmark-author"><strong>{{ item.author || item.username || 'X 用户' }}</strong><span v-if="item.username">@{{ item.username }}</span></div>
          <p>{{ item.text || '（无正文）' }}</p>
          <div class="bookmark-card-foot">
            <a :href="item.url" target="_blank" rel="noopener noreferrer" title="在 X 中打开"><ExternalLink :size="15"/> 原推文</a>
            <div class="bookmark-card-controls">
              <button v-if="item.has_video" type="button" class="bookmark-small" :disabled="downloading" @click="save([item.id])"><ArrowDownToLine :size="15"/> 下载</button>
              <button type="button" class="bookmark-select" :class="{ active: selected.includes(item.id) }" :disabled="!item.has_video || (selectedCount >= 5 && !selected.includes(item.id))" @click="toggle(item.id)" :aria-label="selected.includes(item.id) ? '取消选择' : '选择视频'">
                <Check v-if="selected.includes(item.id)" :size="15"/><span v-else>+</span>
              </button>
            </div>
          </div>
        </div>
      </article>
    </div>

    <div v-if="items.length" class="bookmark-paging">
      <button type="button" class="bookmark-outline-btn" :disabled="pageIndex === 0 || loading" @click="previousPage"><ArrowLeft :size="15"/> 上一页</button>
      <span>第 {{ pageIndex + 1 }} 页 · 每次读取最多 20 条</span>
      <button type="button" class="bookmark-outline-btn" :disabled="!nextCursor || loading" @click="nextPage">下一页 <ArrowRight :size="15"/></button>
    </div>
    <p class="bookmark-disclaimer">非官方 Cookie 接口可能随 X 网页更新而失效。建议低频、手动读取，避免大量或连续请求。</p>
  </section>
</template>
