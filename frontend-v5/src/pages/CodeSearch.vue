<template>
  <div class="page-wrap">
    <div class="search-header">
      <div class="search-bar">
        <el-input
          v-model="query"
          :placeholder="t('search.placeholder')"
          @keyup.enter="doSearch"
          clearable
          size="large"
        />
        <el-select v-model="nResults" style="width:100px" size="large">
          <el-option :value="5"  :label="t('search.resultCount', { n: 5 })" />
          <el-option :value="10" :label="t('search.resultCount', { n: 10 })" />
          <el-option :value="20" :label="t('search.resultCount', { n: 20 })" />
        </el-select>
        <el-button type="primary" size="large" @click="doSearch" :loading="sse.loading.value">
          {{ t('search.btn') }}
        </el-button>
      </div>
      <div class="filter-chips">
        <span
          v-for="f in filters"
          :key="String(f.value)"
          class="chip"
          :class="{ active: langFilter === f.value }"
          @click="langFilter = f.value"
        >{{ f.label }}</span>
      </div>
    </div>

    <div class="results-wrap">
      <div v-if="hits.length" class="result-list">
        <div v-for="(hit, i) in hits" :key="i" class="method-card">
          <div class="method-top">
            <div>
              <div class="method-name">{{ hit.qualified_name }}</div>
              <div class="method-path mono">
                {{ hit.file_path }}<span v-if="hit.start_line"> : {{ hit.start_line }}</span>
              </div>
            </div>
            <span class="badge" :class="`badge--${hit.language}`">{{ hit.language }}</span>
          </div>
          <div class="code-block">{{ hit.body_text || hit.signature || '—' }}</div>
        </div>
      </div>

      <div v-else-if="!sse.loading.value && searched" class="no-result">
        <p>{{ t('search.noResult') }}</p>
        <p class="no-result-hint">{{ t('search.noResultHint') }}</p>
      </div>

      <div v-if="aiText || sse.loading.value" class="ai-block">
        <div class="ai-label">✦ {{ t('search.aiExplain') }}</div>
        <div class="ai-text">
          {{ aiText }}<span v-if="sse.loading.value" class="cursor"></span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRepoStore } from '@/stores/repoStore'
import { useSettingStore } from '@/stores/settingStore'
import { usePostSSE } from '@/composables/useSSE'
import type { CodeHit } from '@/types'

const { t }        = useI18n()
const repoStore    = useRepoStore()
const settingStore = useSettingStore()
const sse          = usePostSSE()

const query      = ref('')
const langFilter = ref<string | null>(null)
const nResults   = ref(5)
const searched   = ref(false)
const hits       = ref<CodeHit[]>([])
const aiText     = ref('')

const filters = computed(() => [
  { label: t('search.filterAll'), value: null as string | null },
  { label: 'Java',       value: 'java' },
  { label: 'JSP',        value: 'jsp' },
  { label: 'JS',         value: 'javascript' },
  { label: 'XML',        value: 'xml' },
])

async function doSearch() {
  if (!query.value.trim() || !repoStore.activeRepoId) return
  searched.value = true
  hits.value     = []
  aiText.value   = ''

  await sse.start(
    `/repos/${repoStore.activeRepoId}/search`,
    {
      query: query.value,
      n_results: nResults.value,
      language: langFilter.value,
      lang: settingStore.lang,
    },
    (data) => {
      if (data.type === 'hits')  hits.value  = data.hits as CodeHit[]
      if (data.type === 'chunk') aiText.value += data.text as string
    },
  )
}
</script>

<style lang="scss" scoped>
@use '@/styles/variables' as *;

.page-wrap      { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.search-header  {
  padding: 16px 20px 0; background: var(--el-bg-color);
  border-bottom: $border; flex-shrink: 0;
}
.search-bar     { display: flex; gap: 8px; margin-bottom: 12px; }
.filter-chips   { display: flex; gap: 6px; padding-bottom: 12px; flex-wrap: wrap; }
.chip {
  padding: 4px 12px; border: $border; border-radius: 20px; font-size: 11px;
  color: var(--el-text-color-secondary); cursor: pointer;
  &.active { background: $purple-50; color: $purple-800; border-color: $purple-200; }
  &:hover:not(.active) { background: var(--el-fill-color-light); }
}

.results-wrap {
  flex: 1; overflow-y: auto; padding: 16px 20px;
  display: flex; flex-direction: column; gap: 12px;
}
.result-list  { display: flex; flex-direction: column; gap: 10px; }
.method-card  {
  background: var(--el-bg-color); border: $border;
  border-radius: $radius-lg; padding: 14px 16px;
}
.method-top   {
  display: flex; align-items: flex-start;
  justify-content: space-between; margin-bottom: 8px;
}
.method-name  { font-family: $font-mono; font-size: 12px; font-weight: 500; color: $purple-600; }
.method-path  { font-size: 11px; color: var(--el-text-color-placeholder); margin-top: 2px; }

.ai-block {
  background: $purple-50; border: 0.5px solid $purple-200;
  border-radius: $radius-lg; padding: 14px 16px;
}
.ai-label { font-size: 11px; font-weight: 500; color: $purple-800; margin-bottom: 6px; }
.ai-text  { font-size: 13px; color: $purple-800; line-height: 1.8; white-space: pre-wrap; }
.cursor {
  display: inline-block; width: 2px; height: 13px;
  background: $purple-600; vertical-align: middle;
  animation: blink 1s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

.no-result      { color: var(--el-text-color-placeholder); font-size: 13px; padding: 20px 0; text-align: center; }
.no-result-hint { font-size: 12px; color: var(--el-text-color-placeholder); margin-top: 6px; }
.mono           { font-family: $font-mono; }
</style>
