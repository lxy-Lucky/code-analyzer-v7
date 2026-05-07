<template>
  <div class="page-wrap">
    <el-tabs v-model="activeTab" class="page-tabs">
      <el-tab-pane :label="t('repo.scanConfig')" name="scan">

        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">{{ t('repo.javaFiles') }}</div>
            <div class="stat-value">{{ stats?.java_files ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">{{ t('repo.jspFiles') }}</div>
            <div class="stat-value">{{ stats?.jsp_files ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">{{ t('repo.jsFiles') }}</div>
            <div class="stat-value">{{ stats?.javascript_files ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">{{ t('repo.xmlFiles') }}</div>
            <div class="stat-value">{{ stats?.xml_files ?? 0 }}</div>
          </div>
        </div>

        <div class="card" style="margin-bottom:14px">
          <div class="section-title">{{ t('repo.title') }}</div>
          <div class="scan-row">
            <el-input
              :model-value="repoStore.activeRepo?.path ?? ''"
              readonly
              class="path-input"
              :placeholder="t('repo.pathPlaceholder')"
            />
            <el-button @click="repoStore.refreshStats()">↻</el-button>
            <el-button
              type="primary"
              @click="scan.startScan()"
              :loading="scan.isScanning.value"
              :disabled="scan.isScanning.value"
            >
              {{ scan.isScanning.value ? t('repo.scanning') : t('repo.rescan') }}
            </el-button>
          </div>

          <template v-if="scan.isScanning.value || scan.phasesDone.value > 0">
            <el-steps :active="scan.currentPhase.value" finish-status="success" style="margin-bottom:16px">
              <el-step v-for="s in scan.steps.value" :key="s.phase" :title="s.label" :description="s.desc" />
            </el-steps>

            <div v-if="scan.currentPhaseData.value" class="phase-progress">
              <div class="progress-meta">
                <span class="progress-label-text">{{ scan.currentPhaseData.value.label }}</span>
                <span class="progress-right">
                  {{ scan.currentPhaseData.value.done?.toLocaleString() ?? 0 }}
                  / {{ scan.currentPhaseData.value.total?.toLocaleString() ?? 0 }}
                </span>
              </div>
              <el-progress
                :percentage="scan.phasePercent.value"
                :stroke-width="6"
                :show-text="false"
                status="striped"
                striped-flow
                :duration="20"
              />
              <div v-if="scan.currentPhaseData.value.currentFile" class="current-file mono">
                {{ scan.currentPhaseData.value.currentFile }}
              </div>
            </div>

            <div class="log-box" :ref="el => scan.logBoxRef.value = el as HTMLElement">
              <div v-for="(log, i) in scan.logs.value.slice(-8)" :key="i" :class="log.cls">
                {{ log.text }}
              </div>
            </div>
          </template>
        </div>

        <div class="card">
          <div class="section-title">{{ t('repo.automations') }}</div>
          <div class="toggle-row">
            <div class="toggle-item">
              <el-switch v-model="watchEnabled" @change="repoStore.toggleWatch()" />
              <span class="toggle-label">{{ t('repo.watchdog') }}</span>
            </div>
            <div class="toggle-item">
              <el-switch v-model="hookEnabled" @change="repoStore.toggleHook()" />
              <span class="toggle-label">{{ t('repo.hookAuto') }}</span>
            </div>
            <el-button size="small" style="margin-left:auto" @click="repoStore.toggleHook()">
              {{ repoStore.hookInstalled ? t('repo.uninstallHook') : t('repo.installHook') }}
            </el-button>
          </div>
        </div>

      </el-tab-pane>

      <el-tab-pane :label="t('repo.gitHook')" name="hook">
        <div class="card">
          <div class="section-title">post-commit Hook</div>
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
            <span class="badge" :class="repoStore.hookInstalled ? 'badge--success' : 'badge--error'">
              {{ repoStore.hookInstalled ? t('repo.hookInstalled') : 'Not installed' }}
            </span>
            <el-button size="small" type="primary" @click="repoStore.toggleHook()">
              {{ repoStore.hookInstalled ? 'Uninstall' : 'Install' }}
            </el-button>
          </div>
          <div class="code-block">{{ hookScript }}</div>
        </div>

        <div class="card" style="margin-top:14px">
          <div class="section-title">{{ t('analysis.history') }}</div>
          <div v-if="!reports.length" style="color:var(--el-text-color-placeholder);font-size:12px">
            No reports yet.
          </div>
          <div v-for="r in reports" :key="r.id" class="report-row">
            <span class="badge badge--java">{{ r.trigger_type }}</span>
            <span style="font-size:12px;margin-left:8px">{{ r.created_at }}</span>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRepoStore } from '@/stores/repoStore'
import { useScanProgress } from '@/composables/useScanProgress'
import { repoApi } from '@/api'
import type { AnalysisReport } from '@/types'

const { t }     = useI18n()
const repoStore = useRepoStore()

const activeTab = ref('scan')
const reports   = ref<AnalysisReport[]>([])

const scan = useScanProgress(() => repoStore.activeRepoId)

const stats       = computed(() => repoStore.stats)
const watchEnabled = computed({ get: () => repoStore.watching,      set: () => {} })
const hookEnabled  = computed({ get: () => repoStore.hookInstalled, set: () => {} })

const hookScript = computed(() =>
  `#!/bin/sh\n# code-analyzer-hook\ncurl -s -X POST http://localhost:8000/api/git/hook-trigger \\\n  -H "Content-Type: application/json" \\\n  -d '{"repo_path": "${repoStore.activeRepo?.path ?? '/your/repo'}"}' > /dev/null 2>&1 || true`,
)

async function loadReports() {
  if (!repoStore.activeRepoId) return
  reports.value = await repoApi.reports(repoStore.activeRepoId)
}

onMounted(async () => {
  await loadReports()
  await scan.checkAndRestoreProgress()
})

onUnmounted(() => scan.stopPoll())

watch(() => repoStore.activeRepoId, async () => {
  scan.stopPoll()
  scan.reset()
  await loadReports()
  await scan.checkAndRestoreProgress()
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
  :deep(.el-tabs__content) { flex: 1; overflow-y: auto; padding: 20px; }
}

.stats-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 16px; }
.stat-card  { background: var(--el-fill-color-light); border-radius: $radius-md; padding: 12px 14px; }
.stat-label { font-size: 11px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.stat-value { font-size: 22px; font-weight: 500; }

.scan-row   { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; }
.path-input { flex: 1; :deep(input) { font-family: $font-mono; font-size: 12px; } }

.phase-progress { margin-bottom: 10px; }
.progress-meta  {
  display: flex; justify-content: space-between;
  font-size: 11px; color: var(--el-text-color-secondary); margin-bottom: 6px;
}
.progress-label-text { font-weight: 500; color: var(--el-text-color-primary); }
.progress-right      { color: $purple-600; }
.current-file {
  font-family: $font-mono; font-size: 10px; color: var(--el-text-color-placeholder);
  margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.log-box {
  background: var(--el-fill-color-light); border-radius: $radius-md;
  padding: 10px 12px; font-family: $font-mono; font-size: 11px;
  line-height: 1.8; max-height: 120px; overflow-y: auto; margin-top: 10px;
}
.log-update { color: #0F6E56; }
.log-info   { color: #185FA5; }
.log-warn   { color: #854F0B; }
.log-error  { color: #A32D2D; }

.toggle-row  { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.toggle-item { display: flex; align-items: center; gap: 8px; }
.toggle-label { font-size: 12px; color: var(--el-text-color-secondary); }

.report-row { display: flex; align-items: center; padding: 6px 0; border-bottom: $border; }
</style>
