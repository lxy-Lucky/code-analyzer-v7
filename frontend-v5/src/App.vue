<template>
  <div class="app-layout">
    <header class="topbar">
      <div class="topbar__logo">
        <div class="logo-icon">
          <svg viewBox="0 0 12 12" fill="white" width="12" height="12">
            <path d="M2 9L5 3l2 4 1.5-2L11 9H2z"/>
          </svg>
        </div>
        <span>CodeAnalyzer</span>
      </div>

      <nav class="topbar__nav">
        <router-link to="/repo"     class="nav-item">{{ t('nav.repo') }}</router-link>
        <router-link to="/search"   class="nav-item">{{ t('nav.search') }}</router-link>
        <router-link to="/analysis" class="nav-item">{{ t('nav.analysis') }}</router-link>
      </nav>

      <div class="topbar__right">
        <div v-if="repoStore.watching" class="watch-badge">
          <span class="watch-dot pulse"></span>
          {{ t('watching') }}
          <el-badge
            v-if="settingStore.updateBadge"
            :value="settingStore.updateBadge"
            class="ml-1"
            type="success"
          />
        </div>
        <div class="lang-switcher">
          <span
            v-for="l in (['zh', 'en', 'ja'] as const)"
            :key="l"
            class="lang-btn"
            :class="{ active: locale === l }"
            @click="switchLang(l)"
          >{{ t(`lang.${l}`) }}</span>
        </div>
      </div>
    </header>

    <div class="app-body">
      <aside class="sidebar">
        <div class="sidebar__scroll">
          <div class="sidebar__section-label">{{ t('sidebar.repos') }}</div>
          <div
            v-for="repo in repoStore.repos"
            :key="repo.id"
            class="repo-item"
            :class="{ active: repoStore.activeRepoId === repo.id }"
            @click="repoStore.selectRepo(repo.id)"
          >
            <span class="repo-dot" :style="{ background: repoColor(repo.id) }"></span>
            <span class="repo-name">{{ repo.name }}</span>
            <span class="repo-meta">{{ repo.path.split('/').pop() }}</span>
            <span class="repo-delete" @click.stop="confirmDeleteRepo(repo)" title="删除仓库">×</span>
          </div>
          <div class="add-repo-btn" @click="showAddRepo = true">
            <span>+</span> {{ t('sidebar.addRepo') }}
          </div>

          <el-divider style="margin: 8px 0" />

          <div class="sidebar__section-label">{{ t('sidebar.indexStatus') }}</div>
          <div class="stat-row">
            <span>{{ t('sidebar.files') }}</span>
            <strong>{{ stats?.java_files ?? 0 }}</strong>
          </div>
          <div class="stat-row">
            <span>{{ t('sidebar.methods') }}</span>
            <strong>{{ stats?.total_units?.toLocaleString() ?? 0 }}</strong>
          </div>
          <div class="stat-row">
            <span>{{ t('sidebar.lastScan') }}</span>
            <strong style="font-size:11px">{{ relativeTime(stats?.last_scanned_at) }}</strong>
          </div>
        </div>

        <div class="conn-panel">
          <div class="sidebar__section-label">{{ t('sidebar.backendConn') }}</div>
          <div v-for="svc in services" :key="svc.key" class="conn-item">
            <div class="conn-row">
              <span class="conn-dot" :class="dotClass(svc.status)"></span>
              <span class="conn-name">{{ svc.name }}</span>
              <span class="conn-status" :class="statusClass(svc.status)">
                {{ t(`sidebar.${svc.status}`) }}
              </span>
            </div>
            <div class="conn-url">
              {{ svc.url }}
              <span v-if="svc.status === 'failed'" class="retry-btn" @click="checkHealth">
                {{ t('sidebar.retry') }}
              </span>
            </div>
          </div>
        </div>
      </aside>

      <main class="main-content">
        <router-view />
      </main>
    </div>

    <el-dialog v-model="showAddRepo" title="" width="480px" :show-close="true">
      <div style="padding: 8px 0">
        <el-form label-position="top" @submit.prevent="doAddRepo">
          <el-form-item :label="t('repo.nameLabel')">
            <el-input v-model="newRepoName" placeholder="my-project" />
          </el-form-item>
          <el-form-item :label="t('repo.title')">
            <el-input v-model="newRepoPath" :placeholder="t('repo.pathPlaceholder')" />
          </el-form-item>
          <el-button type="primary" @click="doAddRepo" :loading="addingRepo">
            {{ t('repo.addBtn') }}
          </el-button>
        </el-form>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElNotification, ElMessageBox } from 'element-plus'
import { useRepoStore } from '@/stores/repoStore'
import { useSettingStore } from '@/stores/settingStore'
import { healthApi } from '@/api'
import { useGetSSE } from '@/composables/useSSE'
import type { HealthStatus } from '@/types'

const { t, locale } = useI18n()
const repoStore    = useRepoStore()
const settingStore = useSettingStore()

const showAddRepo = ref(false)
const newRepoName = ref('')
const newRepoPath = ref('')
const addingRepo  = ref(false)

const REPO_COLORS = ['#534AB7', '#1D9E75', '#BA7517', '#E24B4A', '#378ADD', '#D4537E']
const repoColor = (id: number) => REPO_COLORS[id % REPO_COLORS.length]

const stats = computed(() => repoStore.stats)

function relativeTime(ts: string | null | undefined): string {
  if (!ts) return t('never')
  const normalized = ts.includes('T') || ts.endsWith('Z') ? ts : ts.replace(' ', 'T') + 'Z'
  const diff = Math.floor((Date.now() - new Date(normalized).getTime()) / 60000)
  if (diff < 1) return t('just_now')
  if (diff < 60) return t('minutes_ago', { n: diff })
  const hours = Math.floor(diff / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

// ── Health check ──────────────────────────────────────────────────────────────

interface Service {
  key: string
  name: string
  url: string
  status: 'connected' | 'failed' | 'loading'
}

const services = ref<Service[]>([
  { key: 'fastapi', name: 'FastAPI', url: 'localhost:8000',  status: 'loading' },
  { key: 'ollama',  name: 'Ollama',  url: 'localhost:11434', status: 'loading' },
  { key: 'qdrant',  name: 'Qdrant',  url: 'localhost:6333',  status: 'loading' },
  { key: 'embed',   name: 'bge-m3',  url: '',                status: 'loading' },
])

async function checkHealth() {
  try {
    const h = await healthApi.check()
    services.value[0].status = h.fastapi     ? 'connected' : 'failed'
    services.value[1].status = h.ollama      ? 'connected' : 'failed'
    services.value[2].status = h.qdrant      ? 'connected' : 'failed'
    services.value[3].status = h.embed_model ? 'connected' : 'loading'
    if (h.embed_device) services.value[3].url = h.embed_device
  } catch {
    services.value.forEach(s => (s.status = 'failed'))
  }
}

function dotClass(s: string) {
  return { 'dot--green': s === 'connected', 'dot--red': s === 'failed', 'dot--amber': s === 'loading' }
}
function statusClass(s: string) {
  return { 's-ok': s === 'connected', 's-err': s === 'failed', 's-warn': s === 'loading' }
}

// ── Global SSE ────────────────────────────────────────────────────────────────

const globalSSE = useGetSSE()

function connectSSE() {
  globalSSE.open('/api/events', (data) => {
    if (data.type === 'index_updated') {
      repoStore.refreshStats()
      settingStore.bumpBadge()
      ElNotification({
        title: t('watching'),
        message: `${(data.files as string[]).join(', ')} — ${data.units_updated} units`,
        type: 'success',
        duration: 3000,
      })
    }
    if (data.type === 'hook_triggered') {
      ElNotification({
        title: 'Git Hook',
        message: `${data.changed_files} files changed`,
        type: 'info',
        duration: 3000,
      })
    }
  })
}

// ── Lang ──────────────────────────────────────────────────────────────────────

function switchLang(l: 'zh' | 'en' | 'ja') {
  locale.value = l
  settingStore.setLang(l)
}

// ── Delete repo ───────────────────────────────────────────────────────────────

async function confirmDeleteRepo(repo: { id: number; name: string }) {
  try {
    await ElMessageBox.confirm(
      `确认删除仓库「${repo.name}」？\n相关 SQLite 数据和 Qdrant 向量将一并清除，不可恢复。`,
      '删除仓库',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
    await repoStore.removeRepo(repo.id)
  } catch {
    // 用户点取消，忽略
  }
}

// ── Add repo ──────────────────────────────────────────────────────────────────

async function doAddRepo() {
  if (!newRepoName.value || !newRepoPath.value) return
  addingRepo.value = true
  try {
    await repoStore.addRepo(newRepoName.value, newRepoPath.value)
    showAddRepo.value = false
    newRepoName.value = ''
    newRepoPath.value = ''
  } finally {
    addingRepo.value = false
  }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

let healthTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await repoStore.fetchRepos()
  await checkHealth()
  connectSSE()
  healthTimer = setInterval(checkHealth, 30000)
})

onUnmounted(() => {
  globalSSE.close()
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<style lang="scss">
@use '@/styles/variables' as *;

.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.topbar {
  display: flex;
  align-items: center;
  height: $topbar-height;
  padding: 0 16px;
  background: var(--el-bg-color);
  border-bottom: $border;
  flex-shrink: 0;
  gap: 0;

  &__logo {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 500;
    margin-right: 24px;
  }

  &__nav {
    display: flex;
    gap: 2px;
    flex: 1;
  }

  &__right {
    display: flex;
    align-items: center;
    gap: 10px;
  }
}

.logo-icon {
  width: 22px;
  height: 22px;
  background: $purple-600;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.nav-item {
  padding: 6px 14px;
  border-radius: $radius-md;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  text-decoration: none;
  transition: background .15s;
  &:hover { background: var(--el-fill-color-light); }
  &.router-link-active {
    background: var(--el-fill-color-light);
    color: var(--el-text-color-primary);
    font-weight: 500;
  }
}

.watch-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: $teal-50;
  border-radius: 20px;
  font-size: 11px;
  color: $teal-600;
}
.watch-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: $teal-400;
}

.lang-switcher { display: flex; gap: 2px; }
.lang-btn {
  padding: 4px 10px;
  border: $border;
  border-radius: $radius-md;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  &.active { background: $purple-50; color: $purple-800; border-color: $purple-200; }
  &:hover:not(.active) { background: var(--el-fill-color-light); }
}

.app-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.sidebar {
  width: $sidebar-width;
  background: var(--el-bg-color);
  border-right: $border;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;

  &__scroll {
    flex: 1;
    overflow-y: auto;
    padding: 12px 8px 8px;
  }

  &__section-label {
    padding: 4px 8px 8px;
    font-size: 11px;
    font-weight: 500;
    color: var(--el-text-color-placeholder);
    letter-spacing: .04em;
    text-transform: uppercase;
  }
}

.repo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: $radius-md;
  cursor: pointer;
  &:hover { background: var(--el-fill-color-light); }
  &.active {
    background: $purple-50;
    .repo-name { color: $purple-800; font-weight: 500; }
  }
}
.repo-dot  { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.repo-name { font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.repo-meta { font-size: 11px; color: var(--el-text-color-placeholder); }
.repo-delete {
  opacity: 0;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  line-height: 16px;
  text-align: center;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
  border-radius: 3px;
  transition: opacity .15s, color .15s, background .15s;
  cursor: pointer;
  &:hover { color: #A32D2D; background: #fde8e8; }
}
.repo-item:hover .repo-delete { opacity: 1; }

.add-repo-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: $radius-md;
  cursor: pointer;
  color: var(--el-text-color-placeholder);
  font-size: 12px;
  margin-top: 4px;
  &:hover { background: var(--el-fill-color-light); }
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  strong { color: var(--el-text-color-primary); }
}

.conn-panel {
  flex-shrink: 0;
  border-top: $border;
  padding: 10px 12px;
}
.conn-item  { margin-bottom: 6px; }
.conn-row   { display: flex; align-items: center; gap: 6px; padding: 3px 0; }
.conn-dot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  &.dot--green { background: $teal-400; }
  &.dot--red   { background: $red-400; }
  &.dot--amber { background: $amber-400; }
}
.conn-name   { font-size: 12px; color: var(--el-text-color-secondary); flex: 1; }
.conn-status { font-size: 11px; }
.s-ok   { color: $teal-600; }
.s-err  { color: #A32D2D; }
.s-warn { color: #854F0B; }
.conn-url {
  font-family: $font-mono;
  font-size: 10px;
  color: var(--el-text-color-placeholder);
  padding-left: 13px;
  display: flex;
  gap: 8px;
}
.retry-btn {
  color: $purple-600;
  cursor: pointer;
  text-decoration: underline;
  font-size: 11px;
}

.main-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.ml-1 { margin-left: 4px; }
</style>
