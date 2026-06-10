"""通用语义检索引擎：每轮 embed 一次用户消息、并发检索各语料、只注入相关片段。

.npz schema（每语料）：
  vectors (N×1024 float32)  — 原始 embedding
  mean    (1024 float32)     — 语料均值（query 与语料都减此值做中心化）
  indices (N int32)          — 行 → 源 DB 下标
  count   (int)              — 生成时的 DB 长度（用于校验）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot.log import logger

from . import np
from .paths import iter_data_roots

# ── 语料注册表 ──────────────────────────────────────────────────────────────
# db: 惰性取值函数（避免循环 import）；npz: .npz 文件名
_registry: dict[str, dict[str, Any]] = {}


def _ensure_registry() -> None:
    """惰性初始化注册表（避免 import 时循环依赖）。"""
    if _registry:
        return
    from .data import PJSK_ENTRIES, SCRIPT_DB

    _registry["scripts"] = {"db": SCRIPT_DB, "npz": "scripts_embeddings.npz"}
    _registry["pjsk"] = {"db": PJSK_ENTRIES, "npz": "pjsk_embeddings.npz"}


# ── 缓存结构 ────────────────────────────────────────────────────────────────

class _Index:
    """单个语料的中心化缓存。"""
    __slots__ = ("centered", "norms", "mean", "indices", "count")

    def __init__(self, vectors, mean, indices, count):
        self.centered = vectors - mean  # (N, 1024) 中心化
        self.norms = (self.centered * self.centered).sum(axis=1) ** 0.5 + 1e-8  # 行范数
        self.mean = mean
        self.indices = indices
        self.count = count


_INDICES: dict[str, _Index | None] = {}  # corpus → _Index | None（None = 不可用）


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
    except Exception as e:
        logger.warning(f"🔍 语料 [{corpus}] .npz 加载失败: {e}")
        return None

    db = cfg["db"]
    if count != len(db):
        logger.warning(
            f"🔍 语料 [{corpus}] .npz count({count}) ≠ DB长度({len(db)})，标记不可用"
        )
        return None

    logger.info(f"✅ 语料 [{corpus}] 加载完成: {count} 条, {vectors.shape[1]}d")
    return _Index(vectors, mean, indices, count)


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


# ── 检索 ────────────────────────────────────────────────────────────────────

async def retrieve(corpus: str, query: str, top_k: int) -> list[int] | None:
    """语义检索：返回源 DB 下标列表；任一环节失败返回 None（降级回静态/随机行为）。"""
    if np is None:
        return None
    _ensure_registry()
    if corpus not in _registry:
        return None

    idx = _INDICES.get(corpus)
    if idx is None:
        return None

    from .api import embed_text

    qv = await embed_text(query)
    if qv is None:
        return None

    try:
        qc = np.asarray(qv, dtype="float32") - idx.mean
        sims = (idx.centered @ qc) / (idx.norms * (np.linalg.norm(qc) + 1e-8) + 1e-8)
        top = np.argsort(-sims)[:top_k]
        return [int(idx.indices[i]) for i in top]
    except Exception as e:
        logger.warning(f"🔍 检索 [{corpus}] 失败，降级: {e}")
        return None
