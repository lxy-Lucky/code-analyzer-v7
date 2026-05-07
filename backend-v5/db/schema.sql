CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    scan_status TEXT DEFAULT 'idle',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS file_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    hash TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, file_path),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS code_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    name TEXT NOT NULL,
    signature TEXT,
    start_line INTEGER,
    end_line INTEGER,
    body_text TEXT,
    summary TEXT,
    comment TEXT,
    class_name TEXT DEFAULT '',
    param_names TEXT DEFAULT '[]',
    param_types TEXT DEFAULT '[]',
    return_type TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, file_path, qualified_name, signature),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS call_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    caller_qualified TEXT NOT NULL,
    callee_qualified TEXT NOT NULL,
    call_line INTEGER DEFAULT 0,
    UNIQUE(repo_id, caller_qualified, callee_qualified),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    changed_files TEXT,
    changed_units TEXT,
    impact_json TEXT,
    report_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_units_repo ON code_units(repo_id);
CREATE INDEX IF NOT EXISTS idx_code_units_file ON code_units(file_path);
CREATE INDEX IF NOT EXISTS idx_call_edges_caller ON call_edges(caller_qualified);
CREATE INDEX IF NOT EXISTS idx_call_edges_callee ON call_edges(callee_qualified);
CREATE INDEX IF NOT EXISTS idx_file_hashes_repo ON file_hashes(repo_id);

CREATE TABLE IF NOT EXISTS llm_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    qualified_name TEXT NOT NULL,
    llm_summary TEXT DEFAULT '',
    llm_tags TEXT DEFAULT '[]',
    model TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    error TEXT DEFAULT '',
    hit_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, qualified_name),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_repo ON llm_cache(repo_id);
CREATE INDEX IF NOT EXISTS idx_llm_cache_status ON llm_cache(repo_id, status);
