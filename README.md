# CodeAnalyzer v5

企业级 Java/前端代码库智能分析平台。通过向量检索 + 调用链分析 + 本地大模型，帮助开发者快速理解代码、评估变更影响。

---

## 目录

- [功能概览](#功能概览)
- [系统架构](#系统架构)
- [环境要求](#环境要求)
- [快速部署](#快速部署)
  - [方式一：Docker Compose（推荐）](#方式一docker-compose推荐)
  - [方式二：本地开发启动](#方式二本地开发启动)
- [配置说明](#配置说明)
- [使用说明](#使用说明)
  - [1. 仓库管理](#1-仓库管理)
  - [2. 代码问答（语义检索）](#2-代码问答语义检索)
  - [3. 变更影响分析](#3-变更影响分析)
- [常见问题](#常见问题)

---

## 功能概览

| 功能 | 说明 |
|---|---|
| 代码索引 | tree-sitter 解析 Java / JSP / JS / XML，增量 MD5 对比，只处理变更文件 |
| 语义检索 | bge-m3 向量化 + Qdrant 双集合 RRF 融合检索，支持中 / 英 / 日自然语言查询 |
| 变更影响分析 | Git diff 定位变更方法 → BFS 上游调用链溯源（精确到调用行号）→ 本地 LLM 生成报告 |
| 文件监听 | watchdog 实时监听仓库文件变更，debounce 防抖，自动更新索引 |
| Git Hook | 安装 post-commit hook，每次提交后自动触发影响分析 |
| 多语言界面 | 中文 / English / 日本語 三语切换，LLM 提示词同步切换 |

---

## 系统架构

```
┌─────────────────────────────────────────────┐
│              Vue 3 前端（:5173）              │
│   RepoManager · CodeSearch · ChangeAnalysis  │
└────────────────────┬────────────────────────┘
                     │ REST + SSE
┌────────────────────▼────────────────────────┐
│           FastAPI 后端（:8000）               │
│  routers/ → services/ → indexer/ retrieval/  │
└──────┬────────────────┬──────────────────────┘
       │                │
┌──────▼──────┐  ┌──────▼──────┐  ┌───────────┐
│   SQLite    │  │   Qdrant    │  │  Ollama   │
│ code_units  │  │ sig + ctx   │  │ LLM 推理  │
│ call_edges  │  │ 双集合 RRF  │  │  :11434   │
└─────────────┘  └─────────────┘  └───────────┘
```

---

## 环境要求

| 组件 | 要求 |
|---|---|
| Python | 3.10 ＋ |
| Node.js | 18 ＋（仅本地开发前端需要） |
| Docker & Docker Compose | 部署方式一需要 |
| Ollama | 本地运行，需提前安装并拉取模型 |
| GPU（可选） | 支持 CUDA 的 NVIDIA 显卡，无 GPU 时自动降级到 CPU |
| 内存 | 建议 16GB＋（bge-m3 模型约 2.2GB，LLM 7B 约 4-14GB） |

### Ollama 模型

```bash
# 安装 Ollama（Windows/Mac/Linux）
# 访问 https://ollama.com 下载安装包

# 拉取 LLM 模型（二选一，推荐 3b 节省内存）
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:3b   # 内存不足时选这个

# 确认 Ollama 运行
ollama serve                   # 默认监听 localhost:11434
```

---

## 快速部署

### 方式一：Docker Compose（推荐）

```bash
# 1. 克隆项目
git clone <your-repo-url>
cd code-analyzer-v5

# 2. 确认 Ollama 已在宿主机运行
ollama serve

# 3. 修改 docker-compose.yml 中 backend 的环境变量（如需）
#    QDRANT_HOST=qdrant（容器内互通，默认正确）

# 4. 启动所有服务
docker compose up -d

# 5. 等待约 30 秒后访问
# 前端：http://localhost:5173
# 后端 API：http://localhost:8000
# Qdrant 控制台：http://localhost:6333/dashboard
```

> **GPU 支持**：docker-compose.yml 默认开启 NVIDIA GPU 直通。如无 GPU，删除 `deploy.resources.reservations.devices` 那段配置即可。

---

### 方式二：本地开发启动

#### 启动 Qdrant

```bash
docker run -d -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

#### 启动后端

```bash
cd backend-v5

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac/Linux

# 安装依赖
pip install -r requirements.txt

# 如需 GPU 加速（CUDA 12.8）
pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128

# 启动
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### 启动前端

```bash
cd frontend-v5

npm install
npm run dev        # 开发模式，访问 http://localhost:5173
npm run build      # 生产构建，产物在 dist/
```

---

## 配置说明

后端所有配置集中在 `backend-v5/config.py`：

```python
# Ollama
OLLAMA_BASE_URL  = "http://localhost:11434"
OLLAMA_LLM_MODEL = "qwen2.5-coder:7b"    # 内存不足改为 3b

# Embedding 模型
EMBED_MODEL_NAME     = "BAAI/bge-m3"
EMBED_DEVICE         = "cuda"             # 无 GPU 改为 "cpu"
EMBED_BATCH_SIZE_GPU = 64                 # 内存不足时调小（16~32）
EMBED_MAX_SEQ_LEN    = 512                # 内存不足时调小（256）

# Qdrant
QDRANT_HOST               = "localhost"
QDRANT_PORT               = 6333
QDRANT_UPSERT_CONCURRENCY = 2             # Qdrant 并发写入数

# 检索
RETRIEVAL_CANDIDATE_N = 20   # 向量检索候选数
RERANK_TOP_N          = 5    # reranker 保留 top N
```

---

## 使用说明

### 1. 仓库管理

**入口**：顶部导航 → 「仓库管理」

#### 添加仓库

点击左侧侧边栏的 **「+ 添加仓库」**，填写：
- **仓库名称**：用于界面显示的别名，例如 `my-project`
- **仓库路径**：本地绝对路径，例如 `D:\code\my-java-project`

> 路径必须是本地磁盘上存在的目录，且包含 Java/JSP/JS/XML 源码。

#### 扫描索引

添加仓库后点击 **「重新扫描」**，系统会依次执行：

| 阶段 | 说明 |
|---|---|
| ① 文件扫描 | 遍历仓库，过滤三方库文件，增量 MD5 对比，只解析有变化的文件 |
| ② 方法过滤 | 过滤 getter/setter、空方法、EL 表达式等无意义片段 |
| ③ GPU 向量化 | bge-m3 对每个方法的签名和方法体进行向量化（进度实时显示） |
| ④ 写入 Qdrant | 向量并发写入 Qdrant，与 GPU 向量化流水线并行 |

扫描完成后，左侧状态栏会显示 Java 文件数、方法数、上次扫描时间。

#### 自动化选项

| 选项 | 说明 |
|---|---|
| 文件监听（watchdog） | 开启后实时监听仓库文件变更，保存文件即自动更新索引，右上角显示「监听中」徽标 |
| Git Hook 自动分析 | 安装 post-commit hook 后，每次 `git commit` 自动触发影响分析，结果通过 SSE 推送到「变更分析」页 |

---

### 2. 代码问答（语义检索）

**入口**：顶部导航 → 「代码问答」

用自然语言描述你想找的代码逻辑，系统返回最相关的方法列表并由 AI 解释。

#### 操作步骤

1. 在搜索框输入自然语言问题，例如：
   - `哪里处理了用户登录的逻辑？`
   - `查询登录日志列表的方法`
   - `where is the permission check for admin users`
2. 选择返回条数（5 / 10 / 20）
3. 可按语言过滤（Java / JSP / JS / XML）
4. 点击「检索」或按回车

#### 结果说明

- **方法卡片**：显示全限定名、文件路径、行号、方法体代码
- **AI 解释**（蓝紫色区域）：Ollama 流式生成，解释检索到的方法的用途和使用场景

#### 检索原理

```
自然语言 query
    ↓
bge-m3 向量化
    ↓
Qdrant 双集合 RRF 融合（sig 签名向量 + ctx 上下文向量）
    ↓
SQLite 关键词补充（中/英/日 术语映射）
    ↓
bge-m3 reranker 精排（query + 签名 + 方法体 cosine 打分）
    ↓
Top N 结果 → Ollama 解释
```

---

### 3. 变更影响分析

**入口**：顶部导航 → 「变更分析」

分析代码变更后哪些方法会受影响，精确到文件路径和调用行号。

#### 触发方式

**方式 A：Git 手动分析**

选择分析模式后点击「Git 分析」：

| 模式 | 说明 | 适用场景 |
|---|---|---|
| HEAD | 当前工作区 vs HEAD，分析未提交的修改 | 提交前自查 |
| 指定 commit | 填写 Base（如 `HEAD~1`），对比到 HEAD | 分析某次提交的影响 |
| 分支对比 | 填写 Base 分支和 Compare 分支 | 分析 feature 分支合并影响 |

**方式 B：监听变更分析**

1. 先在「仓库管理」页开启「文件监听」
2. 修改并保存任意源码文件
3. 右上角出现「监听中」徽标，变更方法数量实时更新
4. 点击「分析监听变更」，分析当前所有被监听到的变更方法

**方式 C：Git Hook 自动触发**

1. 在「仓库管理」→「Git Hook」页安装 hook
2. 正常执行 `git commit`
3. 提交完成后，「变更分析」页自动收到影响报告（SSE 推送）

#### 结果说明

分析结果分三栏显示：

**左栏：变更方法**
- 列出本次变更的方法全限定名
- 显示文件路径和起始行号
- 展示方法体前 500 字符

**中栏：影响链**

```
● 变更方法（红色）
  ├─ ● 直接调用方（紫色，depth=1）
  │    位置: SomeService.java:156
  │    在第 89 行调用了变更方法        ← 精确到调用行号
  └─ ○ 间接调用方（浅蓝，depth=2+）
       位置: Controller.java:42
       在第 31 行调用了上层方法
```

**右栏：AI 分析报告**

Ollama 基于调用链生成三段式报告：
- `## 影响范围`：哪些模块、文件会被波及
- `## 风险点`：潜在的 Bug 点和需要关注的逻辑
- `## 测试建议`：建议回归测试的范围和用例方向

> AI 报告有约束：**只分析提供的调用链和行号，不推断未出现在链路中的方法**，防止 LLM 胡说。

#### 历史报告

切换到「历史报告」Tab，查看历次分析记录，可一键清空。

---

## 常见问题

**Q：扫描很慢，能加速吗？**

增大 `EMBED_BATCH_SIZE_GPU`（需要更多显存），或增大 `SCAN_WORKERS`（增加解析并行度）。首次扫描最慢，之后是增量扫描，只处理变更文件。

**Q：RAM 占用很高？**

主要来源是 Ollama 的 LLM 模型。7B 模型约需 14GB（fp16）或 4.5GB（4-bit 量化），显存不足时会 offload 到 RAM。建议改用 `qwen2.5-coder:3b` 降低内存压力。

**Q：检索结果不准？**

- 确认扫描时 Embedding 阶段（Phase 3）已完成，`embed_done` 数量大于 0
- 检查 Qdrant 是否正常运行（侧边栏连接状态显示绿色）
- 尝试用更具体的关键词，或切换语言重新搜索

**Q：变更分析找不到影响方法？**

- 确认被变更的方法已被索引（扫描完成后方法数 > 0）
- HEAD 模式分析的是**未提交的 unstaged 变更**，已提交的用「指定 commit」模式
- 调用链深度默认 3 层，如果调用关系超过 3 层不会显示

**Q：前端连接不上后端？**

- 确认后端 `uvicorn` 已启动，访问 `http://localhost:8000/docs` 能看到 API 文档
- 检查 `vite.config.ts` 的代理配置，开发模式下 `/api` 应代理到 `http://localhost:8000`
- Docker 部署时确认容器网络互通（`docker compose ps` 查看状态）

**Q：Qdrant 数据丢失？**

Docker 部署时数据存在 `qdrant_data` volume 中，不会因容器重启丢失。如果误删 volume，需要重新扫描建立索引（SQLite 中的代码解析结果仍在，只需重跑 Phase 3+4）。

---

## 目录结构

```
code-analyzer-v5/
├── backend-v5/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 所有配置项
│   ├── core/
│   │   ├── db.py            # 数据库连接池（async with get_db()）
│   │   ├── events.py        # SSE 全局广播
│   │   └── errors.py        # HTTP 异常工厂
│   ├── models/              # Pydantic 请求/响应模型
│   ├── parsers/             # tree-sitter 解析器（Java/JS/JSP/XML）
│   ├── indexer/
│   │   ├── scanner.py       # 文件扫描、增量对比、call_edges 提取
│   │   ├── embedder.py      # bge-m3 GPU 向量化
│   │   └── graph_builder.py # BFS 调用链溯源
│   ├── retrieval/
│   │   ├── vector_store.py  # Qdrant 双集合 RRF
│   │   ├── vector_search.py # 混合检索（向量 + 关键词）
│   │   └── reranker.py      # bge-m3 精排
│   ├── llm/
│   │   ├── client.py        # Ollama 流式调用
│   │   └── prompts/         # 中/英/日 提示词
│   ├── services/            # 业务逻辑层
│   ├── routers/             # API 路由（纯分发，不含业务）
│   ├── git/                 # diff 解析、hook 管理
│   ├── watcher/             # watchdog 文件监听
│   └── db/schema.sql        # 数据库 DDL
├── frontend-v5/
│   └── src/
│       ├── api/             # Axios 请求层（带类型）
│       ├── composables/     # useSSE, useScanProgress
│       ├── stores/          # Pinia（repoStore, settingStore）
│       ├── pages/           # RepoManager, CodeSearch, ChangeAnalysis
│       └── types/           # 所有 TypeScript 接口定义
├── docker-compose.yml
└── README.md
```

---

## License

MIT
