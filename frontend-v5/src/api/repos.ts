import http from './http'
import type {
  Repo, RepoStats, WatchStatus, WatchChanges, AnalysisReport, HealthStatus,
} from '@/types'

export const repoApi = {
  list:              ()                           => http.get<Repo[]>('/repos'),
  create:            (name: string, path: string) => http.post<Repo>('/repos', { name, path }),
  remove:            (id: number)                 => http.delete<{ ok: boolean }>(`/repos/${id}`),
  stats:             (id: number)                 => http.get<RepoStats>(`/repos/${id}/stats`),
  watchStart:        (id: number)                 => http.post<WatchStatus>(`/repos/${id}/watch/start`),
  watchStop:         (id: number)                 => http.post<WatchStatus>(`/repos/${id}/watch/stop`),
  watchStatus:       (id: number)                 => http.get<WatchStatus>(`/repos/${id}/watch/status`),
  watchChanges:      (id: number)                 => http.get<WatchChanges>(`/repos/${id}/watch/changes`),
  clearWatchChanges: (id: number)                 => http.delete<{ ok: boolean }>(`/repos/${id}/watch/changes`),
  hookInstall:       (id: number)                 => http.post<{ installed: boolean }>(`/repos/${id}/hook/install`),
  hookUninstall:     (id: number)                 => http.post<{ installed: boolean }>(`/repos/${id}/hook/uninstall`),
  hookStatus:        (id: number)                 => http.get<{ installed: boolean }>(`/repos/${id}/hook/status`),
  reports:           (id: number)                 => http.get<AnalysisReport[]>(`/repos/${id}/reports`),
  clearReports:      (id: number)                 => http.delete<{ ok: boolean }>(`/repos/${id}/reports`),
  methods:           (id: number, q: string)      =>
    http.get<{ qualified_name: string; name: string; file_path: string; start_line: number; language: string }[]>(
      `/repos/${id}/methods?q=${encodeURIComponent(q)}`,
    ),
}

export const healthApi = {
  check: () => http.get<HealthStatus>('/health'),
}
