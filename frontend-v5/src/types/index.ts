export interface Repo {
  id: number
  name: string
  path: string
  last_scanned_at: string | null
  unit_count: number
  file_count: number
  scan_status?: 'idle' | 'scanning' | 'done' | 'error'
}

export interface RepoStats {
  java_files: number
  jsp_files: number
  javascript_files: number
  xml_files: number
  total_units: number
  last_scanned_at: string | null
}

export interface CodeHit {
  qualified_name: string
  name: string
  language: string
  unit_type: string
  file_path: string
  start_line: number
  end_line: number
  body_text: string
  signature: string
  summary: string
  rrf_score?: number
  rerank_score?: number
  _source?: string
}

export interface ImpactNode {
  qualified_name: string
  depth: number
  file_path: string
  start_line: number
  call_line: number
  signature: string
  language: string
  body_text: string
}

export interface ImpactChain {
  changed: {
    qualified_name: string
    file_path: string
    start_line: number
    end_line: number
    signature: string
    body_text: string
  }
  impact_chain: ImpactNode[]
}

export interface AnalysisReport {
  id: number
  trigger_type: string
  changed_units: string
  created_at: string
}

export interface HealthStatus {
  fastapi: boolean
  ollama: boolean
  qdrant: boolean
  embed_model: boolean
  embed_device: string
  available_models: string[]
}

export interface ScanPhaseData {
  label: string
  done?: number
  total?: number
  currentFile?: string
  desc?: string
}

export interface ScanProgressState {
  status: 'idle' | 'scanning' | 'done' | 'error'
  current_phase: number
  phases: Record<number, { done: number; total: number; desc: string }>
  embed_done: number
  embed_total: number
  upsert_done: number
  logs: Record<string, unknown>[]
}

export interface WatchStatus {
  watching: boolean
  path: string
}

export interface WatchChanges {
  methods: string[]
  count: number
}
