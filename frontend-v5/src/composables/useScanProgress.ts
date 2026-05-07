import { ref, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePostSSE } from './useSSE'
import type { ScanPhaseData } from '@/types'

export function useScanProgress(getRepoId: () => number | null) {
  const { t } = useI18n()
  const sse = usePostSSE()

  const isScanning   = ref(false)
  const currentPhase = ref(0)
  const phasesDone   = ref(0)
  const logs         = ref<{ text: string; cls: string }[]>([])
  const logBoxRef    = ref<HTMLElement | null>(null)

  const phaseDataMap = ref<Record<number, ScanPhaseData>>({
    1: { label: '' }, 2: { label: '' }, 3: { label: '' }, 4: { label: '' },
  })

  function resetLabels() {
    phaseDataMap.value = {
      1: { label: t('scan.phase1') },
      2: { label: t('scan.phase2') },
      3: { label: t('scan.phase3') },
      4: { label: t('scan.phase4') },
    }
  }

  const steps = computed(() => [
    { phase: 1, label: t('scan.phase1'), desc: phaseDataMap.value[1]?.desc ?? '' },
    { phase: 2, label: t('scan.phase2'), desc: phaseDataMap.value[2]?.desc ?? '' },
    { phase: 3, label: t('scan.phase3'), desc: phaseDataMap.value[3]?.desc ?? '' },
    { phase: 4, label: t('scan.phase4'), desc: phaseDataMap.value[4]?.desc ?? '' },
  ])

  const currentPhaseData = computed(() => phaseDataMap.value[currentPhase.value] ?? null)

  const phasePercent = computed(() => {
    const d = currentPhaseData.value
    if (!d?.total) return 0
    return Math.min(100, Math.round(((d.done ?? 0) / d.total) * 100))
  })

  function addLog(text: string, cls: string) {
    logs.value.push({ text, cls })
    nextTick(() => {
      if (logBoxRef.value) logBoxRef.value.scrollTop = logBoxRef.value.scrollHeight
    })
  }

  function reset() {
    logs.value = []
    currentPhase.value = 0
    phasesDone.value = 0
    resetLabels()
  }

  function handleEvent(data: Record<string, unknown>) {
    const type = data.type as string
    if (type === 'heartbeat') return

    if (type === 'phase') {
      currentPhase.value = data.phase as number
      addLog(`▶ Phase ${data.phase}: ${data.label}`, 'log-info')
    }
    if (type === 'progress') {
      const { done, total, file, status } = data as {
        done: number; total: number; file: string; status: string
      }
      phaseDataMap.value[1] = {
        label: t('scan.phase1'), done, total,
        currentFile: file, desc: `${done}/${total}`,
      }
      if (status === 'updated') addLog(`✓ ${file}`, 'log-update')
    }
    if (type === 'phase_done' && data.phase === 1) {
      phasesDone.value = 1
      phaseDataMap.value[1].desc = `更新${data.updated_files}文件 ${data.updated_units}个方法`
      addLog(`  Phase 1 完成：${data.updated_files} 文件`, 'log-update')
    }
    if (type === 'filter_done') {
      phasesDone.value = 2
      phaseDataMap.value[2] = {
        label: t('scan.phase2'),
        done: data.to_embed as number,
        total: data.total_units as number,
        desc: `过滤${data.filtered}个，待embed ${data.to_embed}个`,
      }
      addLog(`  过滤完成：${data.filtered}个跳过，${data.to_embed}个待embed`, 'log-update')
    }
    if (type === 'embed_progress') {
      phaseDataMap.value[3] = {
        label: t('scan.phase3'),
        done: data.done as number,
        total: data.total as number,
        desc: `${(data.done as number).toLocaleString()}/${(data.total as number).toLocaleString()}`,
      }
    }
    if (type === 'phase_done' && data.phase === 3) {
      phasesDone.value = 3
      phaseDataMap.value[3].desc = `${(data.embedded as number).toLocaleString()} 向量 ${data.elapsed}s`
      addLog(`  Embedding完成：${(data.embedded as number).toLocaleString()} 条，${data.elapsed}s`, 'log-update')
    }
    if (type === 'qdrant_progress') {
      phaseDataMap.value[4] = {
        label: t('scan.phase4'),
        done: data.done as number,
        total: data.total as number,
        desc: `${(data.done as number).toLocaleString()}/${(data.total as number).toLocaleString()}`,
      }
    }
    if (type === 'phase_done' && data.phase === 4) {
      phasesDone.value = 4
      phaseDataMap.value[4].desc = `写入 ${(data.written as number).toLocaleString()} 条`
      addLog(`  Qdrant写入完成：${(data.written as number).toLocaleString()} 条`, 'log-update')
    }
    if (type === 'scan_complete') {
      currentPhase.value = 5
      isScanning.value = false
      addLog(
        `✓ 完成！耗时 ${data.elapsed}s，embed ${((data.total_embedded as number) ?? 0).toLocaleString()} 条`,
        'log-update',
      )
    }
    if (type === 'warning') addLog(`⚠ ${data.message}`, 'log-warn')
    if (type === 'error') {
      isScanning.value = false
      addLog(`✗ ${data.error}`, 'log-error')
    }
  }

  function restoreFromProgress(p: Record<string, unknown>) {
    const phases = p.phases as Record<string, { done: number; total: number; desc: string }>
    if (phases) {
      for (const [k, v] of Object.entries(phases)) {
        const ph = Number(k)
        phaseDataMap.value[ph] = {
          label: t(`scan.phase${ph}` as never),
          done: v.done, total: v.total, desc: v.desc,
        }
      }
    }
    currentPhase.value = (p.current_phase as number) || 0
    phasesDone.value = Math.max(0, currentPhase.value - 1)
    logs.value = []
    const serverLogs = (p.logs as Record<string, unknown>[]) || []
    serverLogs.forEach(handleEvent)
  }

  // 轮询（SSE 断开时降级）
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function startPoll(repoId: number) {
    stopPoll()
    pollTimer = setInterval(async () => {
      try {
        const p = await fetch(`/api/repos/${repoId}/scan/status`).then(r => r.json()) as Record<string, unknown>
        restoreFromProgress(p)
        if (p.status === 'done' || p.status === 'error') {
          isScanning.value = false
          stopPoll()
        }
      } catch { /* ignore */ }
    }, 3000)
  }

  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  async function checkAndRestoreProgress() {
    const repoId = getRepoId()
    if (!repoId) return
    try {
      const p = await fetch(`/api/repos/${repoId}/scan/status`).then(r => r.json()) as Record<string, unknown>
      if (p.status === 'scanning') {
        isScanning.value = true
        restoreFromProgress(p)
        startPoll(repoId)
      } else if (p.status === 'done' && (p.current_phase as number) > 0) {
        restoreFromProgress(p)
        currentPhase.value = 5
      }
    } catch { /* ignore */ }
  }

  async function startScan(force = false) {
    const repoId = getRepoId()
    if (!repoId) return
    reset()
    isScanning.value = true
    stopPoll()

    const url = force ? `/repos/${repoId}/scan?force=true` : `/repos/${repoId}/scan`
    await sse.start(
      url,
      {},
      handleEvent,
      () => { isScanning.value = false; stopPoll() },
      () => {
        if (isScanning.value) startPoll(repoId)
      },
    )
  }

  return {
    isScanning,
    currentPhase,
    phasesDone,
    logs,
    logBoxRef,
    steps,
    currentPhaseData,
    phasePercent,
    reset,
    startScan,
    stopPoll,
    checkAndRestoreProgress,
    ssELoading: sse.loading,
  }
}
