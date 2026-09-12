"""群友助力事件：候选人抽取、羁绊方向和等级效果。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import random
from typing import Any, Literal, cast

from ...core.game_store import _get_intimacy, _render_with_ats, _weighted_choice
from ...core.types import GroupRecord
from ..gift import _bond_level
from .config import _cfg, _copy
from .player import _level_of
from .types import FriendSupportResult

_DEFAULT_TIERS: dict[str, tuple[dict[str, Any], ...]] = {
    "positive": (
        {"min_level": 1, "key": "positive_lv1", "power_mult": 1.02, "exp_mult": 1.03, "points_mult": 1.03, "drop_mult": 1.02, "rescue_chance": 0.0},
        {"min_level": 6, "key": "positive_lv6", "power_mult": 1.04, "exp_mult": 1.05, "points_mult": 1.05, "drop_mult": 1.03, "rescue_chance": 0.10},
        {"min_level": 11, "key": "positive_lv11", "power_mult": 1.06, "exp_mult": 1.07, "points_mult": 1.07, "drop_mult": 1.05, "rescue_chance": 0.20},
        {"min_level": 15, "key": "positive_lv15", "power_mult": 1.08, "exp_mult": 1.10, "points_mult": 1.10, "drop_mult": 1.07, "rescue_chance": 0.30},
    ),
    "negative": (
        {"min_level": 1, "key": "negative_lv1", "power_mult": 0.99, "exp_mult": 0.98, "points_mult": 0.98, "drop_mult": 0.98, "rescue_chance": 0.0},
        {"min_level": 6, "key": "negative_lv6", "power_mult": 0.98, "exp_mult": 0.97, "points_mult": 0.97, "drop_mult": 0.97, "rescue_chance": 0.0},
        {"min_level": 11, "key": "negative_lv11", "power_mult": 0.97, "exp_mult": 0.96, "points_mult": 0.96, "drop_mult": 0.96, "rescue_chance": 0.0},
        {"min_level": 15, "key": "negative_lv15", "power_mult": 0.95, "exp_mult": 0.94, "points_mult": 0.94, "drop_mult": 0.94, "rescue_chance": 0.0},
    ),
    "neutral": (
        {"min_level": 1, "key": "neutral", "power_mult": 1.0, "exp_mult": 1.0, "points_mult": 1.0, "drop_mult": 1.0, "rescue_chance": 0.0},
    ),
}


def _friend_support_cfg() -> dict[str, Any]:
    cfg = _cfg("friend_support", {})
    return cfg if isinstance(cfg, dict) else {}


def _friend_support_chance() -> float:
    try:
        value = float(_friend_support_cfg().get("chance", 0.03))
    except (TypeError, ValueError):
        value = 0.03
    return max(0.0, min(1.0, value))


def _is_rpg_record(record: object) -> bool:
    if not isinstance(record, Mapping):
        return False
    return any(
        key in record
        for key in (
            "exp",
            "equip_date",
            "equip_level",
            "rpg_first_seen",
            "hunt_total",
            "hunt_wins",
        )
    )


def _candidate_records(
    group: GroupRecord,
    participant_ids: Iterable[object],
    excluded_user_ids: Iterable[object],
) -> list[tuple[str, Mapping[str, Any]]]:
    users = group.get("users", {})
    if not isinstance(users, Mapping):
        return []
    normalized_users = {
        str(uid).strip(): record
        for uid, record in users.items()
        if uid is not None and str(uid).strip()
    }
    participant_set = {str(uid).strip() for uid in participant_ids if uid is not None and str(uid).strip()}
    excluded_set = participant_set | {
        str(uid).strip() for uid in excluded_user_ids if uid is not None and str(uid).strip()
    }
    raw_ids = group.get("user_ids", [])
    user_ids = (
        [str(uid).strip() for uid in raw_ids if uid is not None and str(uid).strip()]
        if isinstance(raw_ids, (list, tuple, set))
        else []
    )
    for uid in normalized_users:
        normalized = str(uid).strip()
        if normalized and normalized not in user_ids:
            user_ids.append(normalized)
    candidates: list[tuple[str, Mapping[str, Any]]] = []
    for uid in user_ids:
        if not uid or uid in excluded_set:
            continue
        record = normalized_users.get(uid)
        if isinstance(record, Mapping) and _is_rpg_record(record):
            candidates.append((uid, record))
    return candidates


def _candidate_bond(group: GroupRecord, participant_ids: list[str], candidate_id: str) -> float:
    if not participant_ids:
        return 0.0
    values = [_get_intimacy(group, uid, candidate_id) for uid in participant_ids]
    return sum(values) / len(values)


def _candidate_weight(bond: float) -> float:
    cfg = _friend_support_cfg()
    try:
        cap = max(0.0, float(cfg.get("bond_weight_cap", 1000)))
        step = max(1.0, float(cfg.get("bond_weight_step", 200)))
    except (TypeError, ValueError):
        cap, step = 1000.0, 200.0
    return 1.0 + min(abs(float(bond)), cap) / step


def _roll_polarity(
    bond: float,
    team_level: int,
    rng=random,
) -> Literal["positive", "negative", "neutral"]:
    if bond > 0:
        return "positive"
    if bond == 0:
        return "neutral"
    cfg = _friend_support_cfg()
    try:
        negative_base = float(cfg.get("negative_base", 0.70))
        negative_step = float(cfg.get("negative_per_level", 0.04))
        negative_cap = float(cfg.get("negative_cap", 0.90))
        positive_chance = float(cfg.get("reverse_positive_chance", 0.05))
    except (TypeError, ValueError):
        negative_base, negative_step, negative_cap, positive_chance = 0.70, 0.04, 0.90, 0.05
    negative_chance = max(0.0, min(1.0, negative_base + abs(int(team_level)) * negative_step))
    negative_chance = min(negative_chance, max(0.0, min(1.0, negative_cap)))
    positive_chance = max(0.0, min(1.0 - negative_chance, positive_chance))
    roll = rng.random()
    if roll < negative_chance:
        return "negative"
    if roll < negative_chance + positive_chance:
        return "positive"
    return "neutral"


def _tiers_for(polarity: str) -> list[dict[str, Any]]:
    cfg = _friend_support_cfg().get("tiers", {})
    raw = cfg.get(polarity) if isinstance(cfg, Mapping) else None
    tiers = raw if isinstance(raw, list) else list(_DEFAULT_TIERS.get(polarity, _DEFAULT_TIERS["neutral"]))
    normalized: list[dict[str, Any]] = []
    for tier in tiers:
        if not isinstance(tier, Mapping):
            continue
        try:
            min_level = max(1, int(tier.get("min_level", 1)))
        except (TypeError, ValueError):
            continue
        normalized.append({**tier, "min_level": min_level})
    if not normalized:
        normalized = [dict(item) for item in _DEFAULT_TIERS.get(polarity, _DEFAULT_TIERS["neutral"])]
    normalized.sort(key=lambda item: int(item["min_level"]))
    return normalized


def _tier_for_level(polarity: str, level: int) -> dict[str, Any]:
    tiers = _tiers_for(polarity)
    selected = tiers[0]
    for tier in tiers:
        if int(level) >= int(tier.get("min_level", 1)):
            selected = tier
        else:
            break
    return dict(selected)


def _helper_level(record: Mapping[str, Any]) -> int:
    raw_exp = record.get("exp")
    if raw_exp is not None:
        try:
            return _level_of(int(raw_exp))
        except (TypeError, ValueError):
            pass
    try:
        return max(1, int(record.get("equip_level", 1)))
    except (TypeError, ValueError):
        return 1


def _float_value(value: object, default: float = 1.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def roll_friend_support(
    group: GroupRecord | None,
    participant_ids: Iterable[object],
    *,
    excluded_user_ids: Iterable[object] = (),
    rng=random,
) -> FriendSupportResult | None:
    """按概率抽取一名群友并生成本场助力效果。"""
    if group is None:
        return None
    participants = [str(uid).strip() for uid in participant_ids if uid is not None and str(uid).strip()]
    if not participants:
        return None
    chance = _friend_support_chance()
    if chance <= 0 or rng.random() >= chance:
        return None
    candidates = _candidate_records(group, participants, excluded_user_ids)
    if not candidates:
        return None
    weights = {
        uid: _candidate_weight(_candidate_bond(group, participants, uid))
        for uid, _record in candidates
    }
    helper_id = _weighted_choice(weights, rng)
    if not helper_id or helper_id not in weights:
        return None
    helper_record = dict(next(record for uid, record in candidates if uid == helper_id))
    bond = _candidate_bond(group, participants, helper_id)
    bond_value = int(bond)
    if bond_value == 0 and bond != 0:
        bond_value = 1 if bond > 0 else -1
    bond_info = _bond_level(bond_value)
    polarity = _roll_polarity(bond, int(bond_info.get("team_level", 0)), rng)
    helper_level = _helper_level(helper_record)
    tier = _tier_for_level(polarity, helper_level)
    rescue_chance = max(0.0, min(1.0, _float_value(tier.get("rescue_chance", 0.0), 0.0)))
    result: FriendSupportResult = {
        "helper_id": helper_id,
        "helper_name": str(helper_record.get("display_name") or f"群友{helper_id}"),
        "bond": bond_value,
        "bond_level": int(bond_info.get("team_level", 0)),
        "bond_name": str(bond_info.get("name", "")),
        "helper_level": helper_level,
        "polarity": polarity,
        "effect_key": str(tier.get("key") or f"{polarity}_lv{tier.get('min_level', 1)}"),
        "power_mult": _float_value(tier.get("power_mult", 1.0)),
        "exp_mult": _float_value(tier.get("exp_mult", 1.0)),
        "points_mult": _float_value(tier.get("points_mult", 1.0)),
        "drop_mult": _float_value(tier.get("drop_mult", 1.0)),
        "rescue_chance": rescue_chance,
        "rescue_triggered": False,
    }
    return result


def _change_text(label: str, multiplier: object) -> str:
    percent = int(round((_float_value(multiplier) - 1.0) * 100))
    if percent == 0:
        return ""
    return f"{label} {'+' if percent > 0 else ''}{percent}%"


def _effect_text(result: Mapping[str, Any]) -> str:
    parts = [
        _change_text("战力", result.get("power_mult", 1.0)),
        _change_text("经验", result.get("exp_mult", 1.0)),
        _change_text("积分", result.get("points_mult", 1.0)),
        _change_text("掉落", result.get("drop_mult", 1.0)),
    ]
    parts = [part for part in parts if part]
    return " / ".join(parts) if parts else "效果不明"


def render_friend_support_line(
    result: Mapping[str, Any] | None,
    *,
    target_name: str = "",
    battle_won: bool | None = None,
    rng=random,
):
    """渲染群友助力播报，helper 占位符会转换为真实 @。"""
    if not isinstance(result, Mapping):
        return ""
    polarity = str(result.get("polarity", "neutral"))
    key = f"friend_support_{polarity}"
    if result.get("rescue_triggered") and polarity == "positive":
        key = "friend_support_positive_rescue"
    elif battle_won is False and polarity == "positive":
        key = "friend_support_positive_fail"
    templates = _copy(key)
    if not templates:
        return ""
    try:
        template = rng.choice(templates)
    except AttributeError:
        template = random.choice(templates)
    context = {
        "helper": str(result.get("helper_id", "")),
        "helper_name": str(result.get("helper_name", "")),
        "target_name": target_name,
        "bond": int(result.get("bond", 0)),
        "bond_level": int(result.get("bond_level", 0)),
        "helper_level": int(result.get("helper_level", 1)),
        "effect": _effect_text(result),
    }
    return _render_with_ats(str(template), context)
