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
    # ---- 签到：保留轻量背景经验（积分由 gift 的签到发放；主成长仍靠打怪）----
    "signin": {"exp": 10},
    # ---- 连续签到：从第二天起每天多给一点，默认把签到经验从 10 逐步抬到 20；断签重置 ----
    "signin_streak": {"per_day": 2, "cap": 10},
    # ---- 等级曲线：升到 L 级累计需 base*(L-1)*L/2 经验 ----
    "level_curve": {"base": 135},
    # ---- 今日装备：战力 = base + 等级*per_level + rand(0,var) + 强化次数*forge.step（战力为隐藏值，不外显）----
    "equip": {
        "base": 10,
        "per_level": 5,
        "var": 6,
        "rebuy_cost": 100,
        "rebuy_points_mult": 0.5,
        "rebuy_exp_mult": 0.5,
        "rebuy_max_per_day": 1,
    },
    # ---- 强化（积分出口）：优先按 costs 分段收费；未配时回退到 cost_base*n。+step 战力，每日最多 max_per_day 次，次日重置 ----
    "forge": {"cost_base": 100, "costs": [30, 60, 90], "step": 6, "max_per_day": 3},
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
        "factor_min": 0.9,
        "factor_max": 1.1,
        "fortune_affects_hunt": True,
        "crush_margin": 1.5,
        "weak_margin": 0.8,
        "no_event_weight": 45,
        "events": {
            "slip":      {"weight": 18, "power_mult": 0.74},  # 脚底打滑：有效战力 ×0.74
            "insight":   {"weight": 22, "exp_mult": 1.6},     # 弱点看破：胜则经验 ×1.6
            "desperate": {"weight": 28, "power_mult": 1.60},  # 绝境爆发：有效战力 ×1.60 可翻盘
        },
        # ---- 遭遇分段：名称权重允许以后追加怪物而不改旧分段；最后一档作为未来等级的安全回退 ----
        "encounter_brackets": [
            {"max_level": 2, "weights": {"史莱姆": 36, "泥怪": 32, "哥布林": 20, "野狼": 12}},
            {"max_level": 4, "weights": {"史莱姆": 20, "泥怪": 24, "哥布林": 28, "野狼": 28}},
            {
                "max_level": 6,
                "weights": {"史莱姆": 10, "泥怪": 14, "哥布林": 20, "野狼": 24, "座狼": 18, "骸骨兵": 14},
            },
            {
                "max_level": 8,
                "weights": {"史莱姆": 4, "泥怪": 8, "哥布林": 12, "野狼": 16, "座狼": 18, "骸骨兵": 18,
                            "食人魔": 14, "魔铠兵": 10},
            },
            {
                "max_level": 10,
                "weights": {"哥布林": 2, "野狼": 6, "座狼": 10, "骸骨兵": 14, "食人魔": 18, "魔铠兵": 18,
                            "石像鬼": 18, "双足飞龙": 14},
            },
            {
                "max_level": 13,
                "weights": {"野狼": 2, "座狼": 8, "骸骨兵": 12, "食人魔": 16, "魔铠兵": 18, "石像鬼": 18,
                            "双足飞龙": 20, "龙": 6},
            },
            {
                "max_level": 15,
                "weights": {"座狼": 2, "骸骨兵": 8, "食人魔": 12, "魔铠兵": 16, "石像鬼": 18,
                            "双足飞龙": 20, "龙": 24},
            },
            {
                "max_level": 18,
                "weights": {"骸骨兵": 2, "食人魔": 6, "魔铠兵": 10, "石像鬼": 18, "双足飞龙": 24,
                            "龙": 30, "风暴狮鹫": 10},
            },
            {
                "max_level": 21,
                "weights": {"食人魔": 2, "魔铠兵": 6, "石像鬼": 10, "双足飞龙": 18, "龙": 24,
                            "风暴狮鹫": 28, "魔化奇美拉": 12},
            },
            {
                "max_level": 24,
                "weights": {"魔铠兵": 2, "石像鬼": 6, "双足飞龙": 10, "龙": 16, "风暴狮鹫": 22,
                            "魔化奇美拉": 28, "深渊骑士": 16},
            },
            {
                "max_level": 27,
                "weights": {"石像鬼": 2, "双足飞龙": 6, "龙": 10, "风暴狮鹫": 16, "魔化奇美拉": 22,
                            "深渊骑士": 28, "冰霜巨人": 16},
            },
            {
                "max_level": 30,
                "weights": {"龙": 4, "风暴狮鹫": 8, "魔化奇美拉": 16, "深渊骑士": 24, "冰霜巨人": 30,
                            "远古巨龙": 18},
            },
            {
                "max_level": None,
                "weights": {"风暴狮鹫": 2, "魔化奇美拉": 8, "深渊骑士": 18, "冰霜巨人": 30, "远古巨龙": 42},
            },
        ],
        # ---- 精英怪：遭遇时小概率升级，更难打（power_req×）但胜则更肥（经验/掉落×）。藏着不外显，撞上才知道 ----
        "elite": {"chance": 0.12, "power_mult": 1.6, "exp_mult": 1.8, "drop_mult": 2.0},
    },
    # ---- 打怪奖励：经验按等级（胜/负不同），掉落系数，少量积分（串起送礼经济）----
    "challenge": {
        "win_exp_base": 55, "win_exp_per_level": 7,
        "lose_exp_base": 22, "lose_exp_per_level": 3,
        "win_drop_mult": 1.0, "lose_drop_mult": 0.3,
        "win_points": 15, "lose_points": 5,
    },
    # ---- 冒险补给：每周前 5 次常规价格，第 6/7 次作为高价存量积分出口 ----
    "adventure_supply": {
        "weekly_costs": [140, 140, 140, 140, 140, 200, 300],
        "exp": 30,
        "pool": [
            {"item": "旅人的行囊", "weight": 34},
            {"item": "龙骑士的地图", "weight": 30},
            {"item": "厨子的美食", "weight": 20},
            {"item": "神官的护符", "weight": 12},
            {"item": "勇者的远征套装", "weight": 3},
            {"item": "大葱味蛋糕", "weight": 1},
        ],
    },
    # ---- 小奇遇：普通单刷与成功组成的双人战斗结算后，低概率补一点旅途感与轻奖励 ----
    "minor_encounters": {
        "chance": 0.06,
        "team_chance": 0.04,
        "events": {
            "supply_cache": {"weight": 32, "when": "any", "exp": 10, "points": 1},
            "campfire": {"weight": 24, "when": "lose", "exp": 14},
            "worn_chest": {
                "weight": 24,
                "when": "win",
                "rewards": [
                    {"type": "points", "amount": 5, "label": "破旧积分卡", "weight": 4},
                    {"type": "exp", "amount": 10, "label": "破旧经验券", "weight": 4},
                    {"type": "item", "name": "彰冬无料券", "amount": 1, "weight": 2},
                ],
            },
            "lost_pouch": {"weight": 20, "when": "win", "points": 3},
        },
        "team_events": {
            "supply_cache": {"weight": 32, "when": "any", "exp": 10, "points": 2},
            "campfire": {"weight": 24, "when": "lose", "exp": 14},
            "worn_chest": {
                "weight": 24,
                "when": "win",
                "rewards": [
                    {"type": "points", "amount": 5, "label": "破旧积分卡", "weight": 4},
                    {"type": "exp", "amount": 10, "label": "破旧经验券", "weight": 4},
                    {"type": "item", "name": "彰冬无料券", "amount": 1, "weight": 2},
                ],
            },
            "lost_pouch": {"weight": 20, "when": "win", "points": 4},
        },
    },
    # ---- 主动单刷补偿：只在直接使用「今日打怪」时生效；不影响组队失败后退化单刷 ----
    "solo": {
        "power_bonus": 0.06,
        "win_exp_bonus": 0.12,
        "lose_exp_bonus": 0.08,
    },
    # ---- 组队：正羁绊提成功率与掉落；负羁绊会更难拉动，但下探幅度比正向增幅更缓 ----
    "team": {
        "base_success": 0.50, "per_level": 0.10,   # Lv1=50%，每升一级 +10%，Lv6 封顶 95%
        "negative_per_level": 0.05,                # 负羁绊每档只额外 -5%，别一下子降得太狠
        "min_success": 0.25, "max_success": 0.95,   # 封底（深度负羁绊）/ 封顶
        "power_bonus": 0.05,  # 组队基础战力加成：固定 +5%
        "exp_bonus_per_level": 0.00, "exp_bonus_max": 0.00,  # 角色经验不再因组队额外抬高
        "drop_bonus_per_level": 0.04, "drop_bonus_max": 0.20,  # 组队掉落加成：每级 +4%，封顶 +20%
        "bond_gain_base": 2, "bond_gain_win_bonus": 2, "bond_gain_daily_limit": 1,  # 成功组队后的小额羁绊增长
        "no_event_weight": 45,
        "events": {
            "focus_fire": {"weight": 18, "power_mult": 1.10, "exp_mult": 1.10},
            "cover_route": {"weight": 16, "drop_mult": 1.35},
            "follow_up": {"weight": 14, "exp_mult": 1.20},
            "missed_beat": {"weight": 12, "power_mult": 0.90},
        },
        "negative": {
            "mild_threshold": -50,
            "deep_threshold": -300,
            "chance_mild": 0.35,
            "chance_medium": 0.55,
            "chance_deep": 0.75,
            "events": {
                "friction": {"weight": 18, "power_mult": 0.92},
                "misread": {"weight": 16, "exp_mult": 0.92},
                "loose_guard": {"weight": 14, "drop_mult": 0.85},
                "break_ice": {"weight": 12, "bond_bonus": 2},
            },
        },
        "fail_flavor": {"hesitate": 4, "late_reply": 3, "out_of_step": 3},
    },
    # ---- 战斗特判：普通 RPG 战斗专用。单刷胜利 / 单刷失败 / 组队失败都有独立 3% 援护判定 ----
    "support": {
        "chance": 0.03,
        "akito_success": {"exp_ratio": 0.35, "points_ratio": 0.30},
        "akito_fail": {"exp_ratio": 0.35, "points_ratio": 0.30},
        "duo_combo": {"exp_ratio": 0.35, "points_ratio": 0.30},
    },
    # ---- 世界 BOSS：极低概率在常规打怪后出现；强度按近 7 日活跃签到人数缩放 ----
    "world_boss": {
        "spawn_chance": 0.03,
        "activity_window_days": 7,
        "activity_min_users": 3,
        "activity_scale_cap": 12,
        "hp_scale_extra_rate": 0.30,
        "hp_scale_max": 24,
        "reward_scale_extra_rate": 0.15,
        "reward_scale_max": 16,
        "hp_factor": 0.75,
        "damage_factor_min": 0.92,
        "damage_factor_max": 1.08,
        "team_bond": {
            "base": 1,
            "kill_bonus": 1,
            "negative_bonus": 1,
            "daily_limit": 1,
        },
        "special_drop": {
            "chance": 0.03,
            "items": {
                "赤鳞灾龙": "赤鳞龙鳞",
                "断潮魔虾": "断潮虾壳",
                "焦壳披萨王": "焦香披萨块",
            },
        },
        "rewards": {
            "exp_fixed": 12,
            "exp_pool_per_scale": 60,
            "points_fixed": 2,
            "points_pool_per_scale": 8,
            "last_hit_exp_bonus": 8,
            "last_hit_points_bonus": 2,
            "unfinished_reward_mult": 0.5,
        },
        "boss_names": [
            "赤鳞灾龙",
            "断潮魔虾",
            "焦壳披萨王",
        ],
    },
    # ---- 称号：累计经验→等级→称号（纯派生、零存储，仿羁绊取档）。显示在「我的角色」与排行榜 ----
    "titles": [
        {"min_level": 1,  "name": "见习冒险者"},
        {"min_level": 2,  "name": "启程旅者"},
        {"min_level": 4,  "name": "新锐猎手"},
        {"min_level": 6,  "name": "熟练冒险者"},
        {"min_level": 8,  "name": "资深探索者"},
        {"min_level": 10, "name": "老练讨伐者"},
        {"min_level": 13, "name": "高阶开拓者"},
        {"min_level": 16, "name": "杰出冒险家"},
        {"min_level": 20, "name": "精英猎手"},
        {"min_level": 24, "name": "传奇冒险者"},
        {"min_level": 30, "name": "殿堂开拓者"},
    ],
    # ---- 今日增益：按日期决定、全群一致、不预告；仅生效时打怪播报补一行（藏着不外显）----
    "daily_buffs": {
        "plain": {"name": "平日",       "weight": 6, "exp_mult": 1.0, "drop_mult": 1.0},
        "drop":  {"name": "掉落翻倍日", "weight": 2, "exp_mult": 1.0, "drop_mult": 2.0},
        "exp":   {"name": "经验涌动日", "weight": 2, "exp_mult": 1.5, "drop_mult": 1.0},
    },
    # ---- 野怪：power_req 作难度；今日装备战力随等级涨，自然匹配。drops 为掉落表 ----
    "monsters": [
        {"name": "史莱姆", "power_req": 15, "weight": 30,
         "drops": [{"item": "经验书", "chance": 0.10}, {"item": "彰冬无料券", "chance": 0.08}]},
        {"name": "泥怪", "power_req": 20, "weight": 24,
         "drops": [{"item": "经验书", "chance": 0.12}, {"item": "彰冬无料券", "chance": 0.08},
                   {"item": "双倍经验卡", "chance": 0.03}]},
        {"name": "哥布林", "power_req": 25, "weight": 25,
         "drops": [{"item": "经验书", "chance": 0.12}, {"item": "双倍经验卡", "chance": 0.05},
                   {"item": "彰冬无料券", "chance": 0.08}, {"item": "彰冬谷子券", "chance": 0.05}]},
        {"name": "野狼", "power_req": 32, "weight": 22,
         "drops": [{"item": "经验书", "chance": 0.08}, {"item": "双倍经验卡", "chance": 0.06},
                   {"item": "彰冬谷子券", "chance": 0.05}]},
        {"name": "座狼",   "power_req": 40, "weight": 20,
         "drops": [{"item": "双倍经验卡", "chance": 0.08},
                   {"item": "彰冬谷子券", "chance": 0.06}, {"item": "彰冬豆豆眼券", "chance": 0.04}]},
        {"name": "骸骨兵", "power_req": 48, "weight": 17,
         "drops": [{"item": "双倍经验卡", "chance": 0.09},
                   {"item": "彰冬谷子券", "chance": 0.06}, {"item": "彰冬豆豆眼券", "chance": 0.04}]},
        {"name": "食人魔", "power_req": 55, "weight": 15,
         "drops": [{"item": "双倍经验卡", "chance": 0.10},
                   {"item": "彰冬豆豆眼券", "chance": 0.05}, {"item": "彰冬立牌券", "chance": 0.03}]},
        {"name": "魔铠兵", "power_req": 64, "weight": 12,
         "drops": [{"item": "双倍经验卡", "chance": 0.11},
                   {"item": "彰冬豆豆眼券", "chance": 0.06}, {"item": "彰冬立牌券", "chance": 0.03}]},
        {"name": "石像鬼", "power_req": 75, "weight": 10,
         "drops": [{"item": "双倍经验卡", "chance": 0.12},
                   {"item": "彰冬豆豆眼券", "chance": 0.06}, {"item": "彰冬立牌券", "chance": 0.04}]},
        {"name": "双足飞龙", "power_req": 86, "weight": 7,
         "drops": [{"item": "双倍经验卡", "chance": 0.14},
                   {"item": "彰冬豆豆眼券", "chance": 0.04}, {"item": "彰冬立牌券", "chance": 0.05}]},
        {"name": "龙",     "power_req": 95, "weight": 5,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬立牌券", "chance": 0.06}]},
        {"name": "风暴狮鹫", "power_req": 106, "weight": 4, "reward_exp_mult": 1.03,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬豆豆眼券", "chance": 0.04}, {"item": "彰冬立牌券", "chance": 0.06}]},
        {"name": "魔化奇美拉", "power_req": 120, "weight": 3, "reward_exp_mult": 1.05,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬豆豆眼券", "chance": 0.04}, {"item": "彰冬立牌券", "chance": 0.07}]},
        {"name": "深渊骑士", "power_req": 134, "weight": 2, "reward_exp_mult": 1.07,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬立牌券", "chance": 0.08}]},
        {"name": "冰霜巨人", "power_req": 149, "weight": 1, "reward_exp_mult": 1.09,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬立牌券", "chance": 0.09}]},
        {"name": "远古巨龙", "power_req": 164, "weight": 1, "reward_exp_mult": 1.12,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬立牌券", "chance": 0.10}]},
    ],
    # ---- 道具：普通消耗品 + 需要主动启用的冒险战备 ----
    "items": [
        {"name": "双倍经验卡", "desc": "下次打怪经验翻倍", "effect": {"type": "exp_buff", "uses": 1, "mult": 2}},
        {"name": "经验书", "desc": "立即获得 80 经验", "effect": {"type": "exp_grant", "amount": 80}},
        {"name": "彰冬无料券", "desc": "赠送「彰冬无料」，羁绊+12", "effect": {"type": "gift", "gift_name": "彰冬无料"}},
        {"name": "彰冬谷子券", "desc": "赠送「彰冬谷子」，羁绊+28", "effect": {"type": "gift", "gift_name": "彰冬谷子"}},
        {"name": "彰冬豆豆眼券", "desc": "赠送「彰冬豆豆眼」，羁绊+60", "effect": {"type": "gift", "gift_name": "彰冬豆豆眼"}},
        {"name": "彰冬立牌券", "desc": "赠送「彰冬亚克力立牌」，羁绊+85", "effect": {"type": "gift", "gift_name": "彰冬亚克力立牌"}},
        {"name": "旅人的行囊", "desc": "接下来2次普通个人/组队挑战：战力+10%、经验+25%（世界BOSS不生效）",
         "effect": {"type": "battle_supply", "uses": 2, "power_mult": 1.10, "exp_mult": 1.25}},
        {"name": "龙骑士的地图", "desc": "接下来2次普通个人/组队挑战：掉落率×2、经验+20%（世界BOSS不生效）",
         "effect": {"type": "battle_supply", "uses": 2, "exp_mult": 1.20, "drop_mult": 2.0}},
        {"name": "厨子的美食", "desc": "接下来2次普通个人/组队挑战：战力+15%、经验+40%（世界BOSS不生效）",
         "effect": {"type": "battle_supply", "uses": 2, "power_mult": 1.15, "exp_mult": 1.40}},
        {"name": "神官的护符", "desc": "下一次普通个人/组队挑战失败时转为成功，经验额外+50%（世界BOSS不生效）",
         "effect": {"type": "battle_guard", "uses": 1, "rescue_exp_mult": 1.50}},
        {"name": "勇者的远征套装", "desc": "接下来3次普通个人/组队挑战：装备视为强化满、经验×2、掉落率×2（世界BOSS不生效）",
         "effect": {"type": "battle_supply", "uses": 3, "full_forge": True, "exp_mult": 2.0,
                    "drop_mult": 2.0}},
        {"name": "大葱味蛋糕", "desc": "赠送给群友，使其下一场普通个人/组队挑战：经验-15%、积分-10%、掉落率-20%（世界BOSS不生效）",
         "supply_hint": "· 使用方式：使用 大葱味蛋糕@某人（效果由对方下一场普通个人/组队挑战承受；世界BOSS不生效）",
         "effect": {"type": "battle_debuff_gift", "uses": 1, "exp_mult": 0.85,
                    "points_mult": 0.90, "drop_mult": 0.80}},
    ],
    # ---- 文案。占位符：{a}=真@；其余 {exp}{level}{newlevel}{monster}{cost}{forge}{name}{amount}{loot} 为文本 ----
    "copy": {
        "signin_exp": ["🗡️ 签到记上了。经验 +{exp}，今日装备也给你备好了（Lv{level}）。"],
        "hunt_encounter": [
            "{a} 在野外遭遇了【{monster}】。",
            "{a} 出发没多久，就遇上了【{monster}】。",
        ],
        "hunt_win": ["已击败【{monster}】。经验 +{exp}、积分 +{points}（今日装备已损耗）。"],
        "hunt_lose": ["未能击败【{monster}】。经验 +{exp}、积分 +{points}（今日装备已损耗）。"],
        "levelup": ["⬆️ 等级上去了。Lv{level} → Lv{newlevel}。"],
        "hunt_fail_turn": [
            "眼看就要败退，转机却在最后一刻出现了。",
            "原以为这场战斗就要到此为止，局势却忽然有了变化。",
            "局面已经岌岌可危，没想到最后还是等来了转机。",
            "胜负几乎已成定局，战场上却忽然出现了一线生机。",
        ],
        "event_slip": ["💢 行动受阻，这一击没能完全发挥。"],
        "event_slip_win": ["💢 行动受阻，但还是成功击败了【{monster}】。"],
        "event_slip_lose": ["💢 行动受阻，这次没能稳住局面。"],
        "event_insight": ["🎯 看穿了【{monster}】的弱点，攻击更有效了。"],
        "event_desperate": ["🔥 陷入苦战时强撑住了阵脚。"],
        "event_desperate_win": ["🔥 陷入苦战时强撑住了阵脚，成功反败为胜。"],
        "event_desperate_lose": ["🔥 即使强撑住阵脚，也还是没能扭转战局。"],
        "hunt_exp_buffed": ["✨ 双倍经验卡起效，这次经验翻倍。"],
        "hunt_loot": ["📦 掉落到手：{loot}。"],
        "minor_encounter_supply_cache": ["【奇遇】路边翻出一袋还没被雨淋透的补给。"],
        "minor_encounter_campfire": ["【奇遇】撤出战场时，在路边的营火旁稍微歇了口气。"],
        "minor_encounter_worn_chest": ["【奇遇】战斗结束后，在废墟旁翻出了一个没上锁的小箱子。"],
        "minor_encounter_lost_pouch": ["【奇遇】清点战场时，顺手捡到一个被人遗落的小钱袋。"],
        "minor_encounter_reward": ["· 额外收获：{parts}。"],
        "minor_encounter_levelup": ["⬆️ 旅途中又长了点见识。Lv{level} → Lv{newlevel}。"],
        "minor_encounter_team_supply_cache": ["【奇遇】两人在路边翻出一袋还没被雨淋透的补给。"],
        "minor_encounter_team_campfire": ["【奇遇】撤出战场后，两人在路边的营火旁稍微歇了口气。"],
        "minor_encounter_team_worn_chest": ["【奇遇】战斗结束后，两人在废墟旁翻出了一个没上锁的小箱子。"],
        "minor_encounter_team_lost_pouch": ["【奇遇】清点战场时，两人顺手捡到一个被人遗落的小钱袋。"],
        "minor_encounter_team_reward": ["· 两人各自额外获得：{parts}。"],
        "minor_encounter_team_member_reward": ["· {name}：额外获得 {parts}。"],
        "minor_encounter_team_member_levelup": ["· {name}：升级 Lv{level}→Lv{newlevel}。"],
        "forge_ok": ["🔨 强化好了。今日装备更稳了（已强化 ×{forge}，花费 {cost} 积分）。"],
        "rebuy_ok": ["🛡️ 替换装备已就位，花了 {cost} 积分。不过这套是临时凑的，打怪经验和积分都会减半。"],
        "use_exp_buff": ["📖 【{name}】已储备。会在没有常规战备生效的下一次普通挑战中使经验 ×{mult}。"],
        "use_exp_grant": ["📖 【{name}】用了。经验 +{amount}。"],
        "supply_open": ["📦 冒险补给已开启。\n· 消耗 {cost} 积分（本周 {count}/{max}）\n· 获得【{name}】×1\n（效果：{effect}）\n· 经验 +{exp}，发送“{usage}”后生效{levelup}"],
        "gift_battle_debuff": ["🥬 {a} 把【{name}】送给了 {b}。大葱味已经腌进去了：对方下一场普通个人/组队挑战经验 -15%、积分 -10%、掉落率 -20%（当前排队 {uses} 场；世界BOSS不生效）。"],
        "use_battle_supply": ["🎒 【{name}】已整备。\n· 效果：{parts}\n· 规则：接下来 {uses} 场普通个人/组队挑战生效；期间双倍经验卡暂缓且不消耗；世界BOSS不生效。"],
        "use_battle_guard": ["🛡️ 【{name}】已整备。\n· 效果：{parts}\n· 规则：在下一次普通个人/组队挑战于其他援护判定后仍然失败时触发；护符本身不压制双倍经验卡；世界BOSS不生效。"],
        "battle_supply_active": ["🎒 【{name}】生效：{parts}（剩余 {uses} 场；仅限普通个人/组队挑战）"],
        "battle_debuff_active": ["🥬 【{name}】的怪味发作：经验 -{exp}% / 积分 -{points}% / 掉落率 -{drop}%（剩余 {uses} 场；仅限普通个人/组队挑战）"],
        "battle_guard_triggered": ["🛡️ 【{name}】护住了战线，本次挑战转为成功，经验额外 +50%。"],
        "team_battle_guard_triggered": ["🛡️ {name} 的【{item}】护住了两人的战线，本次挑战转为成功；护符持有者经验额外 +50%。"],
        # 组队（{a}{b}=真@；{name}{exp}{points}{loot}{levelup}{b_name}=文本）
        "team_win": [
            "🤝 {a} 与 {b} 一同出击，成功击败了【{monster}】。",
            "🤝 {a} 和 {b} 组队作战，顺利讨伐了【{monster}】。",
            "🤝 {a} 与 {b} 联手战斗，最终拿下了【{monster}】。",
            "🤝 {a} 和 {b} 协力迎战，成功解决了【{monster}】。",
        ],
        "team_lose": [
            "🤝 {a} 与 {b} 一同迎战【{monster}】，但还是没能取胜。",
            "🤝 {a} 和 {b} 组队作战，可惜未能击败【{monster}】。",
            "🤝 {a} 与 {b} 联手挑战【{monster}】，最终还是败下阵来。",
            "🤝 {a} 和 {b} 协力作战，但这次没能拿下【{monster}】。",
        ],
        "team_bonus": ["✨ 协作加成：{parts}。"],
        "team_event_focus_fire": ["⚔️ 两人的攻击集中在一处，造成了更有效的打击。"],
        "team_event_cover_route": ["🧭 一人牵制、一人搜索，额外带回了更多战利品。"],
        "team_event_follow_up": ["🔁 前后配合顺利，追加攻击打得很完整。"],
        "team_event_missed_beat": ["😵 配合出现偏差，这一轮没能完全发挥实力。"],
        "team_negative_event_friction": ["⚠️ 两人还没磨合好，这一轮的配合明显迟滞了。"],
        "team_negative_event_misread": ["⚠️ 两人的判断出现偏差，这次进攻没能完全展开。"],
        "team_negative_event_loose_guard": ["⚠️ 配合还不够稳定，搜索与掩护都慢了一步。"],
        "team_negative_event_break_ice": ["🫱 并肩打完这一战后，两人之间的气氛似乎缓和了一点。"],
        "team_bond_gain": ["💞 同好羁绊 +{amount}。"],
        "team_member": ["· {name}：经验 +{exp}、积分 +{points}{loot}{levelup}"],
        "team_fail": [
            "{a} 试着邀请 {b_name} 一起出战，但没能组队成功，只好独自前往。",
            "{a} 想和 {b_name} 一起行动，可惜这次没能成功会合。",
            "{a} 原本准备和 {b_name} 同行，最后还是只能自己应战。",
            "{a} 邀请了 {b_name} 协助作战，但最终没能组成队伍。",
        ],
        "team_fail_event_hesitate": ["……{b_name} 似乎迟疑了一下，没能及时加入战斗。"],
        "team_fail_event_late_reply": ["……{b_name} 赶来得稍晚，没能在战斗开始前会合。"],
        "team_fail_event_out_of_step": ["……两人没能顺利会合，这次组队作战失败了。"],
        "team_fail_turn": [
            "{a} 原本已经准备独自迎战，转机却在最后一刻出现了。",
            "{a} 本以为这次只能单独出战，没想到局势忽然有了变化。",
            "{a} 都已经打算一个人撑下这一战，结果最后还是等来了转机。",
            "{a} 眼看要改成独自作战，战线那边却突然出现了新的变化。",
        ],
        "support_akito_success": [
            "【追击】橙发的勇者从旁挥剑追上。\n“『真·龙王烈火斩』！……”\n【{monster}】被彻底消灭。\n· 额外获得经验 +{exp}、积分 +{points}"
        ],
        "support_akito_fail": [
            "【追击】橙发的勇者拦在怪物身前，反手补上一剑。\n【{monster}】被这一击逼退。\n· 额外获得经验 +{exp}、积分 +{points}"
        ],
        "support_toya_rescue": [
            "【援护】蓝灰双色发的神官挥动法杖。\n“释放治愈魔法——『神圣治愈』！”\n战局稳住了。\n· 本次挑战转为成功"
        ],
        "support_duo_combo": [
            "【联携】双色发神官在远处施放了支援魔法稳住阵型，橙色的勇者趁机挥剑突入追击。\n【{monster}】被一举击破。\n· 本次挑战转为成功\n· 额外获得经验 +{exp}、积分 +{points}"
        ],
        "team_support_hesitate": [
            "【支援】路过的勇者与神官的鼓励重新使{b_name}充满了勇气。\n· 本次组队成立"
        ],
        "team_support_late_reply": [
            "【支援】蓝灰双色发的神官施放传送魔法，将{b_name}送到了{a_name}附近。\n· 本次组队成立"
        ],
        "team_support_out_of_step": [
            "【支援】橙发的勇者先行稳住敌人攻势，{b_name}得以及时加入战场。\n· 本次组队成立"
        ],
        # 世界 BOSS
        "world_boss_spawn": ["🌍 世界BOSS【{monster}】出现了。"],
        "world_boss_spawn_scale": ["· 这次的强度按近 7 日活跃冒险者规模生成。"],
        "world_boss_status_head": ["🌍 世界BOSS【{monster}】"],
        "world_boss_status_hp": ["· 生命：{hp}/{max_hp}（{percent}%）"],
        "world_boss_status_scale": ["· 规模：近 7 日活跃 {recent_active} 人，本次按 {scale_count} 人强度生成。"],
        "world_boss_status_empty": ["· 目前还没有人造成伤害。"],
        "world_boss_status_rank": ["· 当前贡献："],
        "world_boss_status_entry": ["{rank}. {name}　{damage} 伤害"],
        "world_boss_status_hint": ["· 指令：攻击世界BOSS / 组队世界BOSS@某人 / 强化世界BOSS装备"],
        "world_boss_force_opened": ["🛠️ 已强制开启世界BOSS测试。"],
        "world_boss_force_exists": ["🛠️ 当前已经有世界BOSS了，直接看现在这只。"],
        "world_boss_attack": ["{a} 对【{monster}】造成了 {damage} 点伤害。剩余生命 {hp}/{max_hp}。"],
        "world_boss_attack_kill": ["{a} 对【{monster}】造成了 {damage} 点伤害，完成了最后一击。"],
        "world_boss_team_attack": [
            "🤝 {a} 与 {b} 联手攻击【{monster}】。{a_name} 造成 {a_damage} 点，{b_name} 造成 {b_damage} 点，总计 {total_damage} 点。剩余生命 {hp}/{max_hp}。"
        ],
        "world_boss_team_kill": [
            "🤝 {a} 与 {b} 联手攻击【{monster}】。{a_name} 造成 {a_damage} 点，{b_name} 造成 {b_damage} 点，总计 {total_damage} 点。最后一击由 {last_hit_name} 完成。"
        ],
        "world_boss_team_fail": ["{a} 试着和 {b_name} 一起挑战【{monster}】，没能会合，只能自己先上。"],
        "world_boss_fail_event_hesitate": ["……{b_name} 临时迟疑了一下。"],
        "world_boss_fail_event_late_reply": ["……{b_name} 赶到得慢了半步。"],
        "world_boss_fail_event_out_of_step": ["……两人没能在开战前顺利会合。"],
        "world_boss_team_bonus": ["· 协作加成：本次合击额外提高了 {bonus_total} 点总伤害。"],
        "world_boss_kill": ["🏆 世界BOSS 已经被成功击杀。"],
        "world_boss_last_hit": ["⚔️ {name} 拿下了尾刀。"],
        "world_boss_expired": ["🌫️ 昨天的世界BOSS【{monster}】已经离场。讨伐进度 {progress}% ，本次按 {reward_percent}% 奖励规模发放补偿。"],
        "world_boss_reward": ["· {name}：贡献 {damage}，经验 +{exp}、积分 +{points}{bond_part}{drop_part}{levelup}"],
        "forge_world_boss_ok": ["🔧 世界BOSS装备已强化（已强化 ×{forge}，花费 {cost} 积分）。"],
        # 精英怪遭遇（{a}=真@；{monster}=文本）
        "hunt_encounter_elite": [
            "{a} 这次遭遇的是精英·{monster}。",
            "{a} 刚一出发，就遇上了精英·{monster}。",
        ],
        # 连签 / 今日增益（{streak}{bonus}{buff} 为文本）
        "signin_streak": ["🔥 连签 {streak} 天，额外经验 +{bonus}。"],
        "daily_buff": ["✨ 今天触发了「{buff}」，这一趟的收获提高了。"],
        # 排行榜
        "rank_title": ["🏆 本群冒险排行："],
    },
    "errors": {
        "private_only": "这套冒险玩法只在群里开。",
        "sleeping": "💤 这会儿不接单。等 6 点以后再来。",
        "need_equip": "你今天还没签到领装备。先去「签到」。",
        "equip_broken": "你今天那套装备已经损坏了。可以「购买装备」（100积分）补一套再打，或等明天签到领新的。",
        "forge_no_equip": "你今天还没领装备，先「签到」。",
        "forge_broken": "装备都损坏了，还强化什么。明天再来。",
        "forge_world_boss_no_equip": "你今天还没签到，先领到今天的装备再准备世界BOSS。",
        "forge_world_boss_used": "你这套世界BOSS装备已经用过了，等下次新的世界BOSS吧。",
        "forge_max": "今天这套装备已经强化到头了（上限 {max} 次）。",
        "forge_poor": "积分不够。这次强化要 {cost}，你现在只有 {total}。",
        "rebuy_no_need": "装备还好好的，不用买新的。",
        "rebuy_no_equip": "今天还没签到领装备，没有坏掉的装备需要替换。",
        "rebuy_poor": "积分不够。购买装备需要 {cost}，你现在只有 {total}。",
        "rebuy_limit": "今天已经买过 {max} 套替换装了，明天再来。",
        "bag_empty": "🎒 背包是空的。先去打一趟再说。",
        "use_need_name": "要用什么？比如：使用 经验书。",
        "item_unknown": "没这个道具：{name}。",
        "item_none": "你背包里没有【{name}】。",
        "supply_limit": "这周已经开过 {max} 次冒险补给了，下周再来。",
        "supply_poor": "积分不够。第 {count} 次冒险补给需要 {cost} 积分，你现在只有 {total}。",
        "supply_slot_busy": "【{name}】还在生效，先用完再启用新的常规战备。",
        "supply_guard_busy": "【{name}】还在待命，暂时不能再装备新的护符。",
        "debuff_gift_bot": "小彰不吃这个。去 @ 一个群友。",
        "team_need_target": "组队得@人。比如：组队@某人。",
        "boss_need_target": "组队世界BOSS得@人。比如：组队世界BOSS@某人。",
        "team_self": "自己跟自己组队就算了。换个人 @。",
        "team_bot": "小彰不下场。去 @ 个群友。",
        "team_target_no_signin": "对方今天还未签到领装备，组队失败。",
        "team_target_broken": "对方今天的装备已经损坏了，组队失败。可以让ta「购买装备」补充。",
        "boss_already_attacked": "你这次世界BOSS已经出手过了，等下次新的世界BOSS吧。",
        "boss_none": "当前没有可挑战的世界BOSS。先去正常打一趟看看吧。",
        "rank_empty": "本群还没人开打。先「签到」领装备，再去「打怪」。",
    },
}


class RpgConfigError(ValueError):
    """Raised when an RPG config would break runtime balance or lookup rules."""


def _config_section(config: dict, key: str, expected_type: type):
    value = config[key] if key in config else DEFAULT_RPG_CONFIG[key]
    if not isinstance(value, expected_type):
        raise RpgConfigError(f"{key} 必须是 {expected_type.__name__}")
    return value


def _validate_probability(value, path: str) -> None:
    try:
        chance = float(value)
    except (TypeError, ValueError) as exc:
        raise RpgConfigError(f"{path} 必须是 0 到 1 之间的数字") from exc
    if not 0.0 <= chance <= 1.0:
        raise RpgConfigError(f"{path} 必须在 0 到 1 之间")


def _validate_monsters(config: dict) -> list[dict]:
    monsters = _config_section(config, "monsters", list)
    if not monsters:
        raise RpgConfigError("monsters 不能为空")
    names: set[str] = set()
    for index, monster in enumerate(monsters):
        if not isinstance(monster, dict):
            raise RpgConfigError(f"monsters[{index}] 必须是对象")
        name = str(monster.get("name", "")).strip()
        if not name:
            raise RpgConfigError(f"monsters[{index}].name 不能为空")
        if name in names:
            raise RpgConfigError(f"怪物名重复：{name}")
        names.add(name)
        try:
            power_req = int(monster.get("power_req", 0))
            weight = int(monster.get("weight", 0))
            reward_exp_mult = float(monster.get("reward_exp_mult", 1.0))
        except (TypeError, ValueError) as exc:
            raise RpgConfigError(f"怪物 {name} 的 power_req/weight/reward_exp_mult 格式错误") from exc
        if power_req <= 0:
            raise RpgConfigError(f"怪物 {name} 的 power_req 必须大于 0")
        if weight < 0:
            raise RpgConfigError(f"怪物 {name} 的 weight 不能为负数")
        if not 0.5 <= reward_exp_mult <= 2.0:
            raise RpgConfigError(f"怪物 {name} 的 reward_exp_mult 必须在 0.5 到 2.0 之间")
        if not isinstance(monster.get("drops", []), list):
            raise RpgConfigError(f"怪物 {name} 的 drops 必须是列表")
    return monsters


def _validate_encounter_brackets(config: dict, monsters: list[dict]) -> None:
    combat = _config_section(config, "combat", dict)
    brackets = combat.get("encounter_brackets")
    if not isinstance(brackets, list) or not brackets:
        raise RpgConfigError("combat.encounter_brackets 必须是非空列表")
    monster_names = {str(monster.get("name", "")) for monster in monsters}
    previous_max = 0
    for index, bracket in enumerate(brackets):
        if not isinstance(bracket, dict):
            raise RpgConfigError(f"combat.encounter_brackets[{index}] 必须是对象")
        max_level = bracket.get("max_level")
        if max_level is None:
            if index != len(brackets) - 1:
                raise RpgConfigError("max_level=None 只能出现在最后一个遭遇分段")
        else:
            try:
                max_level = int(max_level)
            except (TypeError, ValueError) as exc:
                raise RpgConfigError(f"第 {index + 1} 个遭遇分段的 max_level 必须是整数或 null") from exc
            if max_level <= previous_max:
                raise RpgConfigError("遭遇分段的 max_level 必须严格递增")
            previous_max = max_level
        weights = bracket.get("weights")
        if isinstance(weights, dict):
            unknown_names = {str(name) for name in weights} - monster_names
            if unknown_names:
                unknown = "、".join(sorted(unknown_names))
                raise RpgConfigError(f"第 {index + 1} 个遭遇分段引用了不存在的怪物：{unknown}")
            raw_weights = list(weights.values())
        elif isinstance(weights, list):
            if len(weights) != len(monsters):
                raise RpgConfigError(
                    f"第 {index + 1} 个遭遇分段有 {len(weights)} 个权重，但怪物池有 {len(monsters)} 只怪物"
                )
            raw_weights = weights
        else:
            raise RpgConfigError(f"第 {index + 1} 个遭遇分段的 weights 必须是名称映射或位置数组")
        try:
            normalized = [int(weight) for weight in raw_weights]
        except (TypeError, ValueError) as exc:
            raise RpgConfigError(f"第 {index + 1} 个遭遇分段的权重必须全部是整数") from exc
        if any(weight < 0 for weight in normalized) or sum(normalized) <= 0:
            raise RpgConfigError(f"第 {index + 1} 个遭遇分段必须至少有一个正权重，且不能出现负权重")
    if brackets[-1].get("max_level") is not None:
        raise RpgConfigError("最后一个遭遇分段必须使用 max_level=null 覆盖后续等级")


def _validate_adventure_supply(config: dict) -> None:
    supply = _config_section(config, "adventure_supply", dict)
    costs = supply.get("weekly_costs")
    if not isinstance(costs, list) or len(costs) != 7:
        raise RpgConfigError("adventure_supply.weekly_costs 必须正好配置 7 次成本")
    try:
        normalized_costs = [int(cost) for cost in costs]
        fixed_exp = int(supply.get("exp", 0))
    except (TypeError, ValueError) as exc:
        raise RpgConfigError("冒险补给成本和固定经验必须是整数") from exc
    if any(cost <= 0 for cost in normalized_costs) or normalized_costs != sorted(normalized_costs):
        raise RpgConfigError("冒险补给成本必须是正整数且不能随次数下降")
    if not 0 <= fixed_exp <= 200:
        raise RpgConfigError("adventure_supply.exp 必须在 0 到 200 之间")

    items = _config_section(config, "items", list)
    item_table = {
        str(item.get("name", "")): item
        for item in items
        if isinstance(item, dict) and str(item.get("name", ""))
    }
    pool = supply.get("pool")
    if not isinstance(pool, list) or not pool:
        raise RpgConfigError("adventure_supply.pool 必须是非空列表")
    names: set[str] = set()
    total_weight = 0
    for index, entry in enumerate(pool):
        if not isinstance(entry, dict):
            raise RpgConfigError(f"adventure_supply.pool[{index}] 必须是对象")
        name = str(entry.get("item", "")).strip()
        if not name or name in names:
            raise RpgConfigError("冒险补给奖池道具名不能为空或重复")
        names.add(name)
        try:
            weight = int(entry.get("weight", 0))
        except (TypeError, ValueError) as exc:
            raise RpgConfigError(f"冒险补给道具 {name} 的 weight 必须是整数") from exc
        if weight <= 0:
            raise RpgConfigError(f"冒险补给道具 {name} 的 weight 必须大于 0")
        total_weight += weight
        item = item_table.get(name)
        if not item:
            raise RpgConfigError(f"冒险补给奖池引用了未定义道具：{name}")
        effect = item.get("effect")
        if not isinstance(effect, dict) or effect.get("type") not in {
            "battle_supply",
            "battle_guard",
            "battle_debuff_gift",
        }:
            raise RpgConfigError(
                f"冒险补给道具 {name} 必须使用 battle_supply、battle_guard 或 battle_debuff_gift 效果"
            )
        try:
            uses = int(effect.get("uses", 0))
            exp_mult = float(effect.get("exp_mult", effect.get("rescue_exp_mult", 1.0)))
            power_mult = float(effect.get("power_mult", 1.0))
            points_mult = float(effect.get("points_mult", 1.0))
            drop_mult = float(effect.get("drop_mult", 1.0))
        except (TypeError, ValueError) as exc:
            raise RpgConfigError(f"冒险补给道具 {name} 的效果数值格式错误") from exc
        if not 1 <= uses <= 10:
            raise RpgConfigError(f"冒险补给道具 {name} 的 uses 必须在 1 到 10 之间")
        if effect.get("type") == "battle_debuff_gift":
            if not 0.0 < exp_mult <= 1.0 or not 0.0 < points_mult <= 1.0 or not 0.0 < drop_mult <= 1.0:
                raise RpgConfigError(f"冒险补给减益道具 {name} 的倍率必须大于 0 且不高于 1")
        elif not 1.0 <= exp_mult <= 3.0 or not 1.0 <= power_mult <= 2.0 or not 1.0 <= drop_mult <= 3.0:
            raise RpgConfigError(f"冒险补给道具 {name} 的倍率超出安全范围")
    if total_weight != 100:
        raise RpgConfigError("adventure_supply.pool 权重总和必须等于 100")


def validate_rpg_config(config: dict) -> None:
    """Validate balance-sensitive structures before startup or hot reload."""
    if not isinstance(config, dict):
        raise RpgConfigError("RPG 配置根节点必须是对象")
    monsters = _validate_monsters(config)
    _validate_encounter_brackets(config, monsters)
    _validate_adventure_supply(config)

    combat = _config_section(config, "combat", dict)
    try:
        factor_min = float(combat.get("factor_min", 0.9))
        factor_max = float(combat.get("factor_max", 1.1))
    except (TypeError, ValueError) as exc:
        raise RpgConfigError("combat.factor_min/factor_max 必须是数字") from exc
    if factor_min <= 0 or factor_max < factor_min:
        raise RpgConfigError("combat.factor_min 必须大于 0，且不能高于 factor_max")

    support = _config_section(config, "support", dict)
    minor = _config_section(config, "minor_encounters", dict)
    world_boss = _config_section(config, "world_boss", dict)
    _validate_probability(support.get("chance", 0.0), "support.chance")
    _validate_probability(minor.get("chance", 0.0), "minor_encounters.chance")
    _validate_probability(minor.get("team_chance", 0.0), "minor_encounters.team_chance")
    _validate_probability(world_boss.get("spawn_chance", 0.0), "world_boss.spawn_chance")
    special_drop = world_boss.get("special_drop", {})
    if not isinstance(special_drop, dict):
        raise RpgConfigError("world_boss.special_drop 必须是对象")
    _validate_probability(special_drop.get("chance", 0.0), "world_boss.special_drop.chance")

    boss_names = world_boss.get("boss_names", [])
    if not isinstance(boss_names, list) or not boss_names or any(not str(name).strip() for name in boss_names):
        raise RpgConfigError("world_boss.boss_names 必须是非空名称列表")
    if len({str(name) for name in boss_names}) != len(boss_names):
        raise RpgConfigError("world_boss.boss_names 不能包含重复名称")

    titles = _config_section(config, "titles", list)
    try:
        title_levels = [int(title.get("min_level", 0)) for title in titles if isinstance(title, dict)]
    except (TypeError, ValueError) as exc:
        raise RpgConfigError("titles[*].min_level 必须是整数") from exc
    if len(title_levels) != len(titles) or not title_levels or title_levels != sorted(set(title_levels)):
        raise RpgConfigError("titles 必须按唯一的 min_level 递增排列")


def _load_config() -> dict:
    """加载并校验 RPG 配置；无文件时使用默认配置。"""
    loaded = load_json_file(CONFIG_FILE, None)
    candidate = loaded if isinstance(loaded, dict) else copy.deepcopy(DEFAULT_RPG_CONFIG)
    validate_rpg_config(candidate)
    return candidate


try:
    RPG_CONFIG: dict = _load_config()
except RpgConfigError as exc:
    logger.error(f"❌ RPG 配置校验失败，启动时改用默认配置：{exc}")
    RPG_CONFIG = copy.deepcopy(DEFAULT_RPG_CONFIG)


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
    """校验通过后原地热重载；失败时保留当前运行配置。"""
    try:
        candidate = _load_config()
    except RpgConfigError as exc:
        logger.error(f"❌ RPG 配置热重载被拒绝，继续使用当前配置：{exc}")
        raise
    RPG_CONFIG.clear()
    RPG_CONFIG.update(candidate)
    logger.info("🔄 RPG 配置已热重载")
