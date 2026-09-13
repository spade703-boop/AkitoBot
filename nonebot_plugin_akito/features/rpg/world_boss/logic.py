"""世界 BOSS 状态、生成与伤害处理。"""


from .settlement import (
    _active_world_boss,
    _apply_team_bonus,
    _apply_world_boss_damage,
    _boss_damage,
    _boss_participants,
    _ensure_boss_participant,
    _force_world_boss_snapshot,
    _maybe_spawn_world_boss,
    _maybe_spawn_world_boss_lines,
    _recent_active_user_ids,
    _spawn_world_boss,
    _world_boss_cfg,
    _world_boss_snapshot,
)

__all__ = [
    "_active_world_boss",
    "_apply_team_bonus",
    "_apply_world_boss_damage",
    "_boss_damage",
    "_boss_participants",
    "_ensure_boss_participant",
    "_force_world_boss_snapshot",
    "_maybe_spawn_world_boss",
    "_maybe_spawn_world_boss_lines",
    "_recent_active_user_ids",
    "_spawn_world_boss",
    "_world_boss_cfg",
    "_world_boss_snapshot",
]
