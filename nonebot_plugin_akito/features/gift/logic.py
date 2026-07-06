"""送礼状态与结算逻辑。"""

from __future__ import annotations

import asyncio
import random
import sys

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment


def _pkg():
    return sys.modules[__package__]


def _get_user(group: dict, user_id, display_name: str = "") -> dict:
    user = _pkg().get_user(group, user_id, display_name)
    user.setdefault("last_sign_in", "")
    user.setdefault("last_gift", "")
    user.setdefault("steal_date", "")
    user.setdefault("steal_used", 0)
    user.setdefault("robbed_date", "")
    user.setdefault("robbed_count", 0)
    user.setdefault("protect_until", 0)
    return user


def _count_key(frm, to) -> str:
    return f"{frm}>{to}"


def _bump_count(group: dict, frm, to) -> int:
    counts = group.setdefault("counts", {})
    key = _count_key(frm, to)
    counts[key] = int(counts.get(key, 0)) + 1
    return counts[key]


def _get_count(group: dict, frm, to) -> int:
    return int(group.get("counts", {}).get(_count_key(frm, to), 0))


def _bond_level(value: int) -> dict:
    levels = _pkg()._bond_levels()
    value = int(value)
    idx = 0
    for i, level in enumerate(levels):
        if value >= int(level.get("min", 0)):
            idx = i
        else:
            break
    zero_i = next((i for i, level in enumerate(levels) if int(level.get("min", 0)) == 0), 0)
    current = levels[idx]
    next_level = levels[idx + 1] if idx + 1 < len(levels) else None
    display_level = idx - zero_i + 1
    return {
        "idx": idx,
        "level": display_level,
        "team_level": int(current.get("team_level", display_level)),
        "name": str(current.get("name", "")),
        "cur_min": int(current.get("min", 0)),
        "next_name": str(next_level.get("name", "")) if next_level else None,
        "next_min": int(next_level.get("min", 0)) if next_level else None,
        "to_next": max(0, int(next_level.get("min", 0)) - value) if next_level else 0,
    }


def _bond_card(group: dict, me: str, other: str):
    pkg = _pkg()
    value = pkg._get_intimacy(group, me, other)
    level = _bond_level(value)
    sent = _get_count(group, me, other)
    recv = _get_count(group, other, me)

    tier = f"{level['name']}（Lv{level['level']}）" if level["level"] >= 1 else level["name"]
    lines = [f"· 等级：{tier}"]
    if level["next_name"]:
        lines.append(f"· 羁绊值 {value}，距「{level['next_name']}」还差 {level['to_next']}")
    else:
        lines.append(f"· 羁绊值 {value}，已达顶级「{level['name']}」(Lv{level['level']})")
    if sent or recv:
        lines.append(f"· 你送出 {sent} 次，ta 回送 {recv} 次（共 {sent + recv} 次往来）")
    else:
        lines.append("· 你们还没互送过礼，快去 送礼@ta 吧～")

    head = MessageSegment.at(me) + " 和 " + MessageSegment.at(other) + " 的同好羁绊"
    return head + "\n" + "\n".join(lines)


def _settle(
    group: dict,
    sender_id: str,
    target_id: str,
    gift: dict,
    main_event: str,
    mishap: str | None,
    return_key: str | None = None,
) -> dict:
    pkg = _pkg()
    base = int(gift.get("intimacy", 0))
    cost = int(gift.get("cost", 0))
    out: dict = {
        "event": main_event,
        "mishap": mishap if main_event == "mishap" else None,
        "gift": gift.get("name", ""),
        "amount": base,
        "refund": 0,
        "return_gift": None,
        "return_key": None,
    }

    if main_event == "normal":
        pkg._add_intimacy(group, sender_id, target_id, base)
    elif main_event == "crit":
        out["amount"] = base * int(pkg._cfg("crit_multiplier", 2))
        pkg._add_intimacy(group, sender_id, target_id, out["amount"])
    elif main_event == "return":
        if return_key is None:
            return_key = next(iter(pkg._return_gifts()), "")
        spec = pkg._return_spec(return_key)
        out["return_key"] = return_key
        out["return_gift"] = str(spec.get("name", ""))
        out["amount"] = base + int(spec.get("bonus", 0))
        out["refund"] = int(cost * float(spec.get("refund_ratio", 0)))
        pkg._add_intimacy(group, sender_id, target_id, out["amount"])
        if out["refund"]:
            pkg._add_points(group, sender_id, out["refund"])
    elif main_event == "fail":
        out["amount"] = 0
        out["refund"] = int(cost * float(pkg._cfg("fail_refund_ratio", 0)))
        if out["refund"]:
            pkg._add_points(group, sender_id, out["refund"])
    elif main_event == "special":
        out["amount"] = int(gift.get("intimacy", base))
        out["copy"] = gift.get("copy", "special")
        pkg._add_intimacy(group, sender_id, target_id, out["amount"])
    elif main_event == "mishap":
        spec = pkg._mishap_spec(mishap)
        out["amount"] = max(int(spec.get("intimacy", 0)), int(float(spec.get("ratio", 0)) * base))
        if out["amount"]:
            pkg._add_intimacy(group, sender_id, target_id, out["amount"])
        out["refund"] = int(cost * float(spec.get("refund_ratio", 0)))
        if out["refund"]:
            pkg._add_points(group, sender_id, out["refund"])

    return out


def _steal_outcome(rng=random) -> str:
    pkg = _pkg()
    return pkg._weighted_choice(pkg._steal_cfg().get("weights", {}), rng)


def _steal_bond_loss(cfg: dict, outcome: str, amount: int, bond: int, rng=random) -> int:
    table = cfg.get("bond_loss", {})
    spec = table.get(outcome) if isinstance(table, dict) else None
    if isinstance(spec, dict) and spec:
        base = max(0, int(spec.get("base", 0)))
        amount_ratio = max(0.0, float(spec.get("amount_ratio", 0.0)))
        positive_ratio = max(0.0, float(spec.get("positive_bond_ratio", 0.0)))
        positive_cap = max(0, int(spec.get("positive_bond_cap", 0)))
        extra = 0
        if bond > 0 and positive_ratio > 0:
            extra = int(bond * positive_ratio)
            if positive_cap > 0:
                extra = min(extra, positive_cap)
        return max(0, base + int(int(amount) * amount_ratio) + extra)

    if bond > 0:
        return int(int(cfg.get("bond_flat", 20)) + float(cfg.get("bond_ratio", 0.1)) * bond)
    return rng.randint(int(cfg.get("bond_neg_min", 10)), int(cfg.get("bond_neg_max", 30)))


def _settle_steal(group: dict, thief_id: str, victim_id: str, outcome: str, rng=random) -> dict:
    pkg = _pkg()
    cfg = pkg._steal_cfg()
    thief = _get_user(group, thief_id)
    victim = _get_user(group, victim_id)
    bond = pkg._get_intimacy(group, thief_id, victim_id)
    out = {"outcome": outcome, "amount": 0, "bond": 0}

    if outcome == "success":
        victim_points = int(victim.get("points", 0))
        amount = min(int(victim_points * float(cfg.get("ratio", 0.1))), int(cfg.get("cap", 40)), victim_points)
        victim["points"] = victim_points - amount
        thief["points"] = int(thief.get("points", 0)) + amount
        out["amount"] = amount
    elif outcome == "minor_success":
        victim_points = int(victim.get("points", 0))
        amount = max(int(cfg.get("minor_min_amount", 6)), int(victim_points * float(cfg.get("minor_ratio", 0.04))))
        minor_cap = int(cfg.get("minor_cap", 12))
        if minor_cap > 0:
            amount = min(amount, minor_cap)
        amount = min(amount, victim_points)
        victim["points"] = victim_points - amount
        thief["points"] = int(thief.get("points", 0)) + amount
        out["amount"] = amount
    elif outcome == "caught":
        penalty = min(int(cfg.get("caught_penalty", 15)), int(thief.get("points", 0)))
        thief["points"] = int(thief.get("points", 0)) - penalty
        victim["points"] = int(victim.get("points", 0)) + penalty
        out["amount"] = penalty
    elif outcome == "reversal":
        amount = min(int(cfg.get("reversal_amount", 10)), int(thief.get("points", 0)))
        thief["points"] = int(thief.get("points", 0)) - amount
        victim["points"] = int(victim.get("points", 0)) + amount
        out["amount"] = amount

    drop = _steal_bond_loss(cfg, outcome, int(out["amount"]), bond, rng)
    new_bond = max(int(cfg.get("bond_floor", -1000)), bond - drop)
    out["bond"] = bond - new_bond
    pkg._add_intimacy(group, thief_id, victim_id, new_bond - bond)
    return out


def _outcome_copy_key(out: dict) -> str:
    if out["event"] == "mishap":
        return f"mishap_{out['mishap']}"
    if out["event"] == "special":
        return out.get("copy") or "special"
    if out["event"] == "return":
        key = out.get("return_key")
        return f"return_{key}" if key else "return"
    return out["event"]


def _build_broadcast(out: dict, sender_id: str, target_id: str, rng=random):
    pkg = _pkg()
    template = rng.choice(pkg._copy(_outcome_copy_key(out)))
    ctx = {
        "a": sender_id,
        "b": target_id,
        "gift": out.get("gift", ""),
        "amount": out.get("amount", 0),
        "refund": out.get("refund", 0),
        "return_gift": out.get("return_gift") or "",
    }
    return pkg._render_with_ats(template, ctx)


def _resolve_group(event: Event) -> tuple[str | None, str | None]:
    pkg = _pkg()
    group_id, is_private = pkg.resolve_group_id(event)
    if group_id is None:
        return None, (pkg._error("private_only") if is_private else None)
    return group_id, None


def _reset_today_signins(group: dict, today: str) -> int:
    cleared = 0
    for user in group.get("users", {}).values():
        if isinstance(user, dict) and user.get("last_sign_in") == today:
            user["last_sign_in"] = ""
            cleared += 1
    return cleared


def _reset_today_steals(group: dict, today: str) -> int:
    cleared = 0
    for user in group.get("users", {}).values():
        if not isinstance(user, dict):
            continue
        touched = False
        if user.get("steal_date") == today:
            user["steal_date"] = ""
            user["steal_used"] = 0
            touched = True
        if user.get("robbed_date") == today:
            user["robbed_date"] = ""
            user["robbed_count"] = 0
            touched = True
        if touched:
            cleared += 1
    return cleared


async def _sign_in_delay() -> None:
    pkg = _pkg()
    delay_cfg = pkg._cfg("sign_delay_sec", {})
    await asyncio.sleep(
        pkg.random.uniform(float(delay_cfg.get("min", 3)), float(delay_cfg.get("max", 5)))
    )


def _top_partners(group: dict, user_id: str, limit: int = 5) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for key, value in group.get("intimacy", {}).items():
        ids = key.split("|||")
        if user_id in ids:
            other = ids[0] if ids[1] == user_id else ids[1]
            result.append((other, int(value)))
    result.sort(key=lambda item: item[1], reverse=True)
    return result[:limit]


def _name_of(group: dict, user_id: str) -> str:
    user = group.get("users", {}).get(str(user_id))
    if isinstance(user, dict) and user.get("display_name"):
        return user["display_name"]
    return f"用户{user_id}"
