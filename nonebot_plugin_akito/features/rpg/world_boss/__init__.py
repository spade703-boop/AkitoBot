"""世界 BOSS 领域。"""

from . import command, logic, settlement

__all__ = ["command", "logic", "settlement"]

from .settlement import _cleanup_stale_world_boss

__all__.append("_cleanup_stale_world_boss")
