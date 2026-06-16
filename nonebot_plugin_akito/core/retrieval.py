"""通用语义检索引擎：每轮 embed 一次用户消息、并发检索各语料、只注入相关片段。
可用时在 cosine 粗召回后经 bge-reranker-v2-m3 精排 + 阈值过滤（失败回退纯 cosine，零回归）。

.npz schema（每语料）：
  vectors (N×1024 float32)  — 原始 embedding
  mean    (1024 float32)     — 语料均值（query 与语料都减此值做中心化）
  indices (N int32)          — 行 → 源 DB 下标
  count   (int)              — 生成时的 DB 长度（用于校验）
  fingerprint (str)          — 语料文本指纹（用于校验内容是否已变化）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot.log import logger

from . import np
from .paths import iter_data_roots
from .retrieval_assets import (
    build_corpus_fingerprint,
    pjsk_retrieval_text,
    script_retrieval_entries,
    script_retrieval_text,
)

# ── 语料注册表 ──────────────────────────────────────────────────────────────
# db: 惰性取值函数（避免循环 import）；npz: .npz 文件名；doc_text: 条目 → 精排文本


def _script_doc_text(entry: dict) -> str:
    """剧本条目 → 精排文本：cn_key 优先、缺失回退 context（与 build_embeddings.py 的 embed 文本一致）。"""
    return script_retrieval_text(entry)


def _pjsk_doc_text(entry: dict) -> str:
    """PJSK 条目 → 精排文本：「category text」拼接（与 build_embeddings.py 一致）。"""
    return pjsk_retrieval_text(entry)


def _live_retrieval_db(corpus: str, db: list[dict]) -> list[dict]:
    """Mirror the exact row subset used during embedding build."""
    if corpus == "scripts":
        return script_retrieval_entries(db)
    return db


_registry: dict[str, dict[str, Any]] = {}


def _ensure_registry() -> None:
    """惰性初始化注册表（避免 import 时循环依赖）。"""
    if _registry:
        return
    from .data import PJSK_ENTRIES, SCRIPT_DB

    _registry["scripts"] = {"db": SCRIPT_DB, "npz": "scripts_embeddings.npz", "doc_text": _script_doc_text}
    _registry["pjsk"] = {"db": PJSK_ENTRIES, "npz": "pjsk_embeddings.npz", "doc_text": _pjsk_doc_text}


# ── 缓存结构 ────────────────────────────────────────────────────────────────

class _Index:
    """单个语料的中心化缓存。"""
    __slots__ = ("centered", "norms", "mean", "indices", "count", "fingerprint")

    def __init__(self, vectors, mean, indices, count, fingerprint: str = ""):
        self.centered = vectors - mean  # (N, 1024) 中心化
        self.norms = (self.centered * self.centered).sum(axis=1) ** 0.5 + 1e-8  # 行范数
        self.mean = mean
        self.indices = indices
        self.count = count
        self.fingerprint = fingerprint


_INDICES: dict[str, _Index | None] = {}  # corpus → _Index | None（None = 不可用）


@dataclass(slots=True)
class RetrievalContext:
    """Shared per-message retrieval context."""

    original_query: str
    query: str
    expanded_query: str | None = None
    embedding: list[float] | None = None


@dataclass(slots=True)
class RetrievalResult:
    """Structured retrieval result."""

    status: str
    ids: list[int]
    reason: str = ""
    used_query: str = ""
    used_rerank: bool = False
    fell_back_to_cosine: bool = False

    @property
    def is_available(self) -> bool:
        return self.status != "unavailable"


# ── 加载 ────────────────────────────────────────────────────────────────────

def _find_npz_path(filename: str) -> Path | None:
    """在候选数据目录中定位 .npz 文件。"""
    for base in iter_data_roots():
        for sub in ("content", ""):
            p = base / sub / filename if sub else base / filename
            if p.exists():
                return p
    return None


def _load_npz(corpus: str) -> _Index | None:
    """加载单个语料的 .npz 并建立中心化缓存；未找到 / 校验失败 / numpy 不可用 → None。"""
    if np is None:
        return None
    _ensure_registry()
    cfg = _registry.get(corpus)
    if cfg is None:
        return None

    path = _find_npz_path(cfg["npz"])
    if path is None:
        logger.debug(f"🔍 语料 [{corpus}] 无 .npz 文件，回退静态/随机行为")
        return None

    try:
        data = np.load(path)
        vectors = data["vectors"]
        mean = data["mean"]
        indices = data["indices"]
        count = int(data["count"])
        files = getattr(data, "files", None)
        if files is not None:
            fingerprint = str(data["fingerprint"]) if "fingerprint" in files else ""
        else:
            fingerprint = str(data.get("fingerprint", "")) if hasattr(data, "get") else ""
    except Exception as e:
        logger.warning(f"🔍 语料 [{corpus}] .npz 加载失败: {e}")
        return None

    db = cfg["db"]
    if count != len(db):
        logger.warning(
            f"🔍 语料 [{corpus}] .npz count({count}) ≠ DB长度({len(db)})，标记不可用"
        )
        return None

    doc_fn = cfg.get("doc_text")
    live_db = _live_retrieval_db(corpus, db)
    live_fingerprint = build_corpus_fingerprint(corpus, live_db, doc_fn) if doc_fn else ""
    if fingerprint and live_fingerprint and fingerprint != live_fingerprint:
        logger.warning(f"🔍 语料 [{corpus}] .npz fingerprint 已过期，标记不可用")
        return None

    logger.info(f"✅ 语料 [{corpus}] 加载完成: {count} 条, {vectors.shape[1]}d")
    return _Index(vectors, mean, indices, count, fingerprint=fingerprint or live_fingerprint)


def reload_indices() -> int:
    """重读所有 .npz 并重建缓存；返回成功加载的语料数。

    先清空 registry 再重建，确保 PJSK_ENTRIES 等重新赋值后的引用为最新。
    """
    _registry.clear()
    _ensure_registry()
    loaded = 0
    for corpus in list(_registry):
        idx = _load_npz(corpus)
        _INDICES[corpus] = idx
        if idx is not None:
            loaded += 1
    if loaded == len(_registry):
        logger.info(f"🔄 检索引擎重载完成: {loaded}/{len(_registry)} 语料可用")
    else:
        logger.debug(f"🔄 检索引擎重载完成: {loaded}/{len(_registry)} 语料可用（部分/全部降级，正常回退）")
    return loaded


# 启动时加载一次
reload_indices()


# ── 重排序精排（bge-reranker-v2-m3） ─────────────────────────────────────────
# cosine 粗召回 _RERANK_RECALL_K 条 → reranker 逐对精读打分 → 阈值过滤 → 取 top_k。

_RERANK_ENABLED = True  # 一键回退开关：False = 纯 cosine 排序（旧行为）
_RERANK_RECALL_K = 20  # cosine 粗召回条数（送入 reranker 的候选量）
_RERANK_MIN_SCORE = 0.1  # 相关分阈值，低于即丢弃；经 tools/eval_retrieval.py 实测调定：负例全场最高 0.090，扩散 blend 后的弱正例 0.166+


async def _rerank_candidates(corpus: str, query: str, idx: _Index, rows: Any, top_k: int) -> list[int] | None:
    """对 cosine 召回的候选行做 bge-reranker 精排 + 阈值过滤。三态返回：

    - None       精排不可用 / 失败 → 调用方回退 cosine 顺序（零回归）
    - []         精排成功但全部低于 _RERANK_MIN_SCORE → 调用方原样上抛（无相关命中）
    - [id, ...]  精排序的源 DB 下标，至多 top_k 条
    """
    try:
        cfg = _registry.get(corpus) or {}
        db = cfg.get("db")
        doc_fn = cfg.get("doc_text")
        if db is None or doc_fn is None:
            return None

        cand_ids: list[int] = []
        docs: list[str] = []
        for row in rows:
            orig = int(idx.indices[int(row)])
            if 0 <= orig < len(db):
                cand_ids.append(orig)
                docs.append(doc_fn(db[orig]))
        if not docs:
            return None

        from .api import rerank_documents

        ranked = await rerank_documents(query, docs, top_n=min(top_k, len(docs)))
        if ranked is None:
            return None

        kept = [cand_ids[i] for i, score in ranked if 0 <= i < len(cand_ids) and score >= _RERANK_MIN_SCORE]
        logger.debug(f"🔍 精排 [{corpus}] 召回{len(docs)} → 保留{len(kept)}")
        return kept[:top_k]
    except Exception as e:
        logger.warning(f"🔍 精排 [{corpus}] 异常，回退 cosine 顺序: {e}")
        return None


async def _ensure_query_embedding(ctx: RetrievalContext) -> list[float] | None:
    """Fill shared query embedding once per message."""
    if ctx.embedding is not None:
        return ctx.embedding
    from .api import embed_text

    qv = await embed_text(ctx.query)
    ctx.embedding = qv
    return qv


async def build_retrieval_context(
    query: str,
    *,
    enable_expansion: bool = True,
    expand_min_chars: int = 3,
) -> RetrievalContext:
    """Build a shared retrieval context for one user message."""
    query = (query or "").strip()
    ctx = RetrievalContext(original_query=query, query=query)
    if not enable_expansion or len(query) < expand_min_chars:
        return ctx
    try:
        from .api import expand_query_for_retrieval

        expanded = await expand_query_for_retrieval(query)
        if expanded:
            ctx.expanded_query = expanded
            ctx.query = f"{query} {expanded}"
    except Exception as e:
        logger.debug(f"🔍 build_retrieval_context 扩散失败，回退原 query: {e}")
    return ctx


# ── 检索 ────────────────────────────────────────────────────────────────────

async def retrieve(corpus: str, query: str, top_k: int) -> list[int] | None:
    """语义检索：cosine 粗召回 →（可用时）bge-reranker 精排 + 阈值过滤，返回源 DB 下标列表。

    三态返回：None = 任一环节不可用（降级回静态/随机行为）；
    [] = 检索正常但精排判定无任何相关条目；[id, ...] = 命中。
    精排失败时回退纯 cosine top-k（与旧行为一致）。
    """
    result = await retrieve_result(corpus, query, top_k)
    if result.status == "unavailable":
        return None
    return result.ids


async def retrieve_result(corpus: str, query: str, top_k: int, ctx: RetrievalContext | None = None) -> RetrievalResult:
    """Structured retrieval API with shared context support."""
    if np is None:
        return RetrievalResult(status="unavailable", ids=[], reason="numpy_unavailable", used_query=query)
    _ensure_registry()
    if corpus not in _registry:
        return RetrievalResult(status="unavailable", ids=[], reason="unknown_corpus", used_query=query)

    idx = _INDICES.get(corpus)
    if idx is None:
        return RetrievalResult(status="unavailable", ids=[], reason="index_unavailable", used_query=query)

    shared_ctx = ctx or RetrievalContext(original_query=query, query=query)
    if not shared_ctx.query:
        shared_ctx.query = query
    qv = await _ensure_query_embedding(shared_ctx)
    if qv is None:
        return RetrievalResult(status="unavailable", ids=[], reason="embed_unavailable", used_query=shared_ctx.query)

    try:
        qc = np.asarray(qv, dtype="float32") - idx.mean
        sims = (idx.centered @ qc) / (idx.norms * (np.linalg.norm(qc) + 1e-8) + 1e-8)
        recall_k = max(top_k, _RERANK_RECALL_K) if _RERANK_ENABLED else top_k
        top = np.argsort(-sims)[:recall_k]
        cosine_ids = [int(idx.indices[i]) for i in top[:top_k]]
    except Exception as e:
        logger.warning(f"🔍 检索 [{corpus}] 失败，降级: {e}")
        return RetrievalResult(status="unavailable", ids=[], reason="cosine_failed", used_query=shared_ctx.query)

    if not _RERANK_ENABLED:
        return RetrievalResult(status="hit", ids=cosine_ids, used_query=shared_ctx.query)
    reranked = await _rerank_candidates(corpus, shared_ctx.query, idx, top, top_k)
    if reranked is None:
        return RetrievalResult(
            status="hit",
            ids=cosine_ids,
            reason="rerank_unavailable",
            used_query=shared_ctx.query,
            used_rerank=True,
            fell_back_to_cosine=True,
        )
    if not reranked:
        return RetrievalResult(status="no_hit", ids=[], used_query=shared_ctx.query, used_rerank=True)
    return RetrievalResult(status="hit", ids=reranked, used_query=shared_ctx.query, used_rerank=True)
