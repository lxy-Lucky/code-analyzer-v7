<template>
  <div class="page-wrap">
    <el-tabs v-model="activeTab" class="page-tabs">
      <el-tab-pane :label="t('analysis.manual')" name="manual">
        <div class="toolbar">
          <div class="seg-ctrl">
            <span class="seg-btn" :class="{ on: diffMode==='head' }"   @click="diffMode='head'">
              {{ t('analysis.head') }}
            </span>
            <span class="seg-btn" :class="{ on: diffMode==='commit' }" @click="diffMode='commit'">
              {{ t('analysis.commit') }}
            </span>
            <span class="seg-btn" :class="{ on: diffMode==='branch' }" @click="diffMode='branch'">
              {{ t('analysis.branch') }}
            </span>
          </div>
          <template v-if="diffMode==='commit' || diffMode==='branch'">
            <el-input v-model="baseRef"    :placeholder="t('analysis.baseLabel')"    size="small" style="width:120px" />
            <el-input v-if="diffMode==='branch'" v-model="compareRef" :placeholder="t('analysis.compareLabel')" size="small" style="width:120px" />
          </template>
          <el-button type="primary" size="small" @click="runGitAnalysis" :loading="gitSSE.loading.value">
            {{ t('analysis.runGit') }}
          </el-button>

          <div class="divider-v"></div>

          <el-button
            size="small"
            :type="repoStore.watching ? 'success' : 'default'"
            @click="runWatchAnalysis"
            :loading="watchSSE.loading.value"
            :disabled="!repoStore.watching"
          >
            {{ t('analysis.runWatch') }}
          </el-button>
          <template v-if="repoStore.watching">
            <span v-if="watchedQnames.length" style="font-size:11px;color:#0F6E56">
              {{ watchedQnames.length }} {{ t('analysis.methodsChanged') }}
            </span>
            <span v-else style="font-size:11px;color:var(--el-text-color-placeholder)">
              {{ t('analysis.noWatchedChanges') }}
            </span>
            <el-button v-if="watchedQnames.length" size="small" @click="clearWatchRecord">
              {{ t('analysis.clearWatch') }}
            </el-button>
          </template>
          <span v-else style="font-size:11px;color:var(--el-text-color-placeholder)">
            {{ t('analysis.watchInactive') }}
          </span>

          <div v-if="repoStore.hookInstalled" class="hook-badge">
            <span class="dot-pulse"></span>{{ t('repo.hookInstalled') }}
          </div>
        </div>

        <div v-if="!chains.length && !gitSSE.loading.value && !watchSSE.loading.value && searched" class="empty-tip">
          {{ t('analysis.noChanges') }}
        </div>

        <!-- 诊断面板：仅在分析结果为空时显示，帮助定位哪一步没有数据 -->
        <div v-if="searched && !gitSSE.loading.value && !chains.length" class="diag-panel">
          <div class="diag-title">🔍 诊断信息</div>
          <div v-if="diagError" class="diag-row diag-error">
            ❌ 后端异常：{{ diagError }}
          </div>
          <div class="diag-row">
            <span class="diag-label">Git diff 检测到的文件</span>
            <span v-if="diagDiffFiles.length" class="diag-ok">{{ diagDiffFiles.length }} 个：{{ diagDiffFiles.join(', ') }}</span>
            <span v-else class="diag-warn">
              0 个 ——
              若你修改的文件已 commit，请改用「Commit」模式；
              若未 commit，请确保文件已保存且未被 .gitignore 忽略。
            </span>
          </div>
          <div class="diag-row">
            <span class="diag-label">匹配到的变更方法</span>
            <span v-if="diagChangedMethods.length" class="diag-ok">{{ diagChangedMethods.length }} 个</span>
            <span v-else-if="diagDiffFiles.length" class="diag-warn">
              0 个 ——
              git diff 找到了文件但 DB 中未匹配到对应 code_units，
              请重新扫描该仓库。
            </span>
            <span v-else class="diag-dim">—</span>
          </div>
        </div>

        <div v-if="chains.length || gitSSE.loading.value || watchSSE.loading.value" class="analysis-layout">
          <!-- 左：变更方法 -->
          <div class="analysis-col">
            <div class="col-title">{{ t('analysis.changedMethods') }}</div>
            <div v-for="chain in chains" :key="chain.changed.qualified_name" class="changed-card">
              <div class="method-header">
                <span class="dot changed"></span>
                <span class="method-qn">{{ shortName(chain.changed.qualified_name) }}</span>
              </div>
              <div class="method-file">
                {{ chain.changed.file_path }}:{{ chain.changed.start_line }}
              </div>
              <div v-if="chain.changed.body_text" class="code-block" style="margin-top:6px;font-size:10px">
                {{ chain.changed.body_text.slice(0, 500) }}
              </div>
            </div>
          </div>

          <!-- 中：影响链 -->
          <div class="analysis-col">
            <div class="col-title">{{ t('analysis.impactChain') }}</div>
            <div class="legend">
              <span class="legend-item"><span class="dot changed"></span>{{ t('analysis.changed') }}</span>
              <span class="legend-item"><span class="dot direct"></span>{{ t('analysis.depth1') }}</span>
              <span class="legend-item"><span class="dot indirect"></span>{{ t('analysis.depth2') }}</span>
            </div>
            <div v-for="chain in chains" :key="'t_'+chain.changed.qualified_name" class="chain-block">
              <div class="tree-node">
                <span class="dot changed"></span>
                <div class="node-label">{{ shortName(chain.changed.qualified_name) }}</div>
              </div>
              <template v-if="chain.impact_chain.length">
                <div
                  v-for="node in chain.impact_chain"
                  :key="node.qualified_name"
                  class="tree-node"
                  :style="{ paddingLeft: `${node.depth * 16}px` }"
                >
                  <span class="dot" :class="node.depth === 1 ? 'direct' : 'indirect'"></span>
                  <div>
                    <div class="node-label">{{ shortName(node.qualified_name) }}</div>
                    <div class="node-file">
                      <span>{{ node.file_path }}:{{ node.start_line }}</span>
                      <span v-if="node.call_line" class="call-line-badge">
                        {{ t('analysis.callLine', { n: node.call_line }) }}
                      </span>
                    </div>
                  </div>
                </div>
              </template>
              <div v-else class="no-impact">{{ t('analysis.noUpstream') }}</div>
            </div>
          </div>

          <!-- 右：AI 报告 -->
          <div class="analysis-col">
            <div class="col-title">{{ t('analysis.report') }}</div>
            <div v-if="gitSSE.loading.value || watchSSE.loading.value" class="ai-loading">
              <span class="cursor"></span> {{ t('analysis.generating') }}
            </div>
            <div class="ai-text">{{ aiText }}</div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('analysis.history')" name="history">
        <div style="display:flex;justify-content:flex-end;margin-bottom:12px">
          <el-button size="small" type="danger" plain @click="clearHistory" :disabled="!reports.length">
            {{ t('analysis.clearHistory') }}
          </el-button>
        </div>
        <div v-if="!reports.length" class="empty-tip">{{ t('analysis.noHistory') }}</div>
        <div v-for="r in reports" :key="r.id" class="report-row">
          <span class="badge badge--java">{{ r.trigger_type }}</span>
          <span style="font-size:12px;margin-left:8px;color:var(--el-text-color-secondary)">
            {{ r.created_at }}
          </span>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { useRepoStore } from '@/stores/repoStore'
import { useSettingStore } from '@/stores/settingStore'
import { usePostSSE } from '@/composables/useSSE'
import { repoApi } from '@/api'
import type { ImpactChain, AnalysisReport } from '@/types'

const { t }        = useI18n()
const repoStore    = useRepoStore()
const settingStore = useSettingStore()
const gitSSE       = usePostSSE()
const watchSSE     = usePostSSE()

const activeTab  = ref('manual')
const diffMode   = ref<'head' | 'commit' | 'branch'>('head')
const baseRef    = ref('HEAD~1')
const compareRef = ref('')
const searched   = ref(false)
const chains     = ref<ImpactChain[]>([])
const aiText     = ref('')
const reports    = ref<AnalysisReport[]>([])
const watchedQnames = ref<string[]>([])

// 诊断信息：显示 diff/changed_methods/error 等中间事件
const diagDiffFiles     = ref<string[]>([])
const diagChangedMethods = ref<string[]>([])
const diagError         = ref('')

function shortName(qn: string): string {
  const parts = qn.split('#')
  if (parts.length === 2) {
    const cls = parts[0].split('.').pop() ?? parts[0]
    return `${cls}#${parts[1]}`
  }
  return qn.split('.').slice(-2).join('.')
}

async function runGitAnalysis() {
  if (!repoStore.activeRepoId) return
  searched.value = true
  chains.value   = []
  aiText.value   = ''
  diagDiffFiles.value      = []
  diagChangedMethods.value = []
  diagError.value          = ''

  const body: Record<string, unknown> = { lang: settingStore.lang }
  if (diffMode.value === 'head') {
    body.mode = 'head'
  } else if (diffMode.value === 'commit') {
    body.mode = 'between'; body.base = baseRef.value; body.compare = 'HEAD'
  } else {
    body.mode = 'between'; body.base = baseRef.value; body.compare = compareRef.value
  }

  await gitSSE.start(
    `/repos/${repoStore.activeRepoId}/analysis`,
    body,
    (data) => {
      if (data.type === 'impact')          chains.value = data.chains as ImpactChain[]
      if (data.type === 'chunk')           aiText.value += data.text as string
      if (data.type === 'diff')            diagDiffFiles.value = (data.files as string[]) ?? []
      if (data.type === 'changed_methods') diagChangedMethods.value = (data.methods as string[]) ?? []
      if (data.type === 'error')           diagError.value = data.error as string ?? 'unknown error'
    },
    () => loadReports(),
  )
}

async function runWatchAnalysis() {
  if (!repoStore.activeRepoId) return
  searched.value = true
  chains.value   = []
  aiText.value   = ''

  const qnames = watchedQnames.value
  const body: Record<string, unknown> = {
    lang: settingStore.lang,
    mode: qnames.length ? 'methods' : 'recent',
    ...(qnames.length ? { qualified_names: qnames } : {}),
  }

  await watchSSE.start(
    `/repos/${repoStore.activeRepoId}/analysis`,
    body,
    (data) => {
      if (data.type === 'impact') chains.value = data.chains as ImpactChain[]
      if (data.type === 'chunk')  aiText.value += data.text as string
    },
    async () => {
      await loadReports()
      if (repoStore.activeRepoId) {
        repoApi.clearWatchChanges(repoStore.activeRepoId).catch(() => {})
      }
    },
  )
}

async function clearWatchRecord() {
  if (!repoStore.activeRepoId) return
  await repoApi.clearWatchChanges(repoStore.activeRepoId).catch(() => {})
  watchedQnames.value = []
}

async function clearHistory() {
  if (!repoStore.activeRepoId) return
  try {
    await ElMessageBox.confirm(
      t('analysis.clearHistoryConfirm'),
      t('analysis.clearHistory'),
      { confirmButtonText: t('analysis.confirmOk'), cancelButtonText: t('analysis.confirmCancel'), type: 'warning' },
    )
    await repoApi.clearReports(repoStore.activeRepoId)
    reports.value = []
  } catch { /* cancelled */ }
}

async function loadWatchChanges() {
  if (!repoStore.activeRepoId) return
  try {
    const r = await repoApi.watchChanges(repoStore.activeRepoId)
    watchedQnames.value = r.methods ?? []
  } catch { /* ignore */ }
}

async function loadReports() {
  if (!repoStore.activeRepoId) return
  reports.value = await repoApi.reports(repoStore.activeRepoId)
}

onMounted(() => { loadReports(); loadWatchChanges() })

watch(() => repoStore.activeRepoId, () => {
  watchedQnames.value = []
  loadReports()
  loadWatchChanges()
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables' as *;

.page-wrap { flex: 1; overflow: hidden; display: flex; flex-direction: column; }

.page-tabs {
  flex: 1; display: flex; flex-direction: column; overflow: hidden;
  :deep(.el-tabs__header) {
    padding: 0 20px; margin: 0; border-bottom: $border;
    background: var(--el-bg-color); flex-shrink: 0;
  }
  :deep(.el-tabs__content) { flex: 1; overflow-y: auto; padding: 16px 20px; }
}

.toolbar    { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.seg-ctrl   { display: flex; border: $border; border-radius: $radius-md; overflow: hidden; }
.seg-btn {
  padding: 5px 12px; font-size: 12px; color: var(--el-text-color-secondary); cursor: pointer;
  &.on { background: $purple-600; color: white; }
  &:hover:not(.on) { background: var(--el-fill-color-light); }
}
.divider-v  { width: 1px; height: 24px; background: var(--el-border-color-light); margin: 0 4px; }
.hook-badge {
  display: flex; align-items: center; gap: 5px; padding: 4px 10px;
  background: #E1F5EE; border-radius: 20px; font-size: 11px; color: #085041;
}
.dot-pulse  { width: 6px; height: 6px; border-radius: 50%; background: #1D9E75; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

.analysis-layout { display: grid; grid-template-columns: 280px 1fr 1.4fr; gap: 14px; }
.analysis-col {
  background: var(--el-bg-color); border: $border; border-radius: $radius-lg;
  padding: 14px 16px; overflow-y: auto; max-height: 520px;
}
.col-title {
  font-size: 12px; font-weight: 500; color: var(--el-text-color-primary);
  margin-bottom: 10px; padding-bottom: 8px; border-bottom: $border;
}

.changed-card { margin-bottom: 14px; padding-bottom: 12px; border-bottom: $border; &:last-child { border-bottom: none; } }
.method-header { display: flex; align-items: center; gap: 6px; }
.method-qn     { font-family: $font-mono; font-size: 12px; font-weight: 500; color: $purple-600; }
.method-file   {
  font-size: 11px; color: var(--el-text-color-placeholder);
  margin-left: 13px; margin-top: 2px; font-family: $font-mono;
}

.chain-block { margin-bottom: 12px; padding-bottom: 10px; border-bottom: $border; &:last-child { border-bottom: none; } }
.tree-node   { display: flex; align-items: flex-start; gap: 6px; padding: 3px 0; }
.node-label  { font-family: $font-mono; font-size: 11px; color: var(--el-text-color-primary); }
.node-file {
  font-size: 10px; color: var(--el-text-color-placeholder);
  font-family: $font-mono; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
}
.call-line-badge {
  font-size: 10px; background: $purple-50; color: $purple-800;
  padding: 1px 6px; border-radius: 10px; font-family: $font-mono; white-space: nowrap;
}
.no-impact { font-size: 11px; color: var(--el-text-color-placeholder); padding-left: 13px; }

.legend      { display: flex; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--el-text-color-secondary); }
.dot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  &.changed  { background: $red-400; }
  &.direct   { background: $purple-600; }
  &.indirect { background: #B5D4F4; }
}

.ai-loading {
  font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 8px;
  display: flex; align-items: center; gap: 6px;
}
.ai-text { font-size: 13px; color: var(--el-text-color-regular); line-height: 1.8; white-space: pre-wrap; }
.cursor  {
  display: inline-block; width: 2px; height: 13px;
  background: $purple-600; vertical-align: middle; animation: blink 1s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

.empty-tip  { color: var(--el-text-color-placeholder); font-size: 13px; padding: 20px 0; text-align: center; }
.report-row { display: flex; align-items: center; padding: 6px 0; border-bottom: $border; }

.diag-panel {
  margin-top: 8px; padding: 12px 16px;
  background: #FAFAFA; border: 1px solid #E4E7ED; border-radius: 8px;
  font-size: 12px;
}
.diag-title  { font-weight: 600; margin-bottom: 8px; color: #303133; }
.diag-row    { display: flex; gap: 12px; align-items: flex-start; padding: 4px 0; flex-wrap: wrap; }
.diag-label  { color: #606266; flex-shrink: 0; min-width: 160px; }
.diag-ok     { color: #67C23A; }
.diag-warn   { color: #E6A23C; }
.diag-dim    { color: #C0C4CC; }
.diag-error  { color: #F56C6C; font-weight: 500; }
</style>
