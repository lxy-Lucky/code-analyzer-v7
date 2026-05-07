from __future__ import annotations
import re
import logging
from typing import Any
import httpx
import aiosqlite
from config import RETRIEVAL_CANDIDATE_N, RERANK_TOP_N, OLLAMA_BASE_URL, OLLAMA_LLM_MODEL
from retrieval.vector_store import rrf_search
from retrieval.reranker import rerank

logger = logging.getLogger("vector_search")


async def _embed_text(text: str) -> list[float]:
    from indexer.embedder import embed_texts
    vecs = await embed_texts([text])
    return vecs[0]


def _detect_lang(text: str) -> str:
    """简单判断查询语言：含日文假名→ja，含中文→zh，否则→en。"""
    if re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", text):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "en"


async def _translate_to_zh(query: str, src_lang: str) -> str:
    """
    把英文或日文查询翻译成中文，用于提升 bge-m3 中文代码库的检索命中率。
    失败时返回原查询。
    """
    if src_lang == "zh":
        return query

    lang_name = "English" if src_lang == "en" else "Japanese"
    prompt = (
        f"Translate the following {lang_name} query about code into Chinese. "
        "Output ONLY the Chinese translation, nothing else.\n\n"
        f"Query: {query}\nChinese:"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": OLLAMA_LLM_MODEL, "prompt": prompt, "stream": False},
            )
            r.raise_for_status()
            zh = r.json().get("response", "").strip()
            if zh:
                logger.info("Translated [%s] %r -> %r", src_lang, query, zh)
                return zh
    except Exception as e:
        logger.warning("Translation failed: %s", e)
    return query


def _extract_keywords(query: str) -> list[str]:
    """提取查询中可能匹配到方法名/类名的关键词，支持中日英。"""
    en_words = [w for w in re.findall(r"[a-zA-Z]{3,}", query)]

    term_map = {
        "判空": ["empty", "null", "blank", "isEmpty", "isNull", "isBlank"],
        "为空": ["empty", "null", "isEmpty", "isNull"],
        "非空": ["notEmpty", "notNull", "isNotEmpty", "isNotNull"],
        "空":   ["empty", "null", "isEmpty"],
        "ヌル": ["null", "isNull"],
        "空チェック": ["isEmpty", "isNull", "isBlank"],
        "数组": ["Array", "isArray"],
        "配列": ["Array", "isArray"],
        "集合": ["Collection", "List", "Set"],
        "リスト": ["List", "ArrayList"],
        "对象": ["Object", "isNull", "isNotNull"],
        "オブジェクト": ["Object", "isNull"],
        "字符串": ["String", "StringUtils", "str"],
        "文字列": ["String", "StringUtils"],
        "判断": ["check", "is", "validate", "verify"],
        "チェック": ["check", "validate", "verify"],
        "検証": ["validate", "verify"],
        "校验": ["validate", "verify", "check"],
        "验证": ["validate", "verify"],
        "查询": ["query", "select", "find", "search", "get"],
        "検索": ["search", "find", "query"],
        "保存": ["save", "insert", "add"],
        "削除": ["delete", "remove"],
        "删除": ["delete", "remove", "clear"],
        "更新": ["update", "modify", "set"],
        "获取": ["get", "fetch", "find"],
        "取得": ["get", "fetch"],
        "格式化": ["format", "formatter"],
        "转换": ["convert", "parse", "cast"],
        "変換": ["convert", "parse"],
        "加密": ["encrypt", "encode", "hash"],
        "解密": ["decrypt", "decode"],
        "日期": ["date", "Date", "time"],
        "日付": ["date", "Date"],
        "时间": ["time", "timestamp", "Date"],
        "缓存": ["cache", "Cache"],
        "キャッシュ": ["cache", "Cache"],
        "日志": ["log", "logger"],
        "ログ": ["log", "logger"],
        "权限": ["permission", "auth", "role"],
        "権限": ["permission", "auth", "role"],
        "用户": ["user", "User"],
        "ユーザー": ["user", "User"],
        "分页": ["page", "pagination"],
        "文件": ["file", "File"],
        "ファイル": ["file", "File"],
        "异常": ["exception", "error", "Exception"],
        "例外": ["exception", "error"],
        "请求": ["request", "Request"],
        "响应": ["response", "Response"],
    }

    extra: list[str] = []
    for term, mappings in term_map.items():
        if term in query:
            extra.extend(mappings)
    return list(set(en_words + extra))


async def _keyword_search(
    keywords: list[str],
    repo_id: int,
    db: aiosqlite.Connection,
    language_filter: str | None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    if not keywords:
        return []
    conditions = " OR ".join(["(qualified_name LIKE ? OR summary LIKE ?)"] * len(keywords))
    lang_clause = " AND language = ?" if language_filter else ""
    sql = f"""
        SELECT qualified_name, name, language, unit_type, file_path,
               start_line, end_line, body_text, signature, summary
        FROM code_units
        WHERE repo_id = ? {lang_clause} AND ({conditions})
        LIMIT {limit}
    """
    params: list[Any] = [repo_id]
    if language_filter:
        params.append(language_filter)
    for kw in keywords:
        params.extend([f"%{kw}%", f"%{kw}%"])

    results = []
    async with db.execute(sql, params) as cur:
        async for row in cur:
            results.append({
                "qualified_name": row[0], "name": row[1],
                "language": row[2],       "unit_type": row[3],
                "file_path": row[4],      "start_line": row[5],
                "end_line": row[6],       "body_text": (row[7] or "")[:500],
                "signature": row[8],      "summary": row[9],
                "rrf_score": 0.01,        "_source": "keyword",
            })
    return results


async def search_code(
    query: str,
    repo_id: int,
    db: aiosqlite.Connection,
    n_results: int = RERANK_TOP_N,
    language_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    检索流程：
    1. 语言检测，非中文查询翻译为中文（提升中文代码库命中率）
    2. embed 翻译后查询
    3. Qdrant RRF 向量检索（sig + ctx 两路融合）
    4. SQLite 关键词补充（用原始查询，保留用户意图）
    5. setter/getter 降权，合并去重
    6. bge-m3 GPU reranker 精排（用原始查询）
    """
    # 1. 语言检测 + 翻译
    src_lang = _detect_lang(query)
    search_query = await _translate_to_zh(query, src_lang)

    # 2. 向量检索
    q_vec = await _embed_text(search_query)
    vec_hits = rrf_search(q_vec, repo_id, RETRIEVAL_CANDIDATE_N, language_filter)

    # 批量补充 body_text / signature（一次查询替代 N 次）
    vec_qns = [h.get("qualified_name", "") for h in vec_hits if h.get("qualified_name")]
    detail_map: dict[str, tuple] = {}
    if vec_qns:
        _ph = ",".join("?" * len(vec_qns))
        async with db.execute(
            f"SELECT qualified_name, body_text, start_line, end_line, file_path, signature, name "
            f"FROM code_units WHERE repo_id=? AND qualified_name IN ({_ph})",
            [repo_id] + vec_qns,
        ) as cur:
            async for row in cur:
                detail_map[row[0]] = row
    for hit in vec_hits:
        row = detail_map.get(hit.get("qualified_name", ""))
        if row:
            hit["body_text"]  = (row[1] or "")[:500]
            hit["start_line"] = row[2]
            hit["end_line"]   = row[3]
            hit["file_path"]  = row[4]
            hit["signature"]  = row[5]
            hit["name"]       = row[6]
        hit["_source"] = "vector"

    # 4. 关键词补充（原始查询）
    keywords = _extract_keywords(query)
    kw_hits = await _keyword_search(keywords, repo_id, db, language_filter)

    # 5. 合并去重，setter/getter 降权
    def _is_trivial(name: str) -> bool:
        n = name or ""
        return n.startswith(("set", "get")) and len(n) > 3

    seen: set[str] = set()
    combined: list[dict[str, Any]] = []

    for hit in vec_hits:
        qn = hit["qualified_name"]
        if qn in seen:
            continue
        seen.add(qn)
        if _is_trivial(hit.get("name", "")):
            hit["rrf_score"] = hit.get("rrf_score", 0) * 0.05
        combined.append(hit)

    for hit in kw_hits:
        qn = hit["qualified_name"]
        if qn in seen:
            for v in combined:
                if v["qualified_name"] == qn:
                    v["rrf_score"] = max(v.get("rrf_score", 0), 0.3)
                    break
        else:
            seen.add(qn)
            if _is_trivial(hit.get("name", "")):
                hit["rrf_score"] = 0.001
            combined.append(hit)

    combined.sort(key=lambda x: x.get("rrf_score", 0), reverse=True)

    # 6. Reranker 精排
    return await rerank(query, combined, top_n=n_results)
