"""检索精度评测工具：用黄金考题集量化 cosine 基线 vs bge-reranker 精排的命中率。

用法：
  py tools/eval_retrieval.py                 # compare：基线 + 精排同跑逐题对比（默认）
  py tools/eval_retrieval.py baseline        # 只跑 cosine 基线（不调 rerank API）
  py tools/eval_retrieval.py rerank          # cosine 召回 → 精排 → 阈值过滤
  py tools/eval_retrieval.py rerank 0.2      # 第二个参数覆盖阈值（默认 0.1）
  py tools/eval_retrieval.py compare 0.15

前置条件：
  - Python 3.9+（用跑 bot 的同一个解释器 / 虚拟环境即可，依赖完全相同；
    Linux 下把示例里的 py 换成对应的 python3）
  - 在仓库根目录运行；.env 中配置 SILICONFLOW_API_KEY
  - data/content/ 已有语料 JSON 与 .npz 向量库（先跑 tools/build_embeddings.py all）
  - pip install numpy openai aiohttp python-dotenv

考题集（tools/eval_set.json，纯文本可直接编辑）每条字段：
  query        模拟的群友原话
  corpus       "scripts"（剧本）或 "pjsk"（黑话）
  expect_any   top-k 条目文本包含任一子串即命中（scripts 匹配 cn_key+context+dialogue，pjsk 匹配 category+text）
  expect_none  true = 负例：精排 + 阈值后应零返回（cosine 基线无阈值，不参与判定）
  k            可选；默认 scripts=5、pjsk=6（与线上注入条数一致）
  note         可选备注，仅展示

与线上行为的差异（解读分数时注意）：
  - 线上 scripts 检索的 query 是「原文 + LLM 扩散关键词」blend；本工具直接用原文（免 DeepSeek 依赖、结果可复现）。
  - 精排臂对全部召回候选打分（线上只取 top_k），便于观察分数分布、确定阈值。

输出：逐题 top-k 明细（cos/rr 分数 + 命中标记）→ 汇总命中率对比 → 阈值调参辅助统计。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import statistics
import sys

import aiohttp
from dotenv import load_dotenv
import numpy as np
from openai import OpenAI

# Windows GBK 终端兼容（emoji/中文输出不崩）
if (
    sys.stdout.encoding
    and sys.stdout.encoding.lower() != "utf-8"
    and hasattr(sys.stdout, "reconfigure")
):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

DATA_CONTENT = Path("data/content")
EVAL_FILE = Path("tools/eval_set.json")

RECALL_K = 20  # cosine 粗召回条数（与 core/retrieval.py 的 _RERANK_RECALL_K 一致）
DEFAULT_THRESHOLD = 0.1
RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
EMBED_MODEL = "BAAI/bge-m3"


# ── 数据加载 ─────────────────────────────────────────────────────────────────

def load_eval_set() -> list[dict]:
    """读取考题集；缺文件 / 无合法考题直接退出。"""
    if not EVAL_FILE.exists():
        print(f"❌ 未找到 {EVAL_FILE}")
        sys.exit(1)
    data = json.loads(EVAL_FILE.read_text(encoding="utf-8-sig"))
    cases = []
    for i, c in enumerate(data.get("cases", [])):
        if not c.get("query") or c.get("corpus") not in ("scripts", "pjsk"):
            print(f"⚠️ 考题 #{i} 缺 query 或 corpus 非法，跳过")
            continue
        cases.append(c)
    if not cases:
        print("❌ 考题集为空")
        sys.exit(1)
    print(f"📖 考题集: {len(cases)} 题")
    return cases


def load_scripts_db() -> list[dict]:
    """全量剧本列表（含 noise——.npz 的 indices 指向全量下标）。"""
    path = DATA_CONTENT / "akito_scripts.json"
    if not path.exists():
        print(f"❌ 未找到 {path}")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_pjsk_db() -> list[dict]:
    """拍平的 PJSK 条目（与 build_embeddings.py 的 load_pjsk 一致）。"""
    path = DATA_CONTENT / "pjsk_knowledge.json"
    if not path.exists():
        print(f"❌ 未找到 {path}")
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    flat = []
    for item in data.get("knowledge_list", []):
        category = item.get("category", "")
        for entry in item.get("entries", []):
            flat.append({"category": category, "text": entry})
    return flat


def load_corpus(name: str) -> dict:
    """加载语料 db + .npz，并建立与 core/retrieval.py._Index 完全一致的中心化缓存。"""
    npz_path = DATA_CONTENT / f"{name}_embeddings.npz"
    if not npz_path.exists():
        print(f"❌ 未找到 {npz_path}（先跑 tools/build_embeddings.py {name}）")
        sys.exit(1)
    db = load_scripts_db() if name == "scripts" else load_pjsk_db()
    data = np.load(npz_path)
    vectors, mean, indices, count = data["vectors"], data["mean"], data["indices"], int(data["count"])
    if count != len(db):
        print(f"❌ 语料 [{name}] .npz count({count}) ≠ DB长度({len(db)})，请重跑 build_embeddings")
        sys.exit(1)
    centered = vectors - mean
    norms = (centered * centered).sum(axis=1) ** 0.5 + 1e-8
    print(f"✅ 语料 [{name}]: {len(db)} 条 DB / {vectors.shape[0]} 条向量")
    return {"db": db, "centered": centered, "norms": norms, "mean": mean, "indices": indices}


# ── 文本构造（与 core/retrieval.py 的 doc_text 口径一致） ─────────────────────

def doc_text(corpus: str, entry: dict) -> str:
    """送入 reranker 的文本（与 embed 文本一致）。"""
    if corpus == "scripts":
        text = (entry.get("cn_key") or "").strip() or (entry.get("context") or "").strip()
        return text or "（空）"
    text = f"{entry.get('category', '')} {entry.get('text', '')}".strip()
    return text or "（空）"


def match_text(corpus: str, entry: dict) -> str:
    """expect_any 的匹配面（比 embed 文本更宽，方便用任何记得住的原文出题）。"""
    if corpus == "scripts":
        return " ".join(str(entry.get(k) or "") for k in ("cn_key", "context", "dialogue"))
    return f"{entry.get('category', '')} {entry.get('text', '')}"


# ── 检索两臂 ─────────────────────────────────────────────────────────────────

def embed_query(client: OpenAI, text: str) -> list[float] | None:
    """单条 query embedding；失败返回 None（该题记为出错）。"""
    try:
        r = client.embeddings.create(model=EMBED_MODEL, input=text)
        return r.data[0].embedding
    except Exception as e:
        print(f"  ⚠️ embed 失败: {e}")
        return None


def recall(corpus_index: dict, qv: list[float], k: int) -> list[tuple[int, float]]:
    """cosine 粗召回：返回 [(行号, cos分)]，公式与生产 retrieve() 完全一致。"""
    qc = np.asarray(qv, dtype="float32") - corpus_index["mean"]
    sims = (corpus_index["centered"] @ qc) / (corpus_index["norms"] * (np.linalg.norm(qc) + 1e-8) + 1e-8)
    order = np.argsort(-sims)[:k]
    return [(int(r), float(sims[r])) for r in order]


async def _rerank_async(api_key: str, query: str, docs: list[str]) -> list[float] | None:
    """调 rerank API 给全部 docs 打分；返回与 docs 对齐的分数列表，失败返回 None。"""
    payload = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": docs,
        "top_n": len(docs),
        "return_documents": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
        RERANK_URL, json=payload, headers=headers
    ) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            print(f"  ⚠️ rerank API HTTP {resp.status}: {error_text[:120]}")
            return None
        data = await resp.json()
    scores: list[float] = [0.0] * len(docs)
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None
    for item in results:
        if isinstance(item, dict) and isinstance(item.get("index"), int) and 0 <= item["index"] < len(docs):
            try:
                scores[item["index"]] = float(item.get("relevance_score"))
            except (TypeError, ValueError):
                continue
    return scores


def rerank_scores(api_key: str, query: str, docs: list[str]) -> list[float] | None:
    """同步包装；任何异常 → None（该题精排臂记为出错）。"""
    try:
        return asyncio.run(_rerank_async(api_key, query, docs))
    except Exception as e:
        print(f"  ⚠️ rerank 失败: {e}")
        return None


# ── 评测主流程 ───────────────────────────────────────────────────────────────

def _fmt_entry(corpus: str, entry: dict) -> str:
    return doc_text(corpus, entry)[:60]


def _hit(corpus: str, entries: list[dict], expect_any: list[str]) -> bool:
    return any(sub in match_text(corpus, e) for e in entries for sub in expect_any)


def run() -> None:
    import os

    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if mode not in ("baseline", "rerank", "compare"):
        print(__doc__)
        sys.exit(1)
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_THRESHOLD

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key or "sk-" not in api_key:
        print("❌ 未配置有效的 SILICONFLOW_API_KEY（需以 sk- 开头）")
        sys.exit(1)
    client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")

    cases = load_eval_set()
    corpora = {name: load_corpus(name) for name in {c["corpus"] for c in cases}}
    use_rerank = mode in ("rerank", "compare")
    use_baseline = mode in ("baseline", "compare")
    print(f"🔨 模式={mode}  阈值={threshold}  召回={RECALL_K}\n")

    stats = {
        "pos_total": {}, "pos_base_hit": {}, "pos_rr_hit": {},  # per-corpus 正例统计
        "neg_total": 0, "neg_pass": 0,
        "hit_scores": [], "miss_scores": [], "neg_scores": [],  # 阈值调参辅助
        "errors": 0,
    }

    for case in cases:
        corpus, query = case["corpus"], case["query"]
        expect_any = case.get("expect_any") or []
        is_negative = bool(case.get("expect_none"))
        k = int(case.get("k") or (6 if corpus == "pjsk" else 5))
        note = f"  // {case['note']}" if case.get("note") else ""
        tag = "负例" if is_negative else "正例"
        print(f"📝 [{corpus}·{tag}] {query}{note}")

        qv = embed_query(client, query)
        if qv is None:
            stats["errors"] += 1
            continue
        rec = recall(corpora[corpus], qv, RECALL_K)
        db, indices = corpora[corpus]["db"], corpora[corpus]["indices"]
        cand = [(int(indices[row]), cos) for row, cos in rec if 0 <= int(indices[row]) < len(db)]

        if not is_negative:
            stats["pos_total"][corpus] = stats["pos_total"].get(corpus, 0) + 1

        if use_baseline:
            base_entries = [db[i] for i, _ in cand[:k]]
            if is_negative:
                print("   基线: n/a（cosine 无阈值，负例不参与判定）")
            else:
                ok = _hit(corpus, base_entries, expect_any)
                stats["pos_base_hit"][corpus] = stats["pos_base_hit"].get(corpus, 0) + (1 if ok else 0)
                print(f"   基线: {'✅ 命中' if ok else '❌ 未中'}")
                for rank, (i, cos) in enumerate(cand[:k], 1):
                    mark = "✔" if expect_any and any(s in match_text(corpus, db[i]) for s in expect_any) else " "
                    print(f"     {rank}. cos={cos:+.3f} {mark} {_fmt_entry(corpus, db[i])}")

        if use_rerank:
            docs = [doc_text(corpus, db[i]) for i, _ in cand]
            scores = rerank_scores(api_key, query, docs)
            if scores is None:
                stats["errors"] += 1
                print("   精排: ⚠️ 调用失败，跳过")
                print()
                continue
            ranked = sorted(zip(cand, scores), key=lambda x: x[1], reverse=True)
            kept = [(i, cos, rr) for (i, cos), rr in ranked if rr >= threshold][:k]
            kept_entries = [db[i] for i, _, _ in kept]

            if is_negative:
                stats["neg_total"] += 1
                stats["neg_scores"].append(max(scores) if scores else 0.0)
                ok = not kept
                stats["neg_pass"] += 1 if ok else 0
                print(f"   精排: {'✅ 通过（零返回）' if ok else f'❌ 仍保留 {len(kept)} 条'}  全场最高分={max(scores):.3f}")
                for rank, (i, cos, rr) in enumerate(kept, 1):
                    print(f"     {rank}. rr={rr:.3f} cos={cos:+.3f}   {_fmt_entry(corpus, db[i])}")
            else:
                ok = _hit(corpus, kept_entries, expect_any)
                stats["pos_rr_hit"][corpus] = stats["pos_rr_hit"].get(corpus, 0) + (1 if ok else 0)
                print(f"   精排: {'✅ 命中' if ok else '❌ 未中'}（阈值后保留 {len(kept)}/{len(cand)}）")
                for rank, (i, cos, rr) in enumerate(kept, 1):
                    matched = expect_any and any(s in match_text(corpus, db[i]) for s in expect_any)
                    mark = "✔" if matched else " "
                    print(f"     {rank}. rr={rr:.3f} cos={cos:+.3f} {mark} {_fmt_entry(corpus, db[i])}")
                # 调参关键信息：期望条目在全部召回里的最高精排分（即使低于阈值被丢弃也显示）
                best_match_rr = max(
                    (rr for (i, _), rr in ranked if any(s in match_text(corpus, db[i]) for s in expect_any)),
                    default=None,
                )
                if best_match_rr is not None:
                    dropped = "（低于阈值被丢弃）" if best_match_rr < threshold else ""
                    print(f"     ↳ 期望条目全场最高分 rr={best_match_rr:.3f}{dropped}")
                else:
                    print(f"     ↳ 召回 {len(cand)} 条中无任何期望条目（语料缺内容或 expect_any 需校准）")
                # 调参辅助：top-k 内命中/未命中条目的精排分分布
                for (i, _), rr in ranked[:k]:
                    matched = expect_any and any(s in match_text(corpus, db[i]) for s in expect_any)
                    (stats["hit_scores"] if matched else stats["miss_scores"]).append(rr)
        print()

    _print_summary(stats, mode, threshold, use_baseline, use_rerank)


def _print_summary(stats: dict, mode: str, threshold: float, use_baseline: bool, use_rerank: bool) -> None:
    print("═" * 60)
    print(f"📊 汇总（模式={mode}，阈值={threshold}）")
    total = sum(stats["pos_total"].values())
    for corpus in sorted(stats["pos_total"]):
        n = stats["pos_total"][corpus]
        line = f"   [{corpus}] 正例 {n} 题:"
        if use_baseline:
            line += f"  基线命中 {stats['pos_base_hit'].get(corpus, 0)}/{n}"
        if use_rerank:
            line += f"  精排命中 {stats['pos_rr_hit'].get(corpus, 0)}/{n}"
        print(line)
    if total:
        line = f"   [总计]  正例 {total} 题:"
        if use_baseline:
            line += f"  基线 {sum(stats['pos_base_hit'].values())}/{total}"
        if use_rerank:
            line += f"  精排 {sum(stats['pos_rr_hit'].values())}/{total}"
        print(line)
    if use_rerank and stats["neg_total"]:
        print(f"   [负例]  通过率: {stats['neg_pass']}/{stats['neg_total']}（精排+阈值后零返回为通过）")
    if stats["errors"]:
        print(f"   ⚠️ 出错跳过 {stats['errors']} 题")

    if use_rerank and (stats["hit_scores"] or stats["miss_scores"]):
        print("\n🎯 阈值调参辅助（精排分数分布，top-k 内）:")
        if stats["hit_scores"]:
            hs = stats["hit_scores"]
            print(f"   命中条目  : n={len(hs)}  中位={statistics.median(hs):.3f}  最低={min(hs):.3f}")
        if stats["miss_scores"]:
            ms = stats["miss_scores"]
            print(f"   未命中条目: n={len(ms)}  中位={statistics.median(ms):.3f}  最高={max(ms):.3f}")
        if stats["neg_scores"]:
            print(f"   负例最高分: {max(stats['neg_scores']):.3f}（阈值应高于此值才能拦住负例）")
        print("   建议: 在「未命中/负例的高分」与「命中条目的低分」之间取阈值，"
              "改 core/retrieval.py 的 _RERANK_MIN_SCORE")


if __name__ == "__main__":
    run()
