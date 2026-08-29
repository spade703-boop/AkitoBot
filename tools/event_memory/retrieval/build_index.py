"""Build the ignored EventMemory embedding index for runtime hybrid retrieval."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
DATA_CONTENT = ROOT / "data" / "content"
ASSET_PATH = DATA_CONTENT / "akito_event_memories.json"
OUTPUT_PATH = DATA_CONTENT / "event_memory_embeddings.npz"
HELPER_PATH = ROOT / "nonebot_plugin_akito" / "core" / "retrieval_assets.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_helpers():
    spec = importlib.util.spec_from_file_location("akito_retrieval_assets", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载共享检索助手: {HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_events() -> list[dict]:
    payload = json.loads(ASSET_PATH.read_text(encoding="utf-8-sig"))
    helpers = _load_helpers()
    return helpers.event_memory_retrieval_entries(payload)


def build_index(events: list[dict], client: Any) -> Path:
    import numpy as np

    helpers = _load_helpers()
    texts = [helpers.event_memory_retrieval_text(event) for event in events]
    fingerprint = helpers.build_corpus_fingerprint("event_memory", events, helpers.event_memory_retrieval_text)
    vectors: list[np.ndarray] = []
    indices: list[int] = []
    failed_indices: list[int] = []
    for index, text in enumerate(texts):
        try:
            response = client.embeddings.create(model="BAAI/bge-m3", input=text)
            vectors.append(np.asarray(response.data[0].embedding, dtype=np.float32))
            indices.append(index)
        except Exception as error:
            failed_indices.append(index)
            print(f"  ⚠️ [{index}] embed 失败: {error}")
        if (index + 1) % 50 == 0 or index == len(texts) - 1:
            print(f"  ... {index + 1}/{len(texts)}")
    if failed_indices or len(vectors) != len(events):
        preview = ", ".join(str(index) for index in failed_indices[:20])
        if len(failed_indices) > 20:
            preview += ", ..."
        raise RuntimeError(
            "EventMemory embedding 不完整，拒绝替换正式索引："
            f"成功 {len(vectors)}/{len(events)}，失败下标 [{preview}]"
        )
    matrix = np.stack(vectors).astype(np.float32, copy=False)
    temporary = OUTPUT_PATH.with_suffix(".tmp.npz")
    np.savez_compressed(
        temporary,
        vectors=matrix,
        mean=matrix.mean(axis=0),
        indices=np.asarray(indices, dtype=np.int32),
        count=np.int32(len(events)),
        fingerprint=np.asarray(fingerprint),
    )
    temporary.replace(OUTPUT_PATH)
    return OUTPUT_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 EventMemory 混合检索向量索引")
    parser.add_argument("--check", action="store_true", help="只检查事件数量和检索文本，不调用 API 或写入索引")
    args = parser.parse_args()
    events = load_events()
    if not events:
        raise RuntimeError(f"事件资产为空: {ASSET_PATH}")
    print(f"📖 EventMemory: {len(events)} 条")
    if args.check:
        print("✅ 检索文本检查通过；未调用 API，未写入索引")
        return 0
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key or "sk-" not in api_key:
        print("❌ 未配置有效的 SILICONFLOW_API_KEY（需以 sk- 开头）")
        return 1
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")
    output = build_index(events, client)
    print(f"✅ 已保存 {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
