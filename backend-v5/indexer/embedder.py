"""
embedder.py — GPU批量Embedding（sentence-transformers BAAI/bge-m3）

优化点：
- 全局单例 + 全局线程池，不每次创建销毁
- sig + ctx 合并成一次 encode，显存峰值减半
- 每批 encode 后 torch.cuda.empty_cache()
- token 截断 EMBED_MAX_SEQ_LEN（默认 512），防长方法体 OOM
- health 接口只查状态，不触发加载
- preload() 供 startup 异步预热
"""
from __future__ import annotations
import asyncio
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# 压制 HuggingFace Hub 未认证警告（不影响下载）
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
try:
    import huggingface_hub.utils._logging as _hf_log
    _hf_log.disable_progress_bar()
except Exception:
    pass

import config

logger = logging.getLogger("embedder")

_model = None
_executor: ThreadPoolExecutor | None = None   # 全局复用，不每次创建
_preload_task: asyncio.Task | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu_embed")
    return _executor


def _get_model():
    global _model
    if _model is not None:
        return _model
    from sentence_transformers import SentenceTransformer
    Path(config.EMBED_MODEL_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    logger.info("Loading %s to %s ...", config.EMBED_MODEL_NAME, config.EMBED_DEVICE)
    model = SentenceTransformer(
        config.EMBED_MODEL_NAME,
        cache_folder=str(config.EMBED_MODEL_CACHE_DIR),
        device=config.EMBED_DEVICE,
    )
    # 截断 token 长度，防止长方法体 OOM
    if hasattr(model, "max_seq_length"):
        model.max_seq_length = config.EMBED_MAX_SEQ_LEN
        logger.info("max_seq_length set to %d", config.EMBED_MAX_SEQ_LEN)
    _model = model
    logger.info("Model loaded. dim=%d device=%s", model.get_embedding_dimension(), model.device)
    return _model


def _sync_embed_merged(sig_texts: list[str], ctx_texts: list[str], batch_size: int) -> tuple[list, list]:
    """
    sig + ctx 合并成一个 list 一次 encode，结果再拆开。
    相比两次 encode，显存峰值减半（单次 forward pass）。
    每个 mini-batch 后主动 empty_cache 释放 PyTorch 显存缓存。
    """
    import torch
    model = _get_model()
    n = len(sig_texts)
    all_texts = sig_texts + ctx_texts   # [sig0, sig1, ..., ctx0, ctx1, ...]
    all_vecs: list[list[float]] = []

    for i in range(0, len(all_texts), batch_size):
        batch = all_texts[i: i + batch_size]
        vecs = model.encode(
            batch,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        all_vecs.extend(vecs.tolist())

    # 全部批次结束后统一释放一次，避免批间空隙饥饿 GPU
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    sig_vecs = all_vecs[:n]
    ctx_vecs = all_vecs[n:]
    return sig_vecs, ctx_vecs


async def embed_texts_merged(
    sig_texts: list[str],
    ctx_texts: list[str],
) -> tuple[list[list[float]], list[list[float]]]:
    """异步接口，复用全局线程池，不阻塞事件循环。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _get_executor(),
        _sync_embed_merged,
        sig_texts,
        ctx_texts,
        config.EMBED_BATCH_SIZE_GPU,
    )


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    単一リスト embed（vector_search / reranker 用）。
    _sync_embed_merged を使わず直接 encode して無駄な2倍処理を避ける。
    """
    def _sync(t: list[str], bs: int) -> list[list[float]]:
        import torch
        model = _get_model()
        results: list[list[float]] = []
        for i in range(0, len(t), bs):
            batch = t[i: i + bs]
            vecs = model.encode(
                batch,
                batch_size=bs,
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            results.extend(vecs.tolist())
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return results

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _get_executor(), _sync, texts, config.EMBED_BATCH_SIZE_GPU
    )


async def preload():
    """startup 时异步预加载模型，不阻塞 FastAPI 启动。"""
    global _preload_task

    async def _do_preload():
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_get_executor(), _get_model)
            logger.info("bge-m3 preload complete")
        except Exception as e:
            logger.error("bge-m3 preload failed: %s", e)

    _preload_task = asyncio.create_task(_do_preload())


def should_embed(unit: dict) -> bool:
    """过滤不值得embed的unit：getter/setter、空方法、EL表达式。"""
    unit_type = unit.get("unit_type", "")
    name      = unit.get("name", "").lower()
    body      = unit.get("body_text", "") or ""
    calls     = unit.get("calls", []) or []
    body_lines = [l for l in body.splitlines() if l.strip()]

    if config.FILTER_EL_EXPRESSIONS and unit_type == "el_expression":
        return False
    if config.FILTER_EMPTY_METHODS and not body.strip():
        return False
    if config.FILTER_EMPTY_METHODS and len(body_lines) <= 1 and not calls:
        return False
    if config.FILTER_GETTERS_SETTERS:
        prefixes = ("get", "set", "is", "has", "with", "to")
        if (
            any(name.startswith(p) for p in prefixes)
            and len(body_lines) <= config.MIN_BODY_LINES_TO_EMBED
            and not calls
        ):
            return False
    return True


async def embed_units_gpu(
    units: list[dict[str, Any]],
    skip_filter: bool = False,
) -> list[dict[str, Any]]:
    """
    主入口：embed → 返回embed结果。
    skip_filter=True 时跳过 should_embed（调用方已过滤）。
    sig + ctx 合并一次 encode，显存峰值减半。
    """
    import time

    embeddable = units if skip_filter else [u for u in units if should_embed(u)]
    if not skip_filter:
        logger.info("to_embed=%d (filtered %d)", len(embeddable), len(units) - len(embeddable))
    if not embeddable:
        return []

    sig_texts = [u.get("signature") or u.get("name", "") for u in embeddable]

    def _build_ctx(u: dict) -> str:
        """
        ctx 向量文本：签名 + javadoc/注释 + 参数名 + 方法体前300字。
        比只用 comment+summary 更能对齐自然语言检索意图。
        """
        parts = []
        sig = u.get("signature") or u.get("name", "")
        if sig:
            parts.append(sig)
        doc = u.get("comment", "") or ""
        if doc:
            parts.append(doc[:200])
        param_names = u.get("param_names") or []
        if param_names:
            parts.append(" ".join(param_names))
        body = (u.get("body_text") or "")[:300]
        if body:
            parts.append(body)
        return " ".join(parts).strip()

    ctx_texts = [_build_ctx(u) for u in embeddable]

    t0 = time.time()
    # 一次 encode 拿两路向量，显存峰值减半
    sig_vecs, ctx_vecs = await embed_texts_merged(sig_texts, ctx_texts)
    logger.info("Embedding done: %d units in %.1fs", len(embeddable), time.time() - t0)

    return [
        {
            "id":       unit["qualified_name"],
            "sig_vec":  sig_vecs[i],
            "ctx_vec":  ctx_vecs[i],
            "metadata": {
                "qualified_name": unit["qualified_name"],
                "name":           unit["name"],
                "language":       unit["language"],
                "unit_type":      unit["unit_type"],
                "file_path":      unit.get("file_path", ""),
                "start_line":     int(unit.get("start_line", 0)),
                "summary":        unit.get("summary", ""),
                "signature":      unit.get("signature", ""),
                "repo_id":        int(unit.get("repo_id", 0)),
                # 结构化字段（N4/N5 新增）
                "class_name":     unit.get("class_name", ""),
                "param_names":    unit.get("param_names") or [],
                "param_types":    unit.get("param_types") or [],
                "return_type":    unit.get("return_type", ""),
            },
        }
        for i, unit in enumerate(embeddable)
    ]


def get_model_info() -> dict:
    """只查询加载状态，不触发模型加载（health 接口用）。"""
    if _model is None:
        return {"loaded": False, "device": config.EMBED_DEVICE}
    try:
        return {
            "loaded": True,
            "model":  config.EMBED_MODEL_NAME,
            "device": str(_model.device),
            "dim":    _model.get_embedding_dimension(),
        }
    except Exception as e:
        return {"loaded": False, "error": str(e)}
