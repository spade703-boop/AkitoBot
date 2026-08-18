"""RPG 玩法附加到共享记录上的字段类型。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from ...core.types import BaseUserRecord


class ActiveBattleRecord(TypedDict):
    name: str
    uses: int


class ActiveBattleView(ActiveBattleRecord):
    effect: dict[str, Any]


class EquipmentRecord(TypedDict, total=False):
    exp: int
    equip_date: str
    equip_level: int
    equip_roll: int
    equip_forge: int
    equip_used: bool
    equip_rebought: bool
    equip_rebuy_count: int


class RpgUserRecord(BaseUserRecord, EquipmentRecord, total=False):
    inventory: dict[str, int]
    fortune: str
    fortune_date: str
    last_fortune: str
    no_lucky_streak: int
    hunt_total: int
    hunt_wins: int
    exp_buff_uses: int
    exp_buff_mult: int
    signin_streak: int
    signin_last_date: str
    world_boss_trophies: list[str]
    active_battle_supply: ActiveBattleRecord
    active_battle_guard: ActiveBattleRecord
    active_battle_debuff: ActiveBattleRecord


class BossParticipantRecord(EquipmentRecord, total=False):
    pass


class WorldBossRecord(TypedDict, total=False):
    date: str
    name: str
    max_hp: int
    hp: int
    recent_active_count: int
    scale_count: int
    reward_scale_count: int
    avg_level: int
    avg_power: int
    contributors: dict[str, int]
    participants: dict[str, BossParticipantRecord]
    spawned_by: str
    last_hit: str
    last_hit_uids: list[str]
    bond_gains: dict[str, int]


MetricScalarField = Literal[
    "battles",
    "wins",
    "solo_battles",
    "team_battles",
    "fallback_battles",
    "team_attempts",
    "team_formed",
    "exp_gained",
    "points_gained",
    "supply_opens",
    "supply_points_spent",
    "supply_exp_gained",
    "world_boss_spawns",
    "world_boss_forced_spawns",
    "world_boss_attacks",
    "world_boss_damage",
    "world_boss_kills",
    "world_boss_expired",
    "world_boss_exp_gained",
    "world_boss_points_gained",
]
MetricMemberField = Literal["players", "world_boss_players"]


class MonsterMetricRecord(TypedDict, total=False):
    battles: int
    wins: int
    elite: int


class RpgMetricDay(TypedDict, total=False):
    battles: int
    wins: int
    solo_battles: int
    team_battles: int
    fallback_battles: int
    team_attempts: int
    team_formed: int
    exp_gained: int
    points_gained: int
    supply_opens: int
    supply_points_spent: int
    supply_exp_gained: int
    world_boss_spawns: int
    world_boss_forced_spawns: int
    world_boss_attacks: int
    world_boss_damage: int
    world_boss_kills: int
    world_boss_expired: int
    world_boss_exp_gained: int
    world_boss_points_gained: int
    players: list[str]
    world_boss_players: list[str]
    monsters: dict[str, MonsterMetricRecord]


class RpgMetricsRecord(TypedDict, total=False):
    days: dict[str, RpgMetricDay]


class DailyPairRecord(TypedDict):
    date: str
    pairs: dict[str, int]


class RpgState(TypedDict, total=False):
    world_boss: WorldBossRecord
    metrics: RpgMetricsRecord
    team_bond_daily: DailyPairRecord
    world_boss_team_bond_daily: DailyPairRecord
