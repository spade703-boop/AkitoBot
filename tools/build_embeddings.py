"""预计算工具：为语料逐条 embed 并生成 .npz 向量库。

用法：
  py tools/build_embeddings.py scripts   # 只构建剧本（home + story，排除 noise）
  py tools/build_embeddings.py pjsk      # 只构建 PJSK 黑话库
  py tools/build_embeddings.py all       # 全量构建

前置条件：
  - .env 中配置 SILICONFLOW_API_KEY
  - pip install numpy openai python-dotenv
  - akito_scripts.json 已含 type 字段（先跑 tools/classify_scripts.py --write）

.npz schema（每语料统一）：
  vectors  (M×1024 float32) — 被选中条目的原始 embedding（M = 子集长度）
  mean     (1024 float32)    — 语料均值
  indices  (M int32)         — 行 → 源 DB 原始下标（非 arange）
  count    (int)             — 源 DB 全量长度（用于加载时校验一致性）

输出：
  data/content/scripts_embeddings.npz  — 剧本（home+story，~2476 条；embed key=cn_key，缺失回退 context）
  data/content/pjsk_embeddings.npz     — PJSK 黑话库
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from dotenv import load_dotenv
from openai import OpenAI

# Windows GBK 终端兼容（emoji/中文输出不崩）
if (
    sys.stdout.encoding
    and sys.stdout.encoding.lower() != "utf-8"
    and hasattr(sys.stdout, "reconfigure")
):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

# 数据路径
DATA_CONTENT = Path("data/content")
SCRIPT_FILE = DATA_CONTENT / "akito_scripts.json"
PJSK_FILE = DATA_CONTENT / "pjsk_knowledge.json"
OUT_DIR = DATA_CONTENT
_HELPER_PATH = Path(__file__).resolve().parents[1] / "nonebot_plugin_akito" / "core" / "retrieval_assets.py"
_HELPER_SPEC = importlib.util.spec_from_file_location("akito_retrieval_assets", _HELPER_PATH)
if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
    raise RuntimeError(f"无法加载共享检索助手: {_HELPER_PATH}")
_HELPER_MODULE = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPER_MODULE)
build_corpus_fingerprint = _HELPER_MODULE.build_corpus_fingerprint
flatten_pjsk_knowledge = _HELPER_MODULE.flatten_pjsk_knowledge
pjsk_retrieval_text = _HELPER_MODULE.pjsk_retrieval_text
script_retrieval_text = _HELPER_MODULE.script_retrieval_text


def load_scripts():
    """返回 ((i, entry), full_len) 清单，选取 type∈{home,story}、排除 noise，保留原始下标。"""
    if not SCRIPT_FILE.exists():
        print(f"❌ 未找到 {SCRIPT_FILE}")
        sys.exit(1)
    data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8-sig"))
    items = [(i, e) for i, e in enumerate(data) if e.get("type") in ("home", "story")]
    full_len = len(data)
    print(f"📖 剧本: {full_len} 条 → home+story: {len(items)} 条（排除 noise）")
    return items, full_len


def load_pjsk():
    """返回 ((i, entry), full_len) 清单（统一接口）。"""
    if not PJSK_FILE.exists():
        print(f"❌ 未找到 {PJSK_FILE}")
        sys.exit(1)
    data = json.loads(PJSK_FILE.read_text(encoding="utf-8-sig"))
    flat = flatten_pjsk_knowledge(data, include_drafts=False)
    items = [(i, e) for i, e in enumerate(flat)]
    full_len = len(flat)
    print(f"📖 PJSK: {full_len} 条目")
    return items, full_len


def build(
    indexed_items: list[tuple[int, dict]],
    full_len: int,
    out_name: str,
    embed_key: str,
    client: OpenAI,
    fallback_key: str | None = None,
    corpus_name: str = "",
) -> Path:
    """逐条 embed → 堆叠 → 存 .npz（indices=原始下标，count=全量长度）。

    embed_key: "cn_key"（剧本 cn_key→context 兜底）/ "text"（PJSK）。
    fallback_key: embed_key 取值为空时回退到此字段。
    """
    import numpy as np

    total = len(indexed_items)
    kept_vectors: list[np.ndarray] = []
    kept_indices: list[int] = []
    db_subset: list[dict] = [entry for _, entry in indexed_items]
    text_builder = pjsk_retrieval_text if embed_key == "text" else script_retrieval_text
    fingerprint = build_corpus_fingerprint(corpus_name or out_name, db_subset, text_builder)
    print(
        f"🔨 开始构建 {out_name} ({total} 条, key={embed_key}"
        + (f" fallback={fallback_key})" if fallback_key else ")")
    )

    for row, (orig_i, entry) in enumerate(indexed_items):
        if embed_key == "text":
            text = pjsk_retrieval_text(entry)
        else:
            text = script_retrieval_text(entry)
            if text == "（空）" and fallback_key:
                text = entry.get(fallback_key, "") or ""
        if not text.strip():
            text = "（空）"
        try:
            r = client.embeddings.create(model="BAAI/bge-m3", input=text)
            vec = r.data[0].embedding
            kept_vectors.append(np.array(vec, dtype=np.float32))
            kept_indices.append(orig_i)
        except Exception as e:
            print(f"  ⚠️ [{row}] embed 失败 (orig_idx={orig_i}): {e}")
        if (row + 1) % 50 == 0 or row == total - 1:
            print(f"  ... {row + 1}/{total}")

    if not kept_vectors:
        print("❌ 没有任何 embedding 构建成功，放弃写入 .npz")
        sys.exit(1)

    vectors = np.stack(kept_vectors).astype(np.float32, copy=False)
    orig_indices = np.asarray(kept_indices, dtype=np.int32)
    mean = vectors.mean(axis=0)
    count = np.int32(full_len)

    out_path = OUT_DIR / out_name
    np.savez_compressed(
        out_path,
        vectors=vectors,
        mean=mean,
        indices=orig_indices,
        count=count,
        fingerprint=np.asarray(fingerprint),
    )
    print(f"✅ 已保存 {out_path} ({vectors.shape[0]}×{vectors.shape[1]}，count={full_len})")
    return out_path


def main() -> None:
    import os

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key or "sk-" not in api_key:
        print("❌ 未配置有效的 SILICONFLOW_API_KEY（需以 sk- 开头）")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")

    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("scripts", "all"):
        items, full_len = load_scripts()
        build(
            items,
            full_len,
            "scripts_embeddings.npz",
            embed_key="cn_key",
            fallback_key="context",
            client=client,
            corpus_name="scripts",
        )

    if target in ("pjsk", "all"):
        items, full_len = load_pjsk()
        # PJSK intro 不参与检索（常驻注入），仅条目参与 embed
        build(items, full_len, "pjsk_embeddings.npz", embed_key="text", client=client, corpus_name="pjsk")

    print("\n🎉 构建完成。")


if __name__ == "__main__":
    main()
