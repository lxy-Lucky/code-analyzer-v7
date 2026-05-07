from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "db" / "code_analyzer.db"

# Ollama
OLLAMA_BASE_URL   = "http://localhost:11434"
OLLAMA_LLM_MODEL  = "qwen2.5-coder:7b"

# Embedding
EMBED_MODEL_NAME      = "BAAI/bge-m3"
EMBED_DEVICE          = "cuda"
EMBED_BATCH_SIZE_GPU  = 256
EMBED_MAX_SEQ_LEN     = 512
EMBED_DIMENSIONS      = 1024
EMBED_MODEL_CACHE_DIR = BASE_DIR / "models"

# Qdrant
QDRANT_HOST               = "localhost"
QDRANT_PORT               = 6333
QDRANT_COLLECTION_SIG     = "signatures"
QDRANT_COLLECTION_CTX     = "context"
QDRANT_UPSERT_CONCURRENCY = 3   # embed/upsert 并发数

# 方法过滤
FILTER_GETTERS_SETTERS  = True
FILTER_EMPTY_METHODS    = True
FILTER_EL_EXPRESSIONS   = True
MIN_BODY_LINES_TO_EMBED = 2

# 扫描 / 检索
SCAN_WORKERS             = 8
RETRIEVAL_CANDIDATE_N    = 20
RERANK_TOP_N             = 5
WATCHER_DEBOUNCE_SECONDS = 1.5
HEARTBEAT_INTERVAL       = 15
RRF_K                    = 60

SUPPORTED_EXTENSIONS = {
    ".java": "java",
    ".jsp":  "jsp",
    ".js":   "javascript",
    ".xml":  "xml",
}

SUPPORTED_LANGUAGES = ["zh", "en", "ja"]
DEFAULT_LANGUAGE    = "zh"
