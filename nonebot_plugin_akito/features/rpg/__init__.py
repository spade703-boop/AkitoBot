"""RPG 子包（精简版）：每天「签到领装备 → 选择打怪」。

导入各命令子模块即触发 on_command 注册与签到钩子注册；并向上 re-export：
- on_signin：供 gift 的签到经 game_store 钩子表回调。
- reload_rpg_config：供 core.data.reload_assets 热重载。
"""

from . import (  # noqa: F401
    reporting,
    world_boss,
    profile,
    signin,
    hunt,
    inventory,
    equipment,
    supply,
    team,
    battle,
    player,
)
from .config import reload_rpg_config  # noqa: F401
from .signin import on_signin  # noqa: F401
