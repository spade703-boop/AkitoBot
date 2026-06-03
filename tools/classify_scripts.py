"""Stage A: 剧本脚本分类工具（dry-run 打印分界 → 用户确认后落盘）。

用法：
  py tools/classify_scripts.py           # dry-run：只打印分类结果
  py tools/classify_scripts.py --write   # 确认无误后落盘（写回 akito_scripts.json，打 type 字段）

分类规则：
  home   — 中文 context（纯中文概览，不含日文假名/角色名换行标签）
  story  — context 含日文假名 或 含 "角色名:" 格式的换行标签
  noise  — context 形如「（剧情开场/无前文）来源: event_xxx.asset」
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

# Windows GBK 终端兼容
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None

DATA_DIRS = [
    Path("data/content"),
    Path("../data/content"),
    Path("../../data/content"),
]
SCRIPT_FILENAME = "akito_scripts.json"


def find_scripts() -> Path | None:
    for d in DATA_DIRS:
        p = d / SCRIPT_FILENAME
        if p.exists():
            return p
    return None


def classify(ctx: str) -> str:
    """返回 "home" / "story" / "noise"。"""
    # noise: 无前文/剧情开场 + asset 标记
    if re.search(r"[（(](?:剧情开场|无前文)[)）]", ctx) or "asset" in ctx:
        return "noise"
    # story: 含日文假名 或 角色名: 换行标签
    if re.search(r"[぀-ゟ゠-ヿ]", ctx):
        return "story"
    if re.search(r"\n\s*[^：:]+[：:]", ctx):
        return "story"
    # home: 中文 context
    return "home"


def main() -> None:
    path = find_scripts()
    if path is None:
        print(f"❌ 未找到 {SCRIPT_FILENAME}")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    print(f"📖 读取 {len(data)} 条脚本 from {path}\n")

    # 分类并标记
    counts: dict[str, int] = {"home": 0, "story": 0, "noise": 0}
    boundaries: list[tuple[int, str]] = []  # (index, prev_type→new_type)
    prev_type: str | None = None

    for i, entry in enumerate(data):
        ctx = entry.get("context", "")
        tp = classify(ctx)
        entry["_type"] = tp
        counts[tp] += 1
        if prev_type is not None and tp != prev_type:
            boundaries.append((i, f"{prev_type} → {tp}"))
        prev_type = tp

    # 打印统计
    print("=" * 60)
    print("📊 分类统计")
    print("=" * 60)
    for tp, cnt in counts.items():
        pct = cnt / len(data) * 100
        print(f"  {tp:6s}: {cnt:5d}  ({pct:.1f}%)")
    print()

    # 打印分界
    print("=" * 60)
    print(f"🔀 分界点 ({len(boundaries)} 处)")
    print("=" * 60)
    for idx, desc in boundaries[:30]:
        print(f"  [{idx:4d}] {desc}")
    if len(boundaries) > 30:
        print(f"  ... 省略 {len(boundaries) - 30} 处")
    print()

    # 打印各类型示例（每侧 3 条）
    print("=" * 60)
    print("📋 分类示例 (各取前/后 3 条)")
    print("=" * 60)
    for tp in ("home", "story", "noise"):
        entries = [e for e in data if e["_type"] == tp]
        if not entries:
            print(f"\n  [{tp}] 无条目")
            continue
        print(f"\n  [{tp}] 共 {len(entries)} 条 — 前 3:")
        for e in entries[:3]:
            ctx = e["context"][:80]
            dl = e.get("dialogue", "")[:60]
            print(f"    ctx: {ctx}")
            print(f"    dl:  {dl}")
            print()
        if len(entries) > 6:
            print("  ... 后 3:")
            for e in entries[-3:]:
                ctx = e["context"][:80]
                dl = e.get("dialogue", "")[:60]
                print(f"    ctx: {ctx}")
                print(f"    dl:  {dl}")
                print()

    # 噪音清单
    noise_entries = [e for e in data if e["_type"] == "noise"]
    if noise_entries:
        print("=" * 60)
        print("🗑️  噪音清单（将剔除，不纳入检索）")
        print("=" * 60)
        for _, e in enumerate(noise_entries):
            orig_idx = data.index(e) if e in data else -1
            print(f"  [{orig_idx:4d}] ctx: {e['context'][:100]}")

    # --write 落盘
    if "--write" in sys.argv:
        if "--yes" not in sys.argv:
            print("\n⚠️  确认落盘？将覆盖原文件。用 --yes 跳过确认 / Ctrl+C 取消")
            try:
                input()
            except EOFError:
                print("❌ 非交互模式，请加 --yes 确认落盘")
                sys.exit(1)
        # 移除 _type 内部标记，写入 type 字段
        for e in data:
            e["type"] = e.pop("_type")
        out = json.dumps(data, ensure_ascii=False, indent=2)
        path.write_text(out, encoding="utf-8")
        print(f"✅ 已写入 {path}（{len(data)} 条，含 type 字段）")
    else:
        print("\n💡 这是 dry-run。确认无误后加 --write 落盘。")


if __name__ == "__main__":
    main()
