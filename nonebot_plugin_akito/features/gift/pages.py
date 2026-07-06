"""Level logic + data builders for the bond (羁绊) pages.

``build_bond_page_data`` / ``build_bond_rank_page_data`` turn raw intimacy
data into the dicts that bond.html / bond_rank.html expect.

The ``levels`` parameter drives all level computation. It accepts the same
format as gift_config.json → bond_levels: a list of ``{"min": int, "name": str}``
sorted from low to high. This module does NOT import the gift package; callers pass
their level table explicitly so the config stays single-source.
"""

from __future__ import annotations

from datetime import datetime

FOOTER_BRAND = "AkitoBot · 羁绊系统"
FOOTER_RPG_BRAND = "AkitoBot · RPG系统"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def qq_avatar_uri(qq: str | int) -> str:
    """QQ 头像直链。html_to_pic 会在渲染时联网拉取。"""
    return f"https://q.qlogo.cn/g?b=qq&nk={qq}&s=640"


def level_info(intimacy: int, levels: list[dict]) -> dict:
    """根据亲密度 + 等级表算出当前等级、下一级、到下一级的距离与进度百分比。

    levels: [{"min": int, "name": str}, ...] 从低到高排列。
    level_no：以 min==0 的档位为 Lv1 锚点；负档 level_no ≤ 0 不显示 Lv。
    """
    intimacy = max(-999_999_999, int(intimacy))
    if not levels:
        return {
            "level_no": 1,
            "level_name": "初识",
            "next_level_name": "",
            "next_need": 0,
            "progress_pct": 100,
            "is_max": True,
        }

    # 找到当前所在等级
    idx = 0
    for i, lv in enumerate(levels):
        if intimacy >= int(lv.get("min", 0)):
            idx = i
        else:
            break

    # 以 min==0 为 Lv1 锚点
    zero_i = next((i for i, lv in enumerate(levels) if int(lv.get("min", 0)) == 0), 0)
    level_no = idx - zero_i + 1

    cur = levels[idx]
    cur_min = int(cur.get("min", 0))
    name = str(cur.get("name", ""))
    is_max = idx >= len(levels) - 1

    if is_max:
        return {
            "level_no": max(level_no, 0),  # 万一满级还在负档，不显示负数 Lv
            "level_name": name,
            "next_level_name": "",
            "next_need": 0,
            "progress_pct": 100,
            "is_max": True,
        }

    nxt = levels[idx + 1]
    nxt_min = int(nxt.get("min", 0))
    span = max(1, nxt_min - cur_min)
    pct = round((intimacy - cur_min) / span * 100)
    return {
        "level_no": level_no,
        "level_name": name,
        "next_level_name": str(nxt.get("name", "")),
        "next_need": max(0, nxt_min - intimacy),
        "progress_pct": max(0, min(100, pct)),
        "is_max": False,
    }


def _negative_progress_pct(intimacy: int, levels: list[dict]) -> int:
    """负羁绊的视觉进度：越负越长，按最低负档到 0 的区间折算。"""
    if intimacy >= 0:
        return 0
    negative_floor = min((int(lv.get("min", 0)) for lv in levels if int(lv.get("min", 0)) < 0), default=-1000)
    span = max(1, abs(negative_floor))
    pct = round(abs(intimacy) / span * 100)
    return max(0, min(100, pct))


def _visual_progress_pct(intimacy: int, levels: list[dict], info: dict) -> int:
    if int(intimacy) < 0:
        return _negative_progress_pct(int(intimacy), levels)
    return 100 if info["is_max"] else int(info["progress_pct"])


def _person(p: dict) -> dict:
    """规整一个人的字段：补全 avatar（默认 QQ 头像）与首字占位。"""
    qq = str(p.get("qq", ""))
    name = (p.get("name") or "").strip()
    avatar = p.get("avatar")
    if avatar is None and qq != "":
        avatar = qq_avatar_uri(qq)
    return {
        "qq": qq,
        "name": name or f"用户{qq}",
        "avatar": avatar,
        "initial": (name[:1] if name else "?"),
    }


def build_bond_page_data(
    left: dict,
    right: dict,
    intimacy: int,
    levels: list[dict] | None = None,
    *,
    title: str = "羁绊档案",
    eyebrow_tail: str = "BOND PROFILE",
    footer_left: str | None = None,
    footer_right: str = FOOTER_BRAND,
) -> dict:
    """单张羁绊详情页的数据。

    left / right: {"qq": "123456", "name": "星川", "avatar": 可选}
    intimacy: 两人当前亲密度
    levels: 等级阈值表 [{"min": int, "name": str}, ...]，不传则用内置默认
    """
    if levels is None:
        levels = _default_levels()
    info = level_info(intimacy, levels)
    visual_progress_pct = _visual_progress_pct(intimacy, levels, info)
    return {
        "page_width": 680,
        "title": title,
        "eyebrow_tail": eyebrow_tail,
        "left": _person(left),
        "right": _person(right),
        "intimacy": int(intimacy),
        "is_neg": int(intimacy) < 0,
        "visual_progress_pct": visual_progress_pct,
        "footer_left": footer_left or _now_text(),
        "footer_right": footer_right,
        **info,
    }


def build_bond_rank_page_data(
    entries: list[dict],
    levels: list[dict] | None = None,
    *,
    title: str = "羁绊排行榜",
    eyebrow_tail: str = "BOND RANKING",
    pill: str | None = None,
    limit: int | None = None,
    footer_left: str | None = None,
    footer_right: str = FOOTER_BRAND,
) -> dict:
    """羁绊排行榜的数据。

    entries: [{"left": {...}, "right": {...}, "intimacy": 6820}, ...]
    会自动按亲密度从高到低排序并编名次。
    levels: 等级阈值表，不传则用内置默认
    """
    if levels is None:
        levels = _default_levels()

    ranked = sorted(entries, key=lambda e: int(e.get("intimacy", 0)), reverse=True)
    if limit is not None:
        ranked = ranked[:limit]

    rows = []
    for i, e in enumerate(ranked, start=1):
        intimacy = int(e.get("intimacy", 0))
        info = level_info(intimacy, levels)
        rows.append(
            {
                "rank": i,
                "left": _person(e["left"]),
                "right": _person(e["right"]),
                "intimacy": intimacy,
                "level_name": info["level_name"],
                "progress_pct": info["progress_pct"],
                "visual_progress_pct": _visual_progress_pct(intimacy, levels, info),
                "is_max": info["is_max"],
                "is_neg": intimacy < 0,
            }
        )

    if pill is None:
        pill = f"本群亲密度 TOP {len(rows)}" if rows else "本群亲密度"

    return {
        "page_width": 680,
        "title": title,
        "eyebrow_tail": eyebrow_tail,
        "pill": pill,
        "rows": rows,
        "footer_left": footer_left or _now_text(),
        "footer_right": footer_right,
    }


def _default_levels() -> list[dict]:
    """内置默认等级表（当调用方未传入 levels 时使用）。"""
    return [
        {"min": -1000, "name": "宿敌", "team_level": -2},
        {"min": -650, "name": "势同水火", "team_level": -2},
        {"min": -300, "name": "结了梁子", "team_level": -1},
        {"min": -180, "name": "有过节", "team_level": -1},
        {"min": -100, "name": "看不顺眼", "team_level": -1},
        {"min": -50, "name": "闹别扭", "team_level": 0},
        {"min": 0, "name": "Hot Dogs"},
        {"min": 100, "name": "大麦克风"},
        {"min": 400, "name": "能信赖的搭档"},
        {"min": 1000, "name": "云与柳的大头贴"},
        {"min": 2500, "name": "想与你并肩而行"},
        {"min": 6000, "name": "从今往后直到永远"},
    ]


def build_my_bonds_page_data(
    owner: dict,
    partners: list[dict],
    levels: list[dict] | None = None,
    *,
    title: str = "我的羁绊",
    eyebrow_tail: str = "MY BONDS",
    pill: str | None = None,
    limit: int | None = 10,
    compact_threshold: int = 10,
    footer_left: str | None = None,
    footer_right: str = FOOTER_BRAND,
) -> dict:
    """单个用户的羁绊总览页数据。

    owner:    {"qq": "...", "name": "...", "avatar": 可选}  查询者本人
    partners: [{"qq": "...", "name": "...", "avatar": 可选, "intimacy": int}, ...]
              ta 的所有羁绊伙伴（如 gift 包的 `_top_partners` 取出后补名字）。
    会按亲密度从高到低排序；超过 compact_threshold 时切换精简行并展示全部伙伴。
    is_neg（亲密度<0）在模板里走冷色/红色 + 裂痕心。
    """
    if levels is None:
        levels = _default_levels()

    ordered = sorted(partners, key=lambda p: int(p.get("intimacy", 0)), reverse=True)

    count = len(ordered)
    total = sum(int(p.get("intimacy", 0)) for p in ordered)
    top_level = level_info(int(ordered[0].get("intimacy", 0)), levels)["level_name"] if ordered else "—"

    compact = count > compact_threshold
    shown = ordered if compact or limit is None else ordered[:limit]
    rows = []
    for i, p in enumerate(shown, start=1):
        intim = int(p.get("intimacy", 0))
        info = level_info(intim, levels)
        rows.append(
            {
                "rank": i,
                "partner": _person(p),
                "intimacy": intim,
                "level_name": info["level_name"],
                "progress_pct": info["progress_pct"],
                "visual_progress_pct": _visual_progress_pct(intim, levels, info),
                "is_max": info["is_max"],
                "is_neg": intim < 0,
            }
        )

    if pill is None and compact:
        pill = f"全部 {count} 段 · 精简版"

    return {
        "page_width": 680,
        "title": title,
        "eyebrow_tail": eyebrow_tail,
        "pill": pill,
        "compact": compact,
        "owner": _person(owner),
        "stats": {"count": count, "shown_count": len(rows), "total": total, "top_level": top_level},
        "rows": rows,
        "footer_left": footer_left or _now_text(),
        "footer_right": footer_right,
    }


def build_world_boss_rank_page_data(
    monster: str,
    entries: list[dict],
    *,
    title: str = "世界BOSS 结算排行",
    eyebrow_tail: str = "WORLD BOSS RANKING",
    pill: str | None = None,
    footer_left: str | None = None,
    footer_right: str = FOOTER_RPG_BRAND,
) -> dict:
    ranked = sorted(
        entries,
        key=lambda row: (
            int(row.get("rank", 0)) if int(row.get("rank", 0)) > 0 else 999999,
            -int(row.get("damage", 0)),
            str(row.get("uid", "")),
        ),
    )

    rows = []
    for idx, row in enumerate(ranked, start=1):
        rank = int(row.get("rank", 0)) or idx
        damage = int(row.get("damage", 0))
        damage_pct = int(row.get("damage_pct", 0))
        exp = int(row.get("exp", 0))
        points = int(row.get("points", 0))
        exp_bonus = int(row.get("exp_bonus", 0))
        points_bonus = int(row.get("points_bonus", 0))
        bond = int(row.get("bond", 0))
        special_drop = str(row.get("special_drop", ""))
        last_hit = bool(row.get("last_hit"))
        rows.append(
            {
                "rank": rank,
                "player": _person({"qq": row.get("uid", ""), "name": row.get("name", ""), "avatar": row.get("avatar")}),
                "damage": damage,
                "damage_pct": damage_pct,
                "exp": exp,
                "points": points,
                "exp_bonus": exp_bonus,
                "points_bonus": points_bonus,
                "bond": bond,
                "special_drop": special_drop,
                "last_hit": last_hit,
                "levelup": bool(row.get("levelup")),
                "levelup_text": str(row.get("levelup_text", "")),
            }
        )

    podium = rows[:3]
    others = rows[3:]
    participant_count = len(rows)
    last_hit_name = next((row["player"]["name"] for row in rows if row["last_hit"]), "—")
    last_hit_reward = next(
        (
            f"+{row['exp_bonus']} 经验 / +{row['points_bonus']} 积分"
            for row in rows
            if row["last_hit"] and (row["exp_bonus"] > 0 or row["points_bonus"] > 0)
        ),
        "无",
    )
    if pill is None:
        pill = "最终榜单"

    return {
        "page_width": 760,
        "title": title,
        "eyebrow_tail": eyebrow_tail,
        "pill": pill,
        "monster": monster,
        "stats": {
            "count": participant_count,
            "total_damage": sum(row["damage"] for row in rows),
            "total_exp": sum(row["exp"] for row in rows),
            "total_points": sum(row["points"] for row in rows),
            "total_bond": sum(row["bond"] for row in rows),
            "last_hit_name": last_hit_name,
            "last_hit_reward": last_hit_reward,
        },
        "podium": podium,
        "others": others,
        "footer_left": footer_left or _now_text(),
        "footer_right": footer_right,
    }
