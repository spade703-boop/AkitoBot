"""RPG 配置：默认数值/表/文案内嵌于此，可被 data/content/rpg_config.json 覆盖并热重载。

照搬 features/gift.py 的配置范式：DEFAULT_*_CONFIG + _load_config + _cfg/_copy/_error + reload_*。
所有数值（运势权重、精力、等级曲线、怪物表、随机事件、文案）都可在 JSON 里调，改完用「重载配置」热更。
"""

from __future__ import annotations

import copy

from nonebot.log import logger

from ...core import load_json_file

CONFIG_FILE = "rpg_config.json"

# ==================== 默认配置（可被 data/content/rpg_config.json 覆盖） ====================

DEFAULT_RPG_CONFIG: dict = {
    # ---- 签到运势：运势决定签到经验系数，并给当日打野一个轻微战力修正 ----
    "fortune": {
        "signin_exp_base": 50,               # 签到基础经验，乘运势 exp_mult
        "lucky_pity_days": 5,                # 连续 N 天未出「吉以上」后触发保底
        "lucky_pity_boost": 30,              # 保底时给每个「吉以上」档加的权重
        "daji_after_daxiong_boost": 20,      # 昨日大凶 → 今日「大吉」额外权重
        "lucky_keys": ["daji", "ji"],        # 「吉以上」集合（保底/连签清零判定）
        "daji_key": "daji",
        "daxiong_key": "daxiong",
        "levels": [
            {"key": "daji",     "name": "大吉", "weight": 5,  "exp_mult": 3.0, "combat_factor": 1.10},
            {"key": "ji",       "name": "吉",   "weight": 25, "exp_mult": 2.0, "combat_factor": 1.05},
            {"key": "ping",     "name": "中平", "weight": 45, "exp_mult": 1.0, "combat_factor": 1.00},
            {"key": "xiaoxiong", "name": "小凶", "weight": 20, "exp_mult": 0.5, "combat_factor": 0.97},
            {"key": "daxiong",  "name": "大凶", "weight": 5,  "exp_mult": 0.0, "combat_factor": 0.90},
        ],
    },
    # ---- 精力：每天 0 点（懒）回满，打野按次消耗。每日次数 = max // cost_per_hunt ----
    "stamina": {"max": 100, "cost_per_hunt": 20},
    # ---- 等级曲线：升到 L 级累计需 base*(L-1)*L/2 经验（每级增量 +base）----
    "level_curve": {"base": 100},
    # ---- 战力派生：base_power + 等级 * power_per_level（+装备，留待装备阶段）----
    "power": {"base_power": 10, "power_per_level": 5},
    # ---- 战斗判定 ----
    "combat": {
        "factor_min": 0.8,                   # 有效战力随机系数下限
        "factor_max": 1.2,                   # 有效战力随机系数上限
        "fortune_affects_hunt": True,        # 当日运势是否给打野一个战力修正
        "lose_exp_ratio": 0.2,               # 失败时仍给的经验比例（安慰）
        "crush_margin": 1.5,                 # 战力/需求 ≥ 此值视为「碾压」
        "weak_margin": 0.8,                  # 战力/需求 < 此值视为「劣势」
        "no_event_weight": 60,               # 不触发任何随机事件的权重
        "events": {
            "slip":      {"weight": 25, "power_mult": 0.75},  # 脚底打滑：有效战力 ×0.75（均势/优势可触发）
            "insight":   {"weight": 25, "exp_mult": 1.5},     # 弱点看破：碾压时触发，胜则经验 ×1.5
            "desperate": {"weight": 35, "power_mult": 1.6},   # 绝境爆发：劣势时触发，有效战力 ×1.6 可翻盘
        },
    },
    # ---- 野怪表：按 weight 加权遭遇；power_req 越高越难，exp/points 越丰 ----
    "monsters": [
        {"name": "史莱姆", "level": 1, "power_req": 10, "exp": 50,  "points": 20,  "weight": 50},
        {"name": "哥布林", "level": 3, "power_req": 30, "exp": 120, "points": 50,  "weight": 30},
        {"name": "座狼",   "level": 5, "power_req": 55, "exp": 200, "points": 90,  "weight": 15},
        {"name": "食人魔", "level": 8, "power_req": 90, "exp": 320, "points": 150, "weight": 5},
    ],
    # ---- 播报文案。占位符：{a} → 真 @；其余 {monster}{mlevel}{exp}{points}{cost}{stamina}{fortune}{mult}{level}{newlevel}{power}{newpower} → 文本 ----
    "copy": {
        "signin_exp": [
            "🗡️ 签到完成，今日探索经验 +{exp}。",
            "🗡️ 打卡了，探索经验 +{exp} 到账。",
        ],
        "signin_exp_zero": [
            "🗡️ 今天状态不佳，探索经验颗粒无收，明天会更好。",
        ],
        "fortune_query": [
            "{a} 今日运势：{fortune}。",
        ],
        "hunt_encounter": [
            "{a} 整装出门探索，撞上了 Lv{mlevel} 的【{monster}】！",
            "{a} 在野外遭遇了 Lv{mlevel} 的【{monster}】，拔刀迎战！",
        ],
        "hunt_win": [
            "鏖战之后击败了【{monster}】，经验 +{exp}、积分 +{points}（精力 -{cost}，剩 {stamina}）。",
            "一套连招放倒【{monster}】，经验 +{exp}、积分 +{points}（精力 -{cost}，剩 {stamina}）。",
        ],
        "hunt_lose": [
            "可惜不敌【{monster}】，狼狈撤退，只摸到一点经验 +{exp}（精力 -{cost}，剩 {stamina}）。",
        ],
        "levelup": [
            "⬆️ 升级了！Lv{level} → Lv{newlevel}，战力 {power} → {newpower}！",
        ],
        "event_slip": ["💢 脚底一滑，这一下没使上全力……"],
        "event_insight": ["🎯 看破了【{monster}】的破绽，经验大涨！"],
        "event_desperate": ["🔥 被逼到绝境，反而爆发出惊人的战力！"],
    },
    "errors": {
        "private_only": "冒险要在群里玩哦。",
        "sleeping": "💤 这会儿小彰睡着了，等 6 点天亮以后再来探险吧……",
        "no_stamina": "精力不够啦（需要 {cost}，当前 {stamina}），精力每天 0 点回满，先去歇歇。",
        "need_signin": "今天还没签到，先「签到」抽个运势再来冒险吧～",
    },
}


def _load_config() -> dict:
    """加载 RPG 配置；无文件 / 解析失败时回落到默认配置的深拷贝（深拷贝避免 reload 清空默认）。"""
    loaded = load_json_file(CONFIG_FILE, None)
    return loaded if isinstance(loaded, dict) else copy.deepcopy(DEFAULT_RPG_CONFIG)


RPG_CONFIG: dict = _load_config()


def _cfg(key: str, default=None):
    """读配置项，缺失时回落到默认配置。"""
    if key in RPG_CONFIG:
        return RPG_CONFIG[key]
    return DEFAULT_RPG_CONFIG.get(key, default)


def _copy(key: str) -> list[str]:
    table = _cfg("copy", {})
    if isinstance(table, dict) and table.get(key):
        return table[key]
    return DEFAULT_RPG_CONFIG["copy"].get(key, [""])


def _error(key: str, **fmt) -> str:
    table = _cfg("errors", {})
    template = table.get(key) if isinstance(table, dict) else None
    if not template:
        template = DEFAULT_RPG_CONFIG["errors"].get(key, "")
    try:
        return template.format(**fmt)
    except (KeyError, IndexError):
        return template


def reload_rpg_config() -> None:
    """热重载 RPG 配置（原地 clear+update，保持已持有引用不失效）。"""
    RPG_CONFIG.clear()
    RPG_CONFIG.update(_load_config())
    logger.info("🔄 RPG 配置已热重载")
