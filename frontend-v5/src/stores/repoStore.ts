import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { repoApi } from '@/api'
import type { Repo, RepoStats } from '@/types'

export const useRepoStore = defineStore('repo', () => {
  const repos         = ref<Repo[]>([])
  const activeRepoId  = ref<number | null>(null)
  const stats         = ref<RepoStats | null>(null)
  const watching      = ref(false)
  const hookInstalled = ref(false)

  const activeRepo = computed(() =>
    repos.value.find(r => r.id === activeRepoId.value) ?? null,
  )

  async function fetchRepos() {
    repos.value = await repoApi.list()
    if (!activeRepoId.value && repos.value.length) {
      await selectRepo(repos.value[0].id)
    }
  }

  async function selectRepo(id: number) {
    activeRepoId.value = id
    // 三个请求并发，不串行等待
    await Promise.all([refreshStats(), refreshWatchStatus(), refreshHookStatus()])
  }

  async function refreshStats() {
    if (!activeRepoId.value) return
    stats.value = await repoApi.stats(activeRepoId.value)
  }

  async function refreshWatchStatus() {
    if (!activeRepoId.value) return
    const s = await repoApi.watchStatus(activeRepoId.value)
    watching.value = s.watching
  }

  async function refreshHookStatus() {
    if (!activeRepoId.value) return
    const s = await repoApi.hookStatus(activeRepoId.value)
    hookInstalled.value = s.installed
  }

  async function addRepo(name: string, path: string) {
    const repo = await repoApi.create(name, path)
    repos.value.unshift(repo)
    await selectRepo(repo.id)
    return repo
  }

  async function removeRepo(id: number) {
    await repoApi.remove(id)
    repos.value = repos.value.filter(r => r.id !== id)
    if (activeRepoId.value === id) {
      activeRepoId.value = repos.value[0]?.id ?? null
      if (activeRepoId.value) await selectRepo(activeRepoId.value)
    }
  }

  async function toggleWatch() {
    if (!activeRepoId.value) return
    if (watching.value) {
      await repoApi.watchStop(activeRepoId.value)
      watching.value = false
    } else {
      await repoApi.watchStart(activeRepoId.value)
      watching.value = true
    }
  }

  async function toggleHook() {
    if (!activeRepoId.value) return
    if (hookInstalled.value) {
      await repoApi.hookUninstall(activeRepoId.value)
      hookInstalled.value = false
    } else {
      await repoApi.hookInstall(activeRepoId.value)
      hookInstalled.value = true
    }
  }

  return {
    repos, activeRepoId, activeRepo, stats, watching, hookInstalled,
    fetchRepos, selectRepo, refreshStats, addRepo, removeRepo,
    toggleWatch, toggleHook, refreshHookStatus, refreshWatchStatus,
  }
})
