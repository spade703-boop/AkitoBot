"""RPG 配置（精简版）：默认数值/表/文案内嵌于此，可被 data/content/rpg_config.json 覆盖并热重载。

精简后的循环：每天「签到领今日装备 → 选择打怪」。角色对外只有等级；战力是今日装备的隐藏值；
运势隐藏（暗中影响打怪胜负/掉落）；积分出口只有「强化今日装备」（不做商店）。
"""

from __future__ import annotations

import copy
import random

from nonebot.log import logger

from ...core import load_json_file

CONFIG_FILE = "rpg_config.json"

# ==================== 默认配置（可被 data/content/rpg_config.json 覆盖） ====================

DEFAULT_RPG_CONFIG: dict = {
    # ---- 签到：基础经验（积分由 gift 的签到发放；连签额外经验见下方 signin_streak）----
    "signin": {"exp": 50},
    # ---- 连续签到：连签递增额外经验 bonus = min(streak*per_day, cap)，断签重置 ----
    "signin_streak": {"per_day": 10, "cap": 100},
    # ---- 等级曲线：升到 L 级累计需 base*(L-1)*L/2 经验 ----
    "level_curve": {"base": 100},
    # ---- 今日装备：战力 = base + 等级*per_level + rand(0,var) + 强化次数*forge.step（战力为隐藏值，不外显）----
    "equip": {"base": 10, "per_level": 5, "var": 6},
    # ---- 强化（积分出口）：第 n 次花 cost_base*n 积分、+step 战力，每日最多 max_per_day 次，次日重置 ----
    "forge": {"cost_base": 100, "step": 4, "max_per_day": 5},
    # ---- 隐藏运势：签到暗掷，仅经 combat_factor / drop_factor 影响打怪（不外显）----
    "fortune": {
        "lucky_pity_days": 5,
        "lucky_pity_boost": 30,
        "daji_after_daxiong_boost": 20,
        "lucky_keys": ["daji", "ji"],
        "daji_key": "daji",
        "daxiong_key": "daxiong",
        "levels": [
            {"key": "daji",     "name": "大吉", "weight": 5,  "combat_factor": 1.10, "drop_factor": 1.5},
            {"key": "ji",       "name": "吉",   "weight": 25, "combat_factor": 1.05, "drop_factor": 1.2},
            {"key": "ping",     "name": "中平", "weight": 45, "combat_factor": 1.00, "drop_factor": 1.0},
            {"key": "xiaoxiong", "name": "小凶", "weight": 20, "combat_factor": 0.97, "drop_factor": 0.8},
            {"key": "daxiong",  "name": "大凶", "weight": 5,  "combat_factor": 0.90, "drop_factor": 0.5},
        ],
    },
    # ---- 打怪战斗：今日装备战力 vs 怪 power_req，有胜负 ----
    "combat": {
        "factor_min": 0.8,
        "factor_max": 1.2,
        "fortune_affects_hunt": True,
        "crush_margin": 1.5,
        "weak_margin": 0.8,
        "no_event_weight": 60,
        "events": {
            "slip":      {"weight": 25, "power_mult": 0.75},  # 脚底打滑：有效战力 ×0.75
            "insight":   {"weight": 25, "exp_mult": 1.5},     # 弱点看破：胜则经验 ×1.5
            "desperate": {"weight": 35, "power_mult": 1.6},   # 绝境爆发：有效战力 ×1.6 可翻盘
        },
        # ---- 精英怪：遭遇时小概率升级，更难打（power_req×）但胜则更肥（经验/掉落×）。藏着不外显，撞上才知道 ----
        "elite": {"chance": 0.12, "power_mult": 1.6, "exp_mult": 1.8, "drop_mult": 2.0},
    },
    # ---- 打怪奖励：经验按等级（胜/负不同），掉落系数，少量积分（串起送礼经济）----
    "challenge": {
        "win_exp_base": 60, "win_exp_per_level": 10,
        "lose_exp_base": 15, "lose_exp_per_level": 2,
        "win_drop_mult": 1.0, "lose_drop_mult": 0.3,
        "win_points": 30, "lose_points": 10,
    },
    # ---- 组队：成功率随羁绊等级爬升（Lv6 顶级羁绊≈封顶必成）；失败退化为发起人单刷 ----
    "team": {
        "base_success": 0.35, "per_level": 0.12,   # Lv1=35%，每升一级 +12%
        "min_success": 0.10, "max_success": 0.95,   # 封底（含负档硬拉）/ 封顶
        "exp_bonus_per_level": 0.05, "exp_bonus_max": 0.50,  # 组队经验加成：每级 +5%，封顶 +50%
    },
    # ---- 称号：累计经验→等级→称号（纯派生、零存储，仿羁绊取档）。显示在「我的角色」与排行榜 ----
    "titles": [
        {"min_level": 1,  "name": "见习冒险者"},
        {"min_level": 3,  "name": "萌新猎人"},
        {"min_level": 6,  "name": "熟练打野人"},
        {"min_level": 10, "name": "老练讨伐者"},
        {"min_level": 15, "name": "区域强者"},
        {"min_level": 20, "name": "传说猎手"},
        {"min_level": 30, "name": "殿堂级冒险家"},
    ],
    # ---- 今日增益：按日期决定、全群一致、不预告；仅生效时打怪播报补一行（藏着不外显）----
    "daily_buffs": {
        "plain": {"name": "平日",       "weight": 6, "exp_mult": 1.0, "drop_mult": 1.0},
        "drop":  {"name": "掉落翻倍日", "weight": 2, "exp_mult": 1.0, "drop_mult": 2.0},
        "exp":   {"name": "经验涌动日", "weight": 2, "exp_mult": 1.5, "drop_mult": 1.0},
    },
    # ---- 野怪：power_req 作难度；今日装备战力随等级涨，自然匹配。drops 为掉落表 ----
    "monsters": [
        {"name": "史莱姆", "power_req": 15, "weight": 40, "drops": [{"item": "经验书", "chance": 0.10}]},
        {"name": "哥布林", "power_req": 35, "weight": 30,
         "drops": [{"item": "经验书", "chance": 0.12}, {"item": "双倍经验卡", "chance": 0.05}]},
        {"name": "座狼",   "power_req": 60, "weight": 20, "drops": [{"item": "双倍经验卡", "chance": 0.08}]},
        {"name": "食人魔", "power_req": 95, "weight": 10, "drops": [{"item": "双倍经验卡", "chance": 0.12}]},
    ],
    # ---- 道具（消耗品，经验向）：effect.type = exp_buff / exp_grant ----
    "items": [
        {"name": "双倍经验卡", "desc": "下次打怪经验翻倍", "effect": {"type": "exp_buff", "uses": 1, "mult": 2}},
        {"name": "经验书", "desc": "立即获得 80 经验", "effect": {"type": "exp_grant", "amount": 80}},
    ],
    # ---- 文案。占位符：{a}=真@；其余 {exp}{level}{newlevel}{monster}{cost}{forge}{name}{amount}{loot} 为文本 ----
    "copy": {
        "signin_exp": ["🗡️ 签到完成，经验 +{exp}，今日装备已就位（Lv{level}）。"],
        "hunt_encounter": [
            "{a} 用今日装备迎向 Lv? 的【{monster}】！",
            "{a} 提刀出门，撞上了【{monster}】！",
        ],
        "hunt_win": ["击败了【{monster}】，经验 +{exp}、积分 +{points}（今日装备已损耗）。"],
        "hunt_lose": ["不敌【{monster}】，狼狈撤退，经验 +{exp}、积分 +{points}（今日装备已损耗）。"],
        "levelup": ["⬆️ 升级了！Lv{level} → Lv{newlevel}！"],
        "event_slip": ["💢 脚底一滑，这一下没使上全力……"],
        "event_insight": ["🎯 看破了【{monster}】的破绽，经验大涨！"],
        "event_desperate": ["🔥 被逼到绝境，反而爆发出惊人的战力！"],
        "hunt_exp_buffed": ["✨ 双倍经验卡生效，这次经验翻倍！"],
        "hunt_loot": ["📦 战利品掉落：{loot}。"],
        "forge_ok": ["🔨 强化成功，今日装备更锋利了（已强化 ×{forge}，花费 {cost} 积分）。"],
        "use_exp_buff": ["📖 用了【{name}】，下次打怪经验 ×{mult} 已就绪。"],
        "use_exp_grant": ["📖 用了【{name}】，经验 +{amount}。"],
        # 组队（{a}{b}=真@；{name}{exp}{points}{loot}{levelup}{b_name}=文本）
        "team_win": ["🤝 {a} 拉上 {b} 合力出击，一举击败了【{monster}】！"],
        "team_lose": ["🤝 {a} 拉上 {b} 并肩死磕【{monster}】，可惜对手太猛，惜败而归……"],
        "team_member": ["· {name}：经验 +{exp}、积分 +{points}{loot}{levelup}"],
        "team_fail": ["{a} 想拉 {b_name} 组队，却没能拉动，只好自己上……"],
        # 精英怪遭遇（{a}=真@；{monster}=文本）
        "hunt_encounter_elite": [
            "{a} 迎面撞上气势汹汹的 精英·{monster}！",
            "{a} 提刀出门，竟遇上了 精英·{monster}！",
        ],
        # 连签 / 今日增益（{streak}{bonus}{buff} 为文本）
        "signin_streak": ["🔥 连签 {streak} 天，额外经验 +{bonus}！"],
        "daily_buff": ["✨ 今日「{buff}」加持，收获更丰！"],
        # 排行榜
        "rank_title": ["🏆 本群冒险者排行（等级榜）："],
    },
    "errors": {
        "private_only": "冒险要在群里玩哦。",
        "sleeping": "💤 这会儿小彰睡着了，等 6 点天亮以后再来探险吧……",
        "need_equip": "今天还没签到领装备，先「签到」再来打怪。",
        "equip_broken": "今日装备已经在上一场打怪里损坏了，明天签到再领新的。",
        "forge_no_equip": "今天还没领装备，先「签到」。",
        "forge_broken": "今日装备已损坏，强化不了，明天再来。",
        "forge_max": "今日装备强化次数已达上限（{max}）。",
        "forge_poor": "积分不够，本次强化要 {cost}，你只有 {total}。",
        "bag_empty": "🎒 背包空空如也，去打怪掉点东西吧～",
        "use_need_name": "要用哪个道具？比如：使用 经验书。",
        "item_unknown": "没有「{name}」这个道具哦。",
        "item_none": "你背包里没有【{name}】。",
        "team_need_target": "组队要 @一位群友 哦，比如：组队 @某人。",
        "team_self": "自己跟自己组队？还是 @ 个群友吧。",
        "team_bot": "小彰不下场打怪啦，去 @ 个群友组队吧。",
        "rank_empty": "本群还没人开始冒险，先「签到」领装备再「打怪」吧～",
    },
}


def _load_config() -> dict:
    """加载 RPG 配置；无文件 / 解析失败时回落到默认配置的深拷贝。"""
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


def _line(key: str, **fmt) -> str:
    """随机取一条文案并安全格式化（缺占位符不抛错）。"""
    pool = _copy(key)
    template = random.choice(pool) if pool else ""
    try:
        return template.format(**fmt)
    except (KeyError, IndexError):
        return template


def reload_rpg_config() -> None:
    """热重载 RPG 配置（原地 clear+update，保持已持有引用不失效）。"""
    RPG_CONFIG.clear()
    RPG_CONFIG.update(_load_config())
    logger.info("🔄 RPG 配置已热重载")
