"""送礼系统：彰冬同人圈主题的群友互送小游戏。

玩法闭环（完全自包含，不依赖其他模块）：
- `签到`：每天 1 次领取积分（赚取入口）。
- `送礼 @对方`：每天 1 次，系统从「你当前积分买得起的礼物」里随机送一份给对方，按权重抽随机事件
  （普通/暴击/回礼/失败/意外），累积两个群友之间的「亲密度（同好羁绊）」。
  顶档「自己产的彰冬饭」一旦抽中，必定触发「惊喜升级」固定结算。
- `偷 @对方`：每天 2 次，小概率顺走对方少量积分（强保护 + 偷必掉羁绊，偷越亲近掉越多）。
- `我的积分` / `礼物列表` / `亲密度` / `群羁绊排行` 查询；`重置送礼`（超管）清空本群数据。

数据与套路对照 features/random_keyword.py：按群存储、每日按日期重置、原子读写、文件优先+缺省兜底配置。
"""

from __future__ import annotations

import asyncio
import copy
import os
import random
import time

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from ..core import (
    ALLOWED_CHAT_GROUPS,
    SUPERUSER_QQ,
    is_sleeping,
    load_json_file,
)
from ..core.game_store import (
    LOCK,
    SCHEMA_VERSION,  # noqa: F401  仅供 tests/test_gift.py 引用 gift.SCHEMA_VERSION
    _add_intimacy,
    _add_points,
    _display_name,
    _first_at_qq,
    _get_group,
    _get_intimacy,
    _load_data,
    _new_group,
    _normalize_data,  # noqa: F401  仅供 tests/test_gift.py 引用 gift._normalize_data
    _pair_key,  # noqa: F401  仅供 tests/test_gift.py 引用 gift._pair_key
    _render_with_ats,
    _save_data,
    _today_str,
    _weighted_choice,
    get_user,
    resolve_group_id,
    run_signin_hooks,
)
from .bond_pages import build_bond_page_data, build_bond_rank_page_data, build_my_bonds_page_data
from .bond_render import render_bond_page

GIFT_USE_HTML_RENDER = os.environ.get("GIFT_USE_HTML_RENDER", "1").strip() not in {"0", "false", "False"}

CONFIG_FILE = "gift_config.json"

# ==================== 默认配置（可被 data/content/gift_config.json 覆盖） ====================

DEFAULT_GIFT_CONFIG: dict = {
    # 礼物按「心意/稀有度」递增：买来的 → 自己产的。消耗积分与基础亲密度同步递增。
    "gifts": [
        {"name": "彰冬无料", "cost": 50, "intimacy": 12},
        {"name": "彰冬谷子", "cost": 100, "intimacy": 28},
        {"name": "彰冬豆豆眼", "cost": 200, "intimacy": 60},
        {"name": "彰冬亚克力立牌", "cost": 270, "intimacy": 85},
        {"name": "彰冬同人本", "cost": 350, "intimacy": 115},
        {"name": "彰冬画集", "cost": 450, "intimacy": 155},
        {"name": "彰冬约稿点图", "cost": 525, "intimacy": 200},
        {"name": "彰冬手办", "cost": 648, "intimacy": 255},
        # special=true 必定惊喜升级（不暴击不失败，固定取自身 intimacy）；copy 指定专属文案
        {"name": "自己产的彰冬饭", "cost": 819, "intimacy": 520, "special": True, "copy": "special_meal"},
        {"name": "彰冬婚礼邀请函", "cost": 1112, "intimacy": 1314, "special": True, "copy": "special_wedding"},
    ],
    # 回礼回赠物：键控加权表（仿 mishaps）。name 回赠物 / bonus 在 base 之上额外加的羁绊 /
    #   refund_ratio 按所送礼物 cost 退还比例 / weight 稀有度。可增删调、热重载。
    "return_gifts": {
        "guzi":     {"name": "彰冬谷子",         "bonus": 6,  "refund_ratio": 0.0,  "weight": 6},
        "card":     {"name": "彰冬手绘小卡",     "bonus": 14, "refund_ratio": 0.0,  "weight": 4},
        "doujin":   {"name": "彰冬同人本",       "bonus": 24, "refund_ratio": 0.15, "weight": 3},
        "rareguzi": {"name": "彰冬稀有谷子",     "bonus": 32, "refund_ratio": 0.0,  "weight": 2},
        "jouhan":   {"name": "彰冬场贩限定本子", "bonus": 52, "refund_ratio": 0.25, "weight": 1},  # 稀有大奖
    },
    "sign_in": {"min": 50, "max": 100},
    "sign_delay_sec": {"min": 3, "max": 5},  # 签到回复随机延迟，错开另一个签到 bot
    "crit_multiplier": 2,
    "fail_refund_ratio": 0.3,  # 失败时按礼物 cost 比例退还的安慰积分（贵礼自动退得多；仍 0 羁绊）
    # 主事件权重（意外 5→10，让花样有机会出现）
    "event_weights": {"normal": 55, "crit": 16, "return": 12, "fail": 7, "mishap": 10},
    # 意外子事件表：羁绊取 max(intimacy 保底, ratio×base 缩放)——便宜礼吃保底、贵礼按档放大；
    #   refund_ratio 按 cost 退还比例；weight 抽取权重（可增删调，热重载）
    "mishaps": {
        "damaged":     {"intimacy": 8,  "ratio": 0.3,  "refund_ratio": 0.5, "weight": 3},  # 快递翻车
        "freebie":     {"intimacy": 28, "ratio": 1.0,  "refund_ratio": 0.0, "weight": 2},  # 商家加赠
        "rare":        {"intimacy": 24, "ratio": 0.9,  "refund_ratio": 0.0, "weight": 2},  # 买到稀有
        "handwritten": {"intimacy": 20, "ratio": 0.8,  "refund_ratio": 0.0, "weight": 2},  # 附了手写卡
        "praised":     {"intimacy": 22, "ratio": 0.85, "refund_ratio": 0.0, "weight": 2},  # 被同好夸甜
        "overboard":   {"intimacy": 30, "ratio": 1.1,  "refund_ratio": 0.0, "weight": 1},  # 一时上头加码
        "delayed":     {"intimacy": 12, "ratio": 0.5,  "refund_ratio": 0.0, "weight": 2},  # 慢递迟到
        "dupe":        {"intimacy": 6,  "ratio": 0.25, "refund_ratio": 0.3, "weight": 2},  # 撞款了
        "lost":        {"intimacy": 0,  "ratio": 0.0,  "refund_ratio": 1.0, "weight": 1},  # 寄丢了
    },
    # 羁绊等级：累计羁绊值 → 称号，门槛按礼物羁绊值校准（纯展示层，可热重载）
    "bond_levels": [
        {"min": -1000, "name": "宿敌"},
        {"min": -300, "name": "结了梁子"},
        {"min": -50, "name": "有过节"},
        {"min": 0, "name": "Hot Dogs"},
        {"min": 100, "name": "大麦克风"},
        {"min": 400, "name": "能信赖的搭档"},
        {"min": 1000, "name": "云与柳的大头贴"},
        {"min": 2500, "name": "想与你并肩而行"},
        {"min": 6000, "name": "从今往后直到永远"},
    ],
    # 偷积分：对抗玩法（轻量·强保护·掉羁绊），每项可配可热重载
    "steal": {
        "daily_limit": 2,          # 每人每天偷几次
        "victim_daily_limit": 3,   # 每人每天最多被偷几次（护受害者）
        "min_target_points": 50,   # 对方低于此分免疫（不踩穷人）
        "ratio": 0.1,              # 得手时拿走对方余额比例
        "cap": 40,                 # 得手封顶
        "protect_minutes": 60,     # 签到后保护期（分钟）
        "caught_penalty": 15,      # 被抓时倒赔对方
        "reversal_amount": 10,     # 反被顺走的额度
        "bond_flat": 20,           # 偷一次的羁绊基础代价（羁绊>0 时按比例）
        "bond_ratio": 0.1,         # 额外按当前羁绊比例扣（偷越亲近掉越多）
        "bond_neg_min": 10,        # 羁绊≤0 时改随机扣：下限
        "bond_neg_max": 30,        # 羁绊≤0 时改随机扣：上限
        "bond_floor": -1000,       # 羁绊下限（结怨封底）
        "weights": {"success": 5, "caught": 3, "whiff": 2, "reversal": 1},
    },
    # 播报文案（平和同好口吻，每类多条随机选用）。占位符：
    #   {a}{b} → 真 @；{gift}{return_gift}{amount}{total}{cost}{refund}{name} → 文本
    "copy": {
        "sign_in": [
            "{a} 签到完成，今天领到 {amount} 积分，攒着慢慢送礼吧（当前 {total}）。",
            "{a} 来打卡了，+{amount} 积分到账（当前 {total}）。",
            "{a} 今天的签到积分 +{amount}，离心仪的礼物又近了点（当前 {total}）。",
        ],
        "normal": [
            "{a} 给 {b} 投喂了一份【{gift}】，同好羁绊 +{amount}。",
            "{a} 送了 {b} 一份【{gift}】，两个人的羁绊 +{amount}。",
            "{a} 把【{gift}】送给了 {b}，羁绊默默 +{amount}。",
        ],
        "crit": [
            "{a} 送的【{gift}】正好戳中 {b}，有点上头，羁绊翻倍 +{amount}。",
            "{a} 这份【{gift}】送到 {b} 心坎上了，羁绊翻倍 +{amount}。",
            "{a} 送的【{gift}】很对 {b} 的胃口，羁绊直接翻倍 +{amount}。",
        ],
        "return": [
            "{a} 送了【{gift}】，{b} 也回赠了一份【{return_gift}】，礼尚往来，羁绊 +{amount}。",
            "{a} 给 {b} 送了【{gift}】，{b} 顺手回了份【{return_gift}】，羁绊 +{amount}。",
        ],
        # 回礼分档专属文案（{return_gift} 回赠物 / {refund} 退还积分，仅退分档 doujin·jouhan 写 refund）
        "return_guzi": [
            "{a} 送了【{gift}】，{b} 也回赠了一份【{return_gift}】，礼尚往来，羁绊 +{amount}。",
            "{a} 给 {b} 送了【{gift}】，{b} 顺手回了份【{return_gift}】，羁绊 +{amount}。",
        ],
        "return_card": [
            "{a} 送了【{gift}】，{b} 回赠了一张【{return_gift}】，还在角落悄悄画了只小彰冬，羁绊 +{amount}。",
            "{a} 的【{gift}】送到，{b} 翻出珍藏的【{return_gift}】回赠，心意加倍，羁绊 +{amount}。",
        ],
        "return_doujin": [
            "{a} 送了【{gift}】，{b} 回赠了一本【{return_gift}】，还退了 {refund} 积分当回礼茶水费，羁绊 +{amount}。",
            "{a} 给 {b} 送【{gift}】，{b} 大方回赠一本【{return_gift}】，顺带塞回 {refund} 积分，羁绊 +{amount}。",
        ],
        "return_rareguzi": [
            "{a} 送了【{gift}】，{b} 翻出压箱底的【{return_gift}】回赠，可遇不可求，羁绊 +{amount}。",
            "{a} 的【{gift}】刚到，{b} 就回赠了一份绝版【{return_gift}】，{a} 乐开了花，羁绊 +{amount}。",
        ],
        "return_jouhan": [
            "{a} 送了【{gift}】，{b} 竟回赠珍藏的【{return_gift}】！还附 {refund} 积分大红包，全场同好齐刷「这什么神仙礼尚往来」，羁绊 +{amount}。",
            "{a} 给 {b} 送【{gift}】，{b} 大手一挥回赠一本【{return_gift}】外加 {refund} 积分，把 {a} 砸得当场愣住，羁绊 +{amount}。",
        ],
        "fail": [
            "{a} 送的【{gift}】没太对上 {b} 的眼缘，{b} 不好意思收下，退回 {refund} 积分，羁绊没变化。",
            "{a} 送出【{gift}】，{b} 反应平平、心意没太传到，退还 {refund} 积分，羁绊原地踏步。",
            "{a} 送的【{gift}】好像没戳中 {b}，{b} 客气地退回 {refund} 积分，这次羁绊没怎么动。",
        ],
        "mishap_damaged": [
            "{a} 寄的【{gift}】路上有点压坏了，两个人一起心疼了一下，反而更亲近，羁绊各 +{amount}，返还 {refund} 积分。",
            "{a} 的【{gift}】运输途中磕了一下，{b} 陪着一起惋惜，羁绊各 +{amount}，退还 {refund} 积分。",
        ],
        "mishap_freebie": [
            "{a} 送 {b} 的【{gift}】，卖家居然多塞了份赠品小卡，意外加料，羁绊 +{amount}。",
            "{a} 给 {b} 的【{gift}】里附了点小周边，{b} 拆开惊喜了一下，羁绊 +{amount}。",
        ],
        "mishap_rare": [
            "{a} 送 {b} 的这份【{gift}】正好是早绝版的稀有款，可遇不可求，羁绊 +{amount}。",
            "{a} 给 {b} 淘来的【{gift}】是限定稀有版，运气爆棚，羁绊 +{amount}。",
        ],
        "mishap_handwritten": [
            "{a} 随【{gift}】夹了张手写小卡，{b} 看完心头一暖，羁绊 +{amount}。",
            "{a} 在【{gift}】里塞了句手写留言，{b} 反复看了好几遍，羁绊 +{amount}。",
        ],
        "mishap_praised": [
            "{a} 送 {b} 的【{gift}】被群里同好刷屏夸「这对好甜」，公开撒糖加成，羁绊 +{amount}。",
            "{a} 这份送给 {b} 的【{gift}】当众撒了把糖，同好们齐刷「磕到了」，羁绊 +{amount}。",
        ],
        "mishap_overboard": [
            "{a} 一时上头，给【{gift}】又默默加了码，{b} 哭笑不得地收下，羁绊 +{amount}。",
            "{a} 送【{gift}】送上了头，硬是多添了点，{b} 拗不过只好收下，羁绊 +{amount}。",
        ],
        "mishap_delayed": [
            "{a} 送 {b} 的【{gift}】物流慢了半拍，但心意照样送到，羁绊 +{amount}。",
            "{a} 的【{gift}】在路上磨蹭了几天才到，{b} 还是乐呵呵收下，羁绊 +{amount}。",
        ],
        "mishap_dupe": [
            "{a} 送的【{gift}】，{b} 居然早就有同款了，两个人笑作一团，羁绊 +{amount}，返还 {refund} 积分。",
            "{a} 送来【{gift}】，{b} 翻出自己那份同款，俩人乐了半天，羁绊 +{amount}，退还 {refund} 积分。",
        ],
        "mishap_lost": [
            "{a} 寄给 {b} 的【{gift}】半路寄丢了，心意还在，积分全额退回 {refund}。",
            "{a} 的【{gift}】在物流里弄丢了，{b} 说心意领了就好，积分如数退还 {refund}。",
        ],
        # 保证礼专属文案（必定惊喜升级）
        "special_meal": [
            "{a} 送的彰冬饭非常合 {b} 的胃口，羁绊 +{amount}。",
            "{a} 送的彰冬饭正好是 {b} 最喜欢的那个派生，羁绊 +{amount}。",
        ],
        "special_wedding": [
            "{a} 郑重地把【彰冬婚礼邀请函】递到 {b} 手里，邀请 {b} 去参加这对橙蓝给子的婚礼，羁绊 +{amount}（一生一世）。",
            "{a} 向 {b} 递出【彰冬婚礼邀请函】，要把这段同好情谊焊成一辈子，羁绊 +{amount}。",
        ],
        # 偷：四种结果（{amount} 积分、{bond} 掉的羁绊）
        "steal_success": [
            "{a} 瞅准空子，顺走了 {b} {amount} 积分，溜了溜了～（羁绊 -{bond}）",
            "{a} 趁 {b} 不注意摸走了 {amount} 积分，得手！（羁绊 -{bond}）",
        ],
        "steal_caught": [
            "{a} 刚把手伸向 {b} 的钱包就被逮个正着，赔了 {amount} 积分赔罪（羁绊 -{bond}）",
            "{a} 偷 {b} 失了风，当场社死，倒贴 {amount} 积分息事宁人（羁绊 -{bond}）",
        ],
        "steal_whiff": [
            "{a} 摸了半天 {b} 的口袋，扑了个空，啥也没捞着（羁绊 -{bond}）",
            "{a} 想顺 {b} 一笔，结果对方兜比脸还干净，无功而返（羁绊 -{bond}）",
        ],
        "steal_reversal": [
            "{a} 偷鸡不成蚀把米，反被 {b} 顺走了 {amount} 积分，笑死（羁绊 -{bond}）",
            "{a} 想偷 {b}，反倒被将一军，倒搭 {amount} 积分（羁绊 -{bond}）",
        ],
    },
    # 边界/错误提示（纯文本，可含 {cost}{total}{name}）
    "errors": {
        "private_only": "送礼系统在群里才能玩哦。",
        "sleeping": "💤 这会儿小彰睡着了，等 6 点天亮以后再来吧……",
        "already_gifted": "今天的礼已经送过了，明天再来吧。",
        "need_target": "送礼要 @一位群友 哦，系统会随机送出一份礼物。比如：送礼 @某人。",
        "self_target": "给自己送礼就没什么意思啦，去 @一个群友吧。",
        "bot_target": "小彰拒绝了你的礼物。",
        "insufficient": "积分还不太够，最便宜的【{name}】也要 {cost}，你现在有 {total}，先去签到攒一攒吧。",
        "steal_need_target": "偷要 @一个目标 才行，比如：偷 @某人。",
        "steal_self": "偷自己？图啥呀。",
        "steal_bot": "偷到小彰头上来了，胆子不小——不给。",
        "steal_limit": "你今天的手气用完了，明天再来当大盗吧。",
        "steal_protected": "现在偷不了 ta（刚签到受保护，或今天被偷太多次了），换个目标吧。",
        "steal_too_poor": "对方兜里比脸还干净（不足 {min} 积分），没什么好偷的。",
    },
}

def _load_config() -> dict:
    """加载送礼配置；无文件 / 解析失败时回落到默认配置的深拷贝。

    关键：必须深拷贝，否则无文件时 GIFT_CONFIG 与 DEFAULT_GIFT_CONFIG 会是同一对象，
    reload 时的 clear() 会连默认配置一起清空。
    """
    loaded = load_json_file(CONFIG_FILE, None)
    return loaded if isinstance(loaded, dict) else copy.deepcopy(DEFAULT_GIFT_CONFIG)


GIFT_CONFIG: dict = _load_config()


def _cfg(key: str, default=None):
    """读配置项，缺失时回落到默认配置。"""
    if key in GIFT_CONFIG:
        return GIFT_CONFIG[key]
    return DEFAULT_GIFT_CONFIG.get(key, default)


def _copy(key: str) -> list[str]:
    table = _cfg("copy", {})
    if isinstance(table, dict) and table.get(key):
        return table[key]
    return DEFAULT_GIFT_CONFIG["copy"].get(key, [""])


def _error(key: str, **fmt) -> str:
    table = _cfg("errors", {})
    template = table.get(key) if isinstance(table, dict) else None
    if not template:
        template = DEFAULT_GIFT_CONFIG["errors"].get(key, "")
    try:
        return template.format(**fmt)
    except (KeyError, IndexError):
        return template


def reload_gift_config() -> None:
    """热重载礼物配置（原地 clear+update，保持已持有引用不失效）。"""
    GIFT_CONFIG.clear()
    GIFT_CONFIG.update(_load_config())
    logger.info("🔄 送礼配置已热重载")


# ==================== 礼物档位 ====================

def _gift_list() -> list[dict]:
    gifts = _cfg("gifts", [])
    return gifts if isinstance(gifts, list) else []


def _affordable_gifts(points: int) -> list[dict]:
    """返回当前积分买得起的礼物（cost ≤ points）。"""
    return [g for g in _gift_list() if int(g.get("cost", 0)) <= int(points)]


def _pick_gift(points: int, rng=random) -> dict | None:
    """从买得起的礼物里加权随机抽一份：越贵权重越大（按档位顺序 1..k）；都买不起返回 None。

    依赖 gifts 配置按 cost 升序排列（买得起的恰为前 k 档，权重即其档位序号）。
    """
    pool = _affordable_gifts(points)
    if not pool:
        return None
    weights = list(range(1, len(pool) + 1))
    return rng.choices(pool, weights=weights, k=1)[0]


def _cheapest_gift() -> dict | None:
    gifts = _gift_list()
    return min(gifts, key=lambda g: int(g.get("cost", 0))) if gifts else None


# ==================== 数据持久化 / 亲密度（复用 core.game_store） ====================
# 存储原语（读写/锁/积分/亲密度/每日键/加权随机/@渲染/群校验）已抽到 core.game_store，
# 本模块直接复用，并共用同一把 LOCK —— 使送礼/签到/偷与 rpg 打野等串行写同一份
# gift_data.json，互不踩踏。下方仅保留送礼专属的用户字段封装。

_GIFT_LOCK = LOCK


def _get_user(group: dict, user_id, display_name: str = "") -> dict:
    """在通用用户记录（points/display_name）上补齐送礼专属字段。"""
    user = get_user(group, user_id, display_name)
    user.setdefault("last_sign_in", "")     # 上次签到日期
    user.setdefault("last_gift", "")        # 上次送礼日期
    user.setdefault("steal_date", "")       # 贼：上次偷的日期
    user.setdefault("steal_used", 0)        # 贼：今日已偷次数
    user.setdefault("robbed_date", "")      # 受害者：上次被偷日期
    user.setdefault("robbed_count", 0)      # 受害者：今日被偷次数
    user.setdefault("protect_until", 0)     # 受害者：签到保护期截止 epoch
    return user


# ==================== 羁绊等级 / 送礼次数 ====================

def _count_key(frm, to) -> str:
    """有向送礼次数 key：送礼方 → 收礼方。"""
    return f"{frm}>{to}"


def _bump_count(group: dict, frm, to) -> int:
    counts = group.setdefault("counts", {})
    key = _count_key(frm, to)
    counts[key] = int(counts.get(key, 0)) + 1
    return counts[key]


def _get_count(group: dict, frm, to) -> int:
    return int(group.get("counts", {}).get(_count_key(frm, to), 0))


def _bond_levels() -> list[dict]:
    levels = _cfg("bond_levels", [])
    return levels if isinstance(levels, list) and levels else DEFAULT_GIFT_CONFIG["bond_levels"]


def _bond_level(value: int) -> dict:
    """累计羁绊值 → 当前等级信息。

    返回 {idx, level, name, cur_min, next_name, next_min, to_next}；满级时 next_* 为 None。
    level 以 min==0 档为 Lv1 锚点（负档 level ≤ 0，展示时不挂 Lv）。
    """
    levels = _bond_levels()
    value = int(value)
    idx = 0
    for i, lv in enumerate(levels):
        if value >= int(lv.get("min", 0)):
            idx = i
        else:
            break
    zero_i = next((i for i, lv in enumerate(levels) if int(lv.get("min", 0)) == 0), 0)
    cur = levels[idx]
    nxt = levels[idx + 1] if idx + 1 < len(levels) else None
    return {
        "idx": idx,
        "level": idx - zero_i + 1,
        "name": str(cur.get("name", "")),
        "cur_min": int(cur.get("min", 0)),
        "next_name": str(nxt.get("name", "")) if nxt else None,
        "next_min": int(nxt.get("min", 0)) if nxt else None,
        "to_next": max(0, int(nxt.get("min", 0)) - value) if nxt else 0,
    }


def _bond_card(group: dict, me: str, other: str):
    """组装「我和 ta」的羁绊文字卡片（含真 @、等级、进度、分方向次数）。"""
    value = _get_intimacy(group, me, other)
    lv = _bond_level(value)
    sent = _get_count(group, me, other)
    recv = _get_count(group, other, me)

    tier = f"{lv['name']}（Lv{lv['level']}）" if lv["level"] >= 1 else lv["name"]
    lines = [f"· 等级：{tier}"]
    if lv["next_name"]:
        lines.append(f"· 羁绊值 {value}，距「{lv['next_name']}」还差 {lv['to_next']}")
    else:
        lines.append(f"· 羁绊值 {value}，已达顶级「{lv['name']}」(Lv{lv['level']})")
    if sent or recv:
        lines.append(f"· 你送出 {sent} 次，ta 回送 {recv} 次（共 {sent + recv} 次往来）")
    else:
        lines.append("· 你们还没互送过礼，快去 送礼 @ta 吧～")

    head = MessageSegment.at(me) + " 和 " + MessageSegment.at(other) + " 的同好羁绊"
    return head + "\n" + "\n".join(lines)


# ==================== 随机事件 ====================

def _roll_main_event(rng=random) -> str:
    return _weighted_choice(_cfg("event_weights", {}), rng)


def _mishaps() -> dict:
    m = _cfg("mishaps", {})
    return m if isinstance(m, dict) and m else DEFAULT_GIFT_CONFIG["mishaps"]


def _mishap_spec(key: str) -> dict:
    return _mishaps().get(key) or DEFAULT_GIFT_CONFIG["mishaps"].get(key, {})


def _roll_mishap(rng=random) -> str:
    weights = {k: int(v.get("weight", 0)) for k, v in _mishaps().items()}
    return _weighted_choice(weights, rng)


def _return_gifts() -> dict:
    r = _cfg("return_gifts", {})
    return r if isinstance(r, dict) and r else DEFAULT_GIFT_CONFIG["return_gifts"]


def _return_spec(key: str) -> dict:
    return _return_gifts().get(key) or DEFAULT_GIFT_CONFIG["return_gifts"].get(key, {})


def _roll_return_gift(rng=random) -> str:
    weights = {k: int(v.get("weight", 0)) for k, v in _return_gifts().items()}
    return _weighted_choice(weights, rng)


def _is_special_gift(gift: dict) -> bool:
    return bool(gift.get("special"))


def _settle(group: dict, sender_id: str, target_id: str, gift: dict,
            main_event: str, mishap: str | None, return_key: str | None = None) -> dict:
    """按已抽定的事件结算（直接改 group：亲密度/积分），返回播报所需数据。

    本函数不含随机、不做 IO，便于单测。调用方需先扣除礼物消耗积分。
    """
    base = int(gift.get("intimacy", 0))
    cost = int(gift.get("cost", 0))
    out: dict = {
        "event": main_event,
        "mishap": mishap if main_event == "mishap" else None,
        "gift": gift.get("name", ""),
        "amount": base,
        "refund": 0,
        "return_gift": None,
        "return_key": None,
    }

    if main_event == "normal":
        _add_intimacy(group, sender_id, target_id, base)
    elif main_event == "crit":
        out["amount"] = base * int(_cfg("crit_multiplier", 2))
        _add_intimacy(group, sender_id, target_id, out["amount"])
    elif main_event == "return":
        # 回礼：对方回赠一份（加权抽定的）回赠物，礼尚往来——在 base 之上额外加成，稀有档还退部分积分
        if return_key is None:
            return_key = next(iter(_return_gifts()), "")  # 防御：未 roll 时落到首档
        spec = _return_spec(return_key)
        out["return_key"] = return_key
        out["return_gift"] = str(spec.get("name", ""))
        out["amount"] = base + int(spec.get("bonus", 0))
        out["refund"] = int(cost * float(spec.get("refund_ratio", 0)))
        _add_intimacy(group, sender_id, target_id, out["amount"])
        if out["refund"]:
            _add_points(group, sender_id, out["refund"])
    elif main_event == "fail":
        out["amount"] = 0  # 核心惩罚不变：没拉近关系
        out["refund"] = int(cost * float(_cfg("fail_refund_ratio", 0)))  # 安慰退分（按 cost 比例，贵礼退得多）
        if out["refund"]:
            _add_points(group, sender_id, out["refund"])
    elif main_event == "special":
        # 保证礼：必定惊喜升级，固定取礼物自身 intimacy；copy 走礼物专属文案
        out["amount"] = int(gift.get("intimacy", base))
        out["copy"] = gift.get("copy", "special")
        _add_intimacy(group, sender_id, target_id, out["amount"])
    elif main_event == "mishap":
        # 意外：按 mishaps 配置表结算（羁绊加成 + 按比例返还积分）
        spec = _mishap_spec(mishap)
        # 羁绊取「保底」与「ratio×base 缩放」的较大者：便宜礼吃保底、贵礼按档放大
        out["amount"] = max(int(spec.get("intimacy", 0)), int(float(spec.get("ratio", 0)) * base))
        if out["amount"]:
            _add_intimacy(group, sender_id, target_id, out["amount"])
        out["refund"] = int(cost * float(spec.get("refund_ratio", 0)))
        if out["refund"]:
            _add_points(group, sender_id, out["refund"])

    return out


# ==================== 偷积分 ====================

def _steal_cfg() -> dict:
    c = _cfg("steal", {})
    return c if isinstance(c, dict) and c else DEFAULT_GIFT_CONFIG["steal"]


def _steal_outcome(rng=random) -> str:
    return _weighted_choice(_steal_cfg().get("weights", {}), rng)


def _settle_steal(group: dict, thief_id: str, victim_id: str, outcome: str, rng=random) -> dict:
    """按已抽定的偷窃结果结算（改 group：积分/羁绊），返回播报数据。不做 IO，便于单测。

    每次偷都付羁绊代价：羁绊>0 按比例扣（去封底、可跨负）；羁绊≤0 改随机区间扣；统一封底 bond_floor。
    """
    cfg = _steal_cfg()
    thief = _get_user(group, thief_id)
    victim = _get_user(group, victim_id)
    bond = _get_intimacy(group, thief_id, victim_id)
    if bond > 0:
        drop = int(int(cfg.get("bond_flat", 20)) + float(cfg.get("bond_ratio", 0.1)) * bond)
    else:
        drop = rng.randint(int(cfg.get("bond_neg_min", 10)), int(cfg.get("bond_neg_max", 30)))
    new_bond = max(int(cfg.get("bond_floor", -1000)), bond - drop)
    out = {"outcome": outcome, "amount": 0, "bond": bond - new_bond}

    if outcome == "success":
        vp = int(victim.get("points", 0))
        amt = min(int(vp * float(cfg.get("ratio", 0.1))), int(cfg.get("cap", 40)), vp)
        victim["points"] = vp - amt
        thief["points"] = int(thief.get("points", 0)) + amt
        out["amount"] = amt
    elif outcome == "caught":
        pen = min(int(cfg.get("caught_penalty", 15)), int(thief.get("points", 0)))
        thief["points"] = int(thief.get("points", 0)) - pen
        victim["points"] = int(victim.get("points", 0)) + pen
        out["amount"] = pen
    elif outcome == "reversal":
        amt = min(int(cfg.get("reversal_amount", 10)), int(thief.get("points", 0)))
        thief["points"] = int(thief.get("points", 0)) - amt
        victim["points"] = int(victim.get("points", 0)) + amt
        out["amount"] = amt
    # whiff：积分不动

    _add_intimacy(group, thief_id, victim_id, new_bond - bond)
    return out


# ==================== 消息组装 ====================

def _outcome_copy_key(out: dict) -> str:
    if out["event"] == "mishap":
        return f"mishap_{out['mishap']}"
    if out["event"] == "special":
        return out.get("copy") or "special"
    if out["event"] == "return":
        key = out.get("return_key")
        return f"return_{key}" if key else "return"
    return out["event"]  # normal / crit / fail


def _build_broadcast(out: dict, sender_id: str, target_id: str, rng=random):
    template = rng.choice(_copy(_outcome_copy_key(out)))
    ctx = {
        "a": sender_id,
        "b": target_id,
        "gift": out.get("gift", ""),
        "amount": out.get("amount", 0),
        "refund": out.get("refund", 0),
        "return_gift": out.get("return_gift") or "",
    }
    return _render_with_ats(template, ctx)


# ==================== 通用校验 ====================

def _resolve_group(event: Event) -> tuple[str | None, str | None]:
    """返回 (group_id, 拒绝消息)。私聊给提示；非白名单群静默忽略（复用 game_store.resolve_group_id）。"""
    group_id, is_private = resolve_group_id(event)
    if group_id is None:
        return None, (_error("private_only") if is_private else None)
    return group_id, None


def _reset_today_signins(group: dict, today: str) -> int:
    """仅清掉本群用户当日签到闸门；不改 RPG 连签/装备/运势等状态。"""
    cleared = 0
    for user in group.get("users", {}).values():
        if isinstance(user, dict) and user.get("last_sign_in") == today:
            user["last_sign_in"] = ""
            cleared += 1
    return cleared


async def _sign_in_delay() -> None:
    """签到回复前的随机延迟，错开群里另一个签到 bot 的消息。"""
    d = _cfg("sign_delay_sec", {})
    await asyncio.sleep(random.uniform(float(d.get("min", 3)), float(d.get("max", 5))))


# ==================== 指令：签到 ====================

sign_cmd = on_command("签到", priority=5, block=True)


@sign_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ  # 超管不限次（测试用）
    if is_sleeping() and not is_superuser:  # 0–6 点睡眠拦截（超管除外）
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _get_user(group, user_id, _display_name(event))

        if not is_superuser and user.get("last_sign_in") == today:
            return  # 重复签到静默：群里另有签到 bot 应答，避免双重刷屏

        sign_cfg = _cfg("sign_in", {})
        amount = random.randint(int(sign_cfg.get("min", 50)), int(sign_cfg.get("max", 100)))
        user["points"] = int(user.get("points", 0)) + amount
        user["last_sign_in"] = today
        user["protect_until"] = time.time() + int(_steal_cfg().get("protect_minutes", 60)) * 60
        # 签到搭车钩子（rpg 运势/经验等）：持锁内纯内存改 group，收集追加播报行
        extra_lines = run_signin_hooks(group, user_id, random)
        _save_data(data)
        total = int(user["points"])

    # 出锁后再延迟 3–5s 发送（不占锁），错开另一个签到 bot 的消息
    await _sign_in_delay()
    template = random.choice(_copy("sign_in"))
    msg = _render_with_ats(template, {"a": user_id, "amount": amount, "total": total})
    for line in extra_lines:
        msg = msg + "\n" + line
    await sign_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：送礼 ====================

gift_cmd = on_command("送礼", force_whitespace=True, priority=5, block=True)


@gift_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    # 格式校验：只接受「送礼 @某人」，后面不能带任何文字
    if args and args.extract_plain_text().strip():
        await gift_cmd.finish(
            MessageSegment.reply(event.message_id) + "格式是「送礼 @某人」，不用加字。"
        )

    sender_id = event.get_user_id()
    is_superuser = sender_id == SUPERUSER_QQ  # 超管不限次（测试用）
    if is_sleeping() and not is_superuser:  # 0–6 点睡眠拦截（超管除外）
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    target_qq = _first_at_qq(getattr(event, "original_message", None))

    # 没有 @ 有效对象时静默忽略（避免"偷什么"之类误触发）
    if not target_qq or target_qq == "all":
        return
    if target_qq == sender_id:
        return
    if target_qq == str(getattr(bot, "self_id", "")):
        return

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        sender = _get_user(group, sender_id, _display_name(event))
        _get_user(group, target_qq)  # 确保被送者入册（用于排行/亲密度查询）

        if not is_superuser and sender.get("last_gift") == today:
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("already_gifted")
            )

        points = int(sender.get("points", 0))
        gift = _pick_gift(points)
        if gift is None:
            cheapest = _cheapest_gift() or {"name": "", "cost": 0}
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id)
                + _error("insufficient", name=cheapest.get("name", ""), cost=int(cheapest.get("cost", 0)), total=points)
            )

        # 先扣消耗，再按事件结算（部分意外会返还）
        sender["points"] = points - int(gift["cost"])
        sender["last_gift"] = today

        if _is_special_gift(gift):
            main_event, mishap, return_key = "special", None, None
        else:
            main_event = _roll_main_event()
            mishap = _roll_mishap() if main_event == "mishap" else None
            return_key = _roll_return_gift() if main_event == "return" else None

        out = _settle(group, sender_id, target_qq, gift, main_event, mishap, return_key)
        _bump_count(group, sender_id, target_qq)  # 记一次有向送礼（无论事件结果）
        _save_data(data)

        broadcast = _build_broadcast(out, sender_id, target_qq)
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)


# ==================== 指令：偷 ====================

steal_cmd = on_command("偷", aliases={"偷积分"}, force_whitespace=True, priority=5, block=True)


@steal_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await steal_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    # 格式校验：只接受「偷 @某人」，后面不能带任何文字
    if args and args.extract_plain_text().strip():
        await steal_cmd.finish(
            MessageSegment.reply(event.message_id) + "格式是「偷 @某人」，不用加字。"
        )

    thief_id = event.get_user_id()
    is_superuser = thief_id == SUPERUSER_QQ  # 超管不限、跳过保护与睡眠（测试用）
    if is_sleeping() and not is_superuser:
        await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    target_qq = _first_at_qq(getattr(event, "original_message", None))

    # 没有 @ 有效对象时静默忽略（避免"偷什么"之类误触发）
    if not target_qq or target_qq == "all":
        return
    if target_qq == thief_id:
        return
    if target_qq == str(getattr(bot, "self_id", "")):
        return

    cfg = _steal_cfg()
    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        thief = _get_user(group, thief_id, _display_name(event))
        victim = _get_user(group, target_qq)

        if not is_superuser:
            if thief.get("steal_date") != today:
                thief["steal_date"], thief["steal_used"] = today, 0
            if int(thief.get("steal_used", 0)) >= int(cfg.get("daily_limit", 2)):
                await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("steal_limit"))
            if int(victim.get("points", 0)) < int(cfg.get("min_target_points", 50)):
                await steal_cmd.finish(
                    MessageSegment.reply(event.message_id)
                    + _error("steal_too_poor", min=int(cfg.get("min_target_points", 50)))
                )
            if time.time() < float(victim.get("protect_until", 0)):
                await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("steal_protected"))
            if victim.get("robbed_date") != today:
                victim["robbed_date"], victim["robbed_count"] = today, 0
            if int(victim.get("robbed_count", 0)) >= int(cfg.get("victim_daily_limit", 3)):
                await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("steal_protected"))

        outcome = _steal_outcome()
        out = _settle_steal(group, thief_id, target_qq, outcome)

        if not is_superuser:
            thief["steal_date"], thief["steal_used"] = today, int(thief.get("steal_used", 0)) + 1
            victim["robbed_date"], victim["robbed_count"] = today, int(victim.get("robbed_count", 0)) + 1
        _save_data(data)

        template = random.choice(_copy(f"steal_{outcome}"))
        msg = _render_with_ats(template, {
            "a": thief_id, "b": target_qq, "amount": out["amount"], "bond": out["bond"],
        })
        await steal_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：我的积分 ====================

points_cmd = on_command("我的积分", priority=5, block=True)


@points_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await points_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    today = _today_str()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _get_user(group, user_id, _display_name(event))

    can_sign = "可签到 ✅" if user.get("last_sign_in") != today else "今日已签到"
    can_gift = "可送礼 ✅" if user.get("last_gift") != today else "今日已送礼"
    msg = (
        f"你当前有 {int(user.get('points', 0))} 积分。\n"
        f"· {can_sign}\n· {can_gift}"
    )
    await points_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：礼物列表 ====================

list_cmd = on_command("礼物列表", priority=5, block=True)


@list_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    lines = ["🎁 彰冬礼物档位（稀有度递增）："]
    for gift in _gift_list():
        lines.append(f"· {gift['name']}　{gift['cost']} 积分　羁绊+{gift['intimacy']}")
    lines.append("用法：送礼 @某人 —— 系统会从你买得起的礼物里随机送一份（越贵的越容易抽中）。")
    await list_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：亲密度 ====================

intimacy_cmd = on_command("我的羁绊", priority=5, block=True)


@intimacy_cmd.handle()
async def _(bot: Bot, event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    data = _load_data()
    group = _get_group(data, group_id)
    target_qq = _first_at_qq(getattr(event, "original_message", None))

    if target_qq and target_qq not in ("all", user_id):
        # 尝试获取对方群名片（bot API → card → nickname → 兜底 QQ）
        target_name = _name_of(group, target_qq)
        if target_name.startswith("用户"):
            try:
                member_info = await bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(target_qq)
                )
                target_name = member_info.get("card") or member_info.get("nickname") or target_qq
            except Exception:
                target_name = target_qq

        if GIFT_USE_HTML_RENDER:
            left = {"qq": user_id, "name": _display_name(event)}
            right = {"qq": target_qq, "name": target_name}
            intimacy = _get_intimacy(group, user_id, target_qq)
            img_bytes = None
            try:
                page_data = build_bond_page_data(left, right, intimacy, levels=_bond_levels())
                img_bytes = await render_bond_page("bond.html", page_data)
            except Exception as e:
                logger.warning(f"bond render failed ({e}), falling back to text")
            if img_bytes is not None:
                await intimacy_cmd.finish(
                    MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes)
                )
            else:
                await intimacy_cmd.finish(
                    MessageSegment.reply(event.message_id) + _bond_card(group, user_id, target_qq)
                )
        else:
            await intimacy_cmd.finish(
                MessageSegment.reply(event.message_id) + _bond_card(group, user_id, target_qq)
            )
        return  # @某人分支结束，不继续往下走

    # 不带 @：列出自己所有羁绊
    partners = _top_partners(group, user_id, limit=999)
    if not partners:
        await intimacy_cmd.finish(
            MessageSegment.reply(event.message_id) + "你还没有和谁建立羁绊呢，快去送礼吧～"
        )

    if GIFT_USE_HTML_RENDER:
        partner_dicts: list[dict] = []
        for other_id, value in partners:
            name = _name_of(group, other_id)
            partner_dicts.append({"qq": other_id, "name": name, "intimacy": value})
        owner = {"qq": user_id, "name": _display_name(event)}
        img_bytes = None
        try:
            page_data = build_my_bonds_page_data(owner, partner_dicts, levels=_bond_levels())
            img_bytes = await render_bond_page("my_bonds.html", page_data)
        except Exception as e:
            logger.warning(f"my bonds render failed ({e}), falling back to text")
        if img_bytes is not None:
            await intimacy_cmd.finish(
                MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes)
            )
        else:
            lines = ["你的同好羁绊 Top："]
            for other_id, value in partners[:5]:
                lines.append(f"· {_name_of(group, other_id)}：{value}（{_bond_level(value)['name']}）")
            await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
    else:
        lines = ["你的同好羁绊 Top："]
        for other_id, value in partners[:5]:
            lines.append(f"· {_name_of(group, other_id)}：{value}（{_bond_level(value)['name']}）")
        await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


_TEST_MY_BOND_PARTNERS: list[dict] = [
    {"qq": "test01", "name": "测试同好01", "avatar": "", "intimacy": 7200},
    {"qq": "test02", "name": "测试同好02", "avatar": "", "intimacy": 4200},
    {"qq": "test03", "name": "测试同好03", "avatar": "", "intimacy": 2600},
    {"qq": "test04", "name": "测试同好04", "avatar": "", "intimacy": 1314},
    {"qq": "test05", "name": "测试同好05", "avatar": "", "intimacy": 980},
    {"qq": "test06", "name": "测试同好06", "avatar": "", "intimacy": 520},
    {"qq": "test07", "name": "测试同好07", "avatar": "", "intimacy": 260},
    {"qq": "test08", "name": "测试同好08", "avatar": "", "intimacy": 120},
    {"qq": "test09", "name": "测试同好09", "avatar": "", "intimacy": 45},
    {"qq": "test10", "name": "测试同好10", "avatar": "", "intimacy": 5},
    {"qq": "test11", "name": "测试同好11", "avatar": "", "intimacy": -10},
    {"qq": "test12", "name": "测试同好12", "avatar": "", "intimacy": -50},
    {"qq": "test13", "name": "测试同好13", "avatar": "", "intimacy": -120},
    {"qq": "test14", "name": "测试同好14", "avatar": "", "intimacy": -300},
    {"qq": "test15", "name": "测试同好15", "avatar": "", "intimacy": -650},
    {"qq": "test16", "name": "测试同好16", "avatar": "", "intimacy": -1000},
]


# ==================== 指令：测试我的羁绊界面 ====================

test_my_bonds_cmd = on_command("test我的羁绊", aliases={"测试我的羁绊"}, priority=5, block=True)


@test_my_bonds_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await test_my_bonds_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if str(event.get_user_id()) != SUPERUSER_QQ:
        await test_my_bonds_cmd.finish(MessageSegment.reply(event.message_id) + "这个测试指令仅限超管使用。")

    owner = {"qq": event.get_user_id(), "name": f"{_display_name(event)}（测试）", "avatar": ""}
    try:
        page_data = build_my_bonds_page_data(
            owner,
            [p.copy() for p in _TEST_MY_BOND_PARTNERS],
            levels=_bond_levels(),
            title="我的羁绊 · 测试",
        )
        img_bytes = await render_bond_page("my_bonds.html", page_data)
    except Exception as e:
        logger.warning(f"test my bonds render failed ({e})")
        await test_my_bonds_cmd.finish(MessageSegment.reply(event.message_id) + f"测试羁绊图渲染失败：{e}")

    await test_my_bonds_cmd.finish(
        MessageSegment.reply(event.message_id)
        + "测试数据：16 段羁绊，不写入真实数据。\n"
        + MessageSegment.image(img_bytes)
    )


def _top_partners(group: dict, user_id: str, limit: int = 5) -> list[tuple[str, int]]:
    """返回 user_id 的羁绊伙伴 [(other_id, value), ...]，按羁绊降序。"""
    result: list[tuple[str, int]] = []
    for key, value in group.get("intimacy", {}).items():
        ids = key.split("|||")
        if user_id in ids:
            other = ids[0] if ids[1] == user_id else ids[1]
            result.append((other, int(value)))
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:limit]


def _name_of(group: dict, user_id: str) -> str:
    user = group.get("users", {}).get(str(user_id))
    if isinstance(user, dict) and user.get("display_name"):
        return user["display_name"]
    return f"用户{user_id}"


# ==================== 指令：群羁绊排行 ====================

rank_cmd = on_command("群羁绊排行", aliases={"群羁绊排行", "羁绊排行"}, priority=5, block=True)


@rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    data = _load_data()
    group = _get_group(data, group_id)
    pairs = sorted(group.get("intimacy", {}).items(), key=lambda kv: int(kv[1]), reverse=True)[:10]
    if not pairs:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + "本群还没有羁绊数据，快去送礼吧～")

    if GIFT_USE_HTML_RENDER:
        entries: list[dict] = []
        for key, value in pairs:
            a, b = key.split("|||")
            entries.append({
                "left": {"qq": a, "name": _name_of(group, a)},
                "right": {"qq": b, "name": _name_of(group, b)},
                "intimacy": int(value),
            })
        img_bytes = None
        try:
            rank_data = build_bond_rank_page_data(entries, levels=_bond_levels())
            img_bytes = await render_bond_page("bond_rank.html", rank_data)
        except Exception:
            logger.warning("bond rank render failed, falling back to text")
        if img_bytes is not None:
            await rank_cmd.finish(
                MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes)
            )
        else:
            lines = ["\U0001f49e 本群同好羁绊排行："]
            for idx, (key, value) in enumerate(pairs, 1):
                a, b = key.split("|||")
                lines.append(f"{idx}. {_name_of(group, a)} × {_name_of(group, b)}：{value}（{_bond_level(value)['name']}）")
            await rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
    else:
        lines = ["💞 本群同好羁绊排行："]
        for idx, (key, value) in enumerate(pairs, 1):
            a, b = key.split("|||")
            lines.append(f"{idx}. {_name_of(group, a)} × {_name_of(group, b)}：{value}（{_bond_level(value)['name']}）")
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：重置送礼（超管） ====================

reset_cmd = on_command("重置送礼", priority=5, block=True)


@reset_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return
    data = _load_data()
    data.setdefault("groups", {})[str(group_id)] = _new_group()
    _save_data(data)
    await reset_cmd.finish(MessageSegment.reply(event.message_id) + "已清空本群的送礼/积分/羁绊数据。")


# ==================== 指令：重置本群签到（超管） ====================

reset_signin_cmd = on_command(
    "重置本群签到",
    aliases={"重置全群签到", "重置签到次数"},
    priority=5,
    block=True,
)


@reset_signin_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await reset_signin_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        cleared = _reset_today_signins(group, today)
        _save_data(data)

    if cleared:
        msg = f"本群今日签到已放开，已清掉 {cleared} 人的签到闸门。RPG 连签和今日装备没动。"
    else:
        msg = "本群今天还没人被签到闸门卡住。"
    await reset_signin_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：送礼功能帮助 ====================

help_cmd = on_command("送礼功能帮助", aliases={"送礼帮助", "送礼说明"}, priority=5, block=True)


@help_cmd.handle()
async def _(event: Event):
    msg = (
        "🎁 彰冬送礼系统\n"
        "━━━━━━━━━━━━━━\n"
        "· 签到 — 每天领一次积分（50~100）\n"
        "· 送礼 @某人 — 每天一次，随机送礼物给对方，累积羁绊值\n"
        "· 偷 @某人 — 每天两次，冒险顺走对方积分（会掉羁绊）\n"
        "· 我的积分 — 查看当前积分和今日状态\n"
        "· 礼物列表 — 查看全部礼物档位和花费\n"
        "· 我的羁绊 @某人 — 查看你与 ta 的羁绊详情图\n"
        "· 群羁绊排行 — 查看本群羁绊排行榜\n"
        "\n"
        "💡 礼物越贵羁绊加得越多；送礼有概率暴击/回礼/意外事件。\n"
        "💡 偷人需谨慎：偷越亲近的人掉羁绊越多，还可能被反杀。"
    )
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    else:
        await help_cmd.finish(msg)
