"""语料库结构化重整工具：用 DeepSeek 给每条剧本生成 cn_key + category + topics。

用法：
  py tools/enrich_scripts.py --sample 30        # 抽样预览 30 条质量
  py tools/enrich_scripts.py --sample 30 --type story  # 只看 story
  py tools/enrich_scripts.py --write             # 全量富集落盘（断点续跑）
  py tools/enrich_scripts.py --write --limit 100 # 只跑 100 条（调试）

前置条件：
  - .env 中配置 DEEPSEEK_API_KEY
  - akito_scripts.json 已含 type 字段（先跑 tools/classify_scripts.py --write）

输出：
  data/content/akito_scripts.json 原地更新（加 cn_key / category / topics 字段）

成本：~2300 次小调用（home 仅分类不生成 cn_key，~176 次；story 生成全量~2300 次），
      DeepSeek 合计约几块钱。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Windows GBK 终端兼容（emoji/中文输出不崩）
if (
    sys.stdout.encoding
    and sys.stdout.encoding.lower() != "utf-8"
    and hasattr(sys.stdout, "reconfigure")
):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_FILE = Path("data/content/akito_scripts.json")
SAVE_EVERY = 50  # 每 N 条增量写盘一次

# ── 闭集定义 ─────────────────────────────────────────────────────────────────

CATEGORIES = {
    "冬弥·彰冬": "与青柳冬弥的互动、提及冬弥、或他人对彰冬关系的看法",
    "VBS伙伴": "与小豆沢こはね（心羽）、白石杏（アン）的互动",
    "VBS虚拟歌手": "与初音ミク、镜音リン、镜音レン、巡音ルカ、MEIKO、KAITO 的互动（VBS SEKAI 内常驻，非跨团）",
    "跨团客串": "与其它团可养成角色的互动（含彰人姐姐东云绘名）",
    "其他NPC·路人": "与白石谦、古柳大河、凪、远野新等传奇/对手，或社长、店员、路人等非可养成角色的互动",
    "彰人独白": "无特定他人、纯彰人内心独白或旁白",
    "家园·对事件": "家园系统：对某事件/状况的看法",
    "家园·对物品": "家园系统：对家具/物品的看法",
    "家园·对人&共度": "家园系统：对某人的看法 & 与某人共度的经历回忆",
    "其它": "无法归入以上任何类别",
}

TOPICS = {
    "音乐·演出": "聊唱歌、表演、LIVE、舞台",
    "街头·比赛": "聊街头表演、RAD WEEKEND、竞赛对抗",
    "练习·努力·信念": "聊练习、排练、坚持、梦想、BEYOND THE WAY",
    "过去·RAD WEEKEND": "聊 RAD WEEKEND 历史、谦/大河/凪的过去",
    "情绪·内心": "心境、烦恼、孤独、喜悦、反思",
    "怕狗": "聊狗/怕狗/犬相关",
    "足球·过去": "聊足球、棒球等运动或过去经历",
    "游戏黑话·抽卡": "聊 PJSK 游戏机制、抽卡、打歌黑话",
    "其它话题": "无法归入以上任何话题标签",
}

# ── 可养成角色花名册（26 人）────────────────────────────────────────────────

_CHARACTER_ROSTER = """【可养成角色归属（26 人）】
冬弥·彰冬：青柳冬弥（トウヤ）
VBS伙伴：小豆沢こはね、白石杏（アン）
VBS虚拟歌手：初音ミク、镜音リン、镜音レン、巡音ルカ、MEIKO、KAITO
跨团客串：
  Leo/need：星乃一歌、天马咲希、望月穗波、日野森志步
  MORE MORE JUMP!：花里实乃理(みのり)、桐谷遥、桃井爱莉、日野森雫
  Wonderlands×Showtime：天马司、凤えむ、草薙寧々、神代类
  25時 Nightcord：宵崎奏、朝比奈まふゆ、东云绘名(=彰人姐姐)、晓山瑞希
【规则】以上 26 人外任何人名 = 其他NPC·路人，虚拟歌手不归跨团"""

# ── Prompt ───────────────────────────────────────────────────────────────────

_ENRICH_SYSTEM_PROMPT = f"""你是 PJSK 剧本分类助手，服务于东云彰人的台词库整理。
给定一条剧本（前文 context + 彰人台词 dialogue），输出三个字段。

【分类规则——优先级从高到低】
1. home（家园系统）→ category 必属 家园·对事件 / 家园·对物品 / 家园·对人&共度 之一。
2. story 有明确他人的对话 → 按人选：冬弥·彰冬 > VBS伙伴 > VBS虚拟歌手 > 跨团客串 > 其他NPC·路人（谁最占主导归谁；彰冬关系永远最高优先级。VBS虚拟歌手是SEKAI内常驻角色，优先于跨团客串。比如同时有冬弥和MEIKO→冬弥·彰冬，MEIKO和杏→VBS伙伴）。
3. story 无特定他人/纯彰人内心/旁白 → 彰人独白。
4. 以上都套不上 → 其它。

【category 闭集（9 类）】
{chr(10).join(f'- {k}：{v}' for k, v in CATEGORIES.items())}

【topics 主题标签（9 类，0–N 个，按内容选）】——在聊什么：
{chr(10).join(f'- {k}：{v}' for k, v in TOPICS.items())}

{_CHARACTER_ROSTER}

【输出格式（严格 JSON）】
对于 home 类型条目，cn_key 留空（工具侧自动复用 context），只输出 category + topics。
对于 story 类型条目，cn_key 写一句 15-30 字中文情境概括（聚焦话题本质，不逐字照抄 context）。
只输出合法 JSON，不要额外说明。"""


def _build_user_message(entry: dict) -> str:
    ctx = (entry.get("context") or "")[:600]
    dl = (entry.get("dialogue") or "")[:200]
    tp = entry.get("type", "?")
    return f"type: {tp}\n前文：{ctx}\n彰人台词：{dl}"


# ── 校验 ─────────────────────────────────────────────────────────────────────


def _validate(entry: dict) -> bool:
    """校验 category/topics 均在闭集内，不合格的归入兜底并告警。"""
    ok = True
    cat = entry.get("category", "")
    if cat not in CATEGORIES:
        print(f"  ⚠️ 非法 category [{cat}] → 归入「其它」")
        entry["category"] = "其它"
        ok = False
    for t in list(entry.get("topics", [])):
        if t not in TOPICS:
            print(f"  ⚠️ 非法 topic [{t}] → 归入「其它话题」")
            entry["topics"].remove(t)
            if "其它话题" not in entry["topics"]:
                entry["topics"].append("其它话题")
            ok = False
    return ok


# ── 断点续跑 ─────────────────────────────────────────────────────────────────


def _needs_enrich(entry: dict) -> str | None:
    """返回需要富集的理由（或 None=已完成）。"""
    tp = entry.get("type", "")
    if tp == "noise":
        return None  # 不处理 noise
    if tp == "home":
        # home: cn_key 由 context 自动设置，需 LLM 判 category
        if "category" in entry:
            return None
        return "home_category"
    if tp == "story":
        # story: LLM 生成 cn_key + category + topics
        if "cn_key" in entry:
            return None
        return "story_full"
    return None


# ── 富集核心 ─────────────────────────────────────────────────────────────────


def enrich_entries(
    entries: list[dict],
    client: OpenAI,
    limit: int = 0,
) -> int:
    """遍历所有条目，断点续跑；返回本次完成的条目数（仅 --write 模式调用）。"""
    done = 0
    skipped = 0
    total = len(entries)
    t_start = time.time()

    for i, entry in enumerate(entries):
        reason = _needs_enrich(entry)
        if reason is None:
            skipped += 1
            continue

        if limit and done >= limit:
            print(f"  ⏸️ 已达 limit={limit}，停止")
            break

        # ── home: cn_key 复用 context；LLM 只判 category + topics ──
        if reason == "home_category":
            entry["cn_key"] = entry.get("context", "")
            resp = _call_llm(entry, client)
            if resp:
                entry["category"] = resp.get("category", "其它")
                entry["topics"] = resp.get("topics", [])
                _validate(entry)
            done += 1

        # ── story: LLM 生成 cn_key + category + topics ──
        elif reason == "story_full":
            resp = _call_llm(entry, client)
            if resp:
                entry["cn_key"] = resp.get("cn_key", "")
                entry["category"] = resp.get("category", "其它")
                entry["topics"] = resp.get("topics", [])
                _validate(entry)
            done += 1

        # 进度
        if done % 10 == 0 or i == 0:
            elapsed = time.time() - t_start
            eta = (elapsed / max(done, 1)) * (total - skipped - done) if done else 0
            pct = (i + 1) / total * 100
            cn = entry.get("cn_key", "")[:30]
            cat = entry.get("category", "")
            print(f"  [{i+1}/{total}] {pct:.0f}% done={done} | ETA {eta:.0f}s | {cat} {cn}")

        # 增量写盘（防崩）
        if done % SAVE_EVERY == 0:
            _save(entries)
            print(f"  💾 已保存 checkpoint (done={done}/{total})")

    _save(entries)
    print(f"  💾 最终保存 (done={done}/{total}, skipped={skipped})")
    return done


def _call_llm(entry: dict, client: OpenAI) -> dict | None:
    """调 DeepSeek 产出一个 entry 的分类/摘要，失败返回 None。"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": _ENRICH_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(entry)},
            ],
            temperature=0.2,
            max_tokens=200,
            stream=False,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text)
        return data
    except Exception as e:
        print(f"  ❌ LLM 调用失败: {e}")
        return None


# ── 读写 ─────────────────────────────────────────────────────────────────────


def _save(entries: list[dict]) -> None:
    """写回数据文件（原地更新）。"""
    out = json.dumps(entries, ensure_ascii=False, indent=2)
    SCRIPT_FILE.write_text(out, encoding="utf-8")


# ── 预览 ─────────────────────────────────────────────────────────────────────


def preview(entries: list[dict], count: int, filter_type: str | None = None) -> None:
    """抽样预览富集质量。"""
    import random

    pool = [e for e in entries if e.get("type") != "noise"]
    if filter_type:
        pool = [e for e in pool if e.get("type") == filter_type]
    samples = random.sample(pool, min(count, len(pool)))

    print(f"📋 预览 {len(samples)} 条 (type={filter_type or 'all'})\n")

    client = _make_client()
    for i, entry in enumerate(samples):
        print(f"── [{i+1}/{len(samples)}] type={entry.get('type')} ──")
        print(f"  context: {(entry.get('context') or '')[:80]}")
        print(f"  dialogue: {(entry.get('dialogue') or '')[:60]}")

        if entry.get("type") == "home":
            entry["cn_key"] = entry.get("context", "")

        resp = _call_llm(entry, client)
        if resp:
            print(f"  cn_key:   {resp.get('cn_key', '')}")
            print(f"  category: {resp.get('category', '?')}")
            print(f"  topics:   {resp.get('topics', [])}")
        else:
            print("  ❌ LLM 失败")
        print()
        time.sleep(0.3)  # 控速


# ── 客户端 ───────────────────────────────────────────────────────────────────


def _make_client() -> OpenAI:
    import os

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key or "sk-" not in api_key:
        print("❌ 未配置有效的 DEEPSEEK_API_KEY（需以 sk- 开头）")
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


# ── 主入口 ───────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="语料库结构化重整")
    parser.add_argument("--sample", type=int, default=0, help="抽样预览 N 条（不落盘）")
    parser.add_argument("--type", dest="filter_type", default=None, help="预览时过滤类型 home/story")
    parser.add_argument("--write", action="store_true", help="全量富集落盘")
    parser.add_argument("--limit", type=int, default=0, help="限制富集条数（调试用）")
    args = parser.parse_args()

    if not SCRIPT_FILE.exists():
        print(f"❌ 未找到 {SCRIPT_FILE}")
        sys.exit(1)

    data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8-sig"))
    total = len([e for e in data if e.get("type") != "noise"])
    done_now = len(
        [
            e
            for e in data
            if (
                e.get("type") == "home"
                and "category" in e
                or e.get("type") == "story"
                and "cn_key" in e
            )
        ]
    )
    print(f"📖 总 {len(data)} 条（需处理 {total} 条，已完成 {done_now} 条）")

    if args.sample:
        preview(data, args.sample, args.filter_type)
        print("💡 这是 preview。确认质量后加 --write 全量落盘。")
        return

    if args.write:
        client = _make_client()
        enrich_entries(data, client, limit=args.limit)
        return

    # 无参数 → 教程
    print("用法:")
    print("  py tools/enrich_scripts.py --sample 30       # 预览 30 条")
    print("  py tools/enrich_scripts.py --sample 30 --type story  # 只看 story")
    print("  py tools/enrich_scripts.py --write            # 全量落盘（断点续跑）")
    print("  py tools/enrich_scripts.py --write --limit 100 # 试跑 100 条")


if __name__ == "__main__":
    main()
