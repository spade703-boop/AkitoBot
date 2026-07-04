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
        # ---- 低等级先少撞高难怪；默认 6 档分段，若缺失/非法则回退到 monsters[*].weight ----
        "encounter_brackets": [
            {"max_level": 2, "weights": [55, 45, 0, 0, 0, 0]},
            {"max_level": 4, "weights": [55, 45, 0, 0, 0, 0]},
            {"max_level": 6, "weights": [40, 35, 25, 0, 0, 0]},
            {"max_level": 9, "weights": [30, 28, 22, 20, 0, 0]},
            {"max_level": 13, "weights": [25, 22, 20, 18, 15, 0]},
            {"max_level": 16, "weights": [22, 20, 20, 18, 20, 0]},
            {"max_level": None, "weights": [25, 20, 20, 15, 10, 10]},
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
        "spawn_chance": 0.01,
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
        {"name": "哥布林", "power_req": 25, "weight": 25,
         "drops": [{"item": "经验书", "chance": 0.12}, {"item": "双倍经验卡", "chance": 0.05},
                   {"item": "彰冬无料券", "chance": 0.08}, {"item": "彰冬谷子券", "chance": 0.05}]},
        {"name": "座狼",   "power_req": 40, "weight": 20,
         "drops": [{"item": "双倍经验卡", "chance": 0.08},
                   {"item": "彰冬谷子券", "chance": 0.06}, {"item": "彰冬豆豆眼券", "chance": 0.04}]},
        {"name": "食人魔", "power_req": 55, "weight": 15,
         "drops": [{"item": "双倍经验卡", "chance": 0.10},
                   {"item": "彰冬豆豆眼券", "chance": 0.05}, {"item": "彰冬立牌券", "chance": 0.03}]},
        {"name": "石像鬼", "power_req": 75, "weight": 10,
         "drops": [{"item": "双倍经验卡", "chance": 0.12},
                   {"item": "彰冬豆豆眼券", "chance": 0.06}, {"item": "彰冬立牌券", "chance": 0.04}]},
        {"name": "龙",     "power_req": 95, "weight": 5,
         "drops": [{"item": "双倍经验卡", "chance": 0.15},
                   {"item": "彰冬立牌券", "chance": 0.06}]},
    ],
    # ---- 道具（消耗品，经验向）：effect.type = exp_buff / exp_grant ----
    "items": [
        {"name": "双倍经验卡", "desc": "下次打怪经验翻倍", "effect": {"type": "exp_buff", "uses": 1, "mult": 2}},
        {"name": "经验书", "desc": "立即获得 80 经验", "effect": {"type": "exp_grant", "amount": 80}},
        {"name": "彰冬无料券", "desc": "赠送「彰冬无料」，羁绊+12", "effect": {"type": "gift", "gift_name": "彰冬无料"}},
        {"name": "彰冬谷子券", "desc": "赠送「彰冬谷子」，羁绊+28", "effect": {"type": "gift", "gift_name": "彰冬谷子"}},
        {"name": "彰冬豆豆眼券", "desc": "赠送「彰冬豆豆眼」，羁绊+60", "effect": {"type": "gift", "gift_name": "彰冬豆豆眼"}},
        {"name": "彰冬立牌券", "desc": "赠送「彰冬亚克力立牌」，羁绊+85", "effect": {"type": "gift", "gift_name": "彰冬亚克力立牌"}},
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
        "forge_ok": ["🔨 强化好了。今日装备更稳了（已强化 ×{forge}，花费 {cost} 积分）。"],
        "rebuy_ok": ["🛡️ 替换装备已就位，花了 {cost} 积分。不过这套是临时凑的，打怪经验和积分都会减半。"],
        "use_exp_buff": ["📖 【{name}】用了。下次打怪经验 ×{mult}。"],
        "use_exp_grant": ["📖 【{name}】用了。经验 +{amount}。"],
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
