"""签到运势与 RPG 签到钩子。"""

from . import fortune

on_signin = fortune.on_signin

__all__ = ["fortune", "on_signin"]
