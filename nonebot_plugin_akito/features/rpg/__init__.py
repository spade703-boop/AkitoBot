"""RPG 子包（精简版）：每天「签到领装备 → 选择打怪」。

导入各命令子模块即触发 on_command 注册与签到钩子注册；并向上 re-export：
- on_signin：供 gift 的签到经 game_store 钩子表回调。
- reload_rpg_config：供 core.data.reload_assets 热重载。
"""

from . import character, fortune, hunt, inventory, smith, team  # noqa: F401  导入即注册命令/钩子
from .config import reload_rpg_config  # noqa: F401  供 core.data.reload_assets 调用
from .fortune import on_signin  # noqa: F401  供签到钩子（已自动注册到 game_store）
