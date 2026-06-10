"""生活状态机：作息 / 睡眠 / 节日 / 晨跑等状态推断，以及安全期、吐槽冷却、图片权限等运行时开关。"""

import datetime
import random
import re
import time

from nonebot.log import logger

from . import GROUP_IMAGE_PERMISSIONS, TZ_CN, TZ_JST
from .data import DAILY_ROUTINE, SLEEP_DB

AKITO_STATUS: dict = {
    "current_key": "",
    "event_history": [],
    "cached_content": "",
    "expire_time": 0.0,
    "last_trigger_user": "",   # 记录上一条 chat 回复是谁触发的，供 self_monitor 判断
}
STATE_DURATION = 1800

AKITO_SAFE_UNTIL = 0.0
AKITO_LAST_COMPLAINT = 0.0


def grant_safety_pass(seconds: int = 5) -> None:
    """开启一段安全期（默认 5 秒），期间抑制深夜抱怨等被动反应。"""
    global AKITO_SAFE_UNTIL
    AKITO_SAFE_UNTIL = time.time() + seconds


def get_safe_until() -> float:
    """返回安全期截止时间戳。"""
    return AKITO_SAFE_UNTIL


def get_last_complaint() -> float:
    """返回上次深夜抱怨的时间戳。"""
    return AKITO_LAST_COMPLAINT


def set_last_complaint(value: float) -> None:
    """记录本次深夜抱怨时间戳（用于冷却控制）。"""
    global AKITO_LAST_COMPLAINT
    AKITO_LAST_COMPLAINT = value


def is_sleeping() -> bool:
    """返回当前是否处于睡眠时段（北京时间 0:00–5:59）。"""
    now = datetime.datetime.now(TZ_CN)
    return 0 <= now.hour < 6


def sleep_block(pool_key: str, silent_chance: float = 0.0, fallback: str = "……zzZ") -> str | None:
    """统一的睡眠拦截决策——取代散落在各模块的手写夜间判断。

    Args:
        pool_key: SLEEP_DB 中的 key，用于选取回复文案。
        silent_chance: 静默拦截的概率（0.0 = 总回复，1.0 = 总静默）。
        fallback: SLEEP_DB 缺 key 或为空时的兜底文案。

    Returns:
        ``""``      — 非睡眠时段，正常放行。
        ``None``    — 睡眠时段 + 静默拦截，调用方应丢弃消息。
        ``"文案"``  — 睡眠时段 + 回复拦截，调用方应发送此文本。
    """
    if not is_sleeping():
        return ""
    if random.random() < silent_chance:
        logger.debug(f"😴 [SleepBlock] {pool_key}: silent")
        return None
    pool = SLEEP_DB.get(pool_key) or [fallback]
    chosen = random.choice(pool)
    logger.info(f"😴 [SleepBlock] {pool_key}: reply")
    return chosen


def compute_period_key(hour: int, weekday: int, minute: int = 0) -> str:
    """按小时 / 星期 / 分钟计算 routine 时段 key。

    时段划分的**单一真相源**：get_daily_activity 与 time_awareness 均转调此函数，
    调整作息划分只需改这一处。
    """
    is_weekend = weekday >= 5
    if 0 <= hour < 6:     return "late_night"
    elif 6 <= hour < 8:   return "morning_weekend" if is_weekend else "morning_weekday"
    elif 8 <= hour < 12:  return "noon_weekend"    if is_weekend else "noon_weekday"
    elif 12 <= hour < 13: return "lunch_weekend"   if is_weekend else "lunch_weekday"
    elif 13 <= hour < 15: return "afternoon_weekend" if is_weekend else "afternoon_weekday"
    elif 15 <= hour < 18: return "evening"
    elif 18 <= hour < 21: return "night_training"
    elif 21 <= hour < 24: return "sleep_buffer" if hour == 23 and minute >= 45 else "night_home"
    else:                 return "late_night"


def get_daily_activity(hour: int, weekday: int, minute: int = 0) -> str:
    """按时段返回彰人当前状态文本，带 30 分钟缓存与同段去重抽取。"""
    global AKITO_STATUS
    key = compute_period_key(hour, weekday, minute)

    now_ts = time.time()
    if AKITO_STATUS["current_key"] != key:
        old_cached = AKITO_STATUS.get("cached_content")
        if old_cached:
            old_status = old_cached.get("status", old_cached) if isinstance(old_cached, dict) else old_cached
            AKITO_STATUS["previous_context"] = old_status

        AKITO_STATUS["current_key"] = key
        AKITO_STATUS["event_history"] = []
        AKITO_STATUS["cached_content"] = ""
        AKITO_STATUS["expire_time"] = 0.0

    if now_ts < AKITO_STATUS["expire_time"] and AKITO_STATUS["cached_content"]:
        cached = AKITO_STATUS["cached_content"]
        status_text = cached.get("status", cached) if isinstance(cached, dict) else cached
        return f"【当前状态】{status_text}"

    routine_list = DAILY_ROUTINE.get(key, [{"status": "正在发呆。", "poke": ["……干嘛。"]}])
    valid_choices = [x for x in routine_list if x not in AKITO_STATUS["event_history"]]
    if not valid_choices:
        valid_choices = routine_list
        AKITO_STATUS["event_history"] = []

    new_event = random.choice(valid_choices)
    AKITO_STATUS["event_history"].append(new_event)
    AKITO_STATUS["cached_content"] = new_event
    AKITO_STATUS["expire_time"] = now_ts + STATE_DURATION

    status_text = new_event.get("status", new_event) if isinstance(new_event, dict) else new_event
    return f"【当前状态】{status_text}"


def check_sleep_status(msg: str) -> tuple[bool, str]:
    """深夜(0–6 点)睡眠状态判定。

    Returns:
        (是否照常处理, 指令/标记文本)。如 (True, "ignore") 表示装睡忽略，
        (False, instruction) 表示被唤醒并注入对应扮演指令。
    """
    if not is_sleeping():
        return False, ""

    now_jst = datetime.datetime.now(TZ_JST)

    msg_lower = msg.strip().lower()
    clean_msg = msg_lower
    for name in ["东云小彰", "彰人", "小彰", "松饼", "akito", "bot", "机器人"]:
        clean_msg = clean_msg.replace(name, "")
    clean_msg = clean_msg.strip()

    wake_up_triggers = ["搜", "查", "是什么", "谁是", "天气", "新闻", "多少钱", "搜一下", "搜索", "查询", "帮我查", "我想知道", "告诉我", "我想问问", "你知道", "帮我看看", "问问你"]
    is_woken_up = any(k in clean_msg for k in wake_up_triggers)

    if not is_woken_up:
        if random.random() < 0.8:
            return True, "ignore"
        else:
            mumble_pool = SLEEP_DB.get("sleep_mumbles") or ["……zzZ……"]
            return True, random.choice(mumble_pool)

    relation_features = ["评价", "看法", "印象", "怎么看", "认识"]
    is_evaluation = any(k in clean_msg for k in relation_features)

    if is_evaluation:
        selected = random.choice(SLEEP_DB.get("sleep_relation") or ["【状态：困】\n动作：闭着眼。\n台词参考：……不知道……困……"])
        instruction = (
            f"\n⚠️⚠️【特殊事件：深夜被叫醒问话】⚠️⚠️\n"
            f"当前时间：凌晨 {now_jst.strftime('%H:%M')}（JST）。用户把你吵醒了，问你对某人的看法：'{msg}'。\n"
            f"你很困，**完全没有拿手机去查**，而是闭着眼凭印象回答。\n严格扮演：\n{selected}\n"
        )
        return False, instruction
    else:
        selected = random.choice(SLEEP_DB.get("sleep_search") or ["【状态：困】\n动作：闭着眼查手机。\n台词参考：……给你……呼……"])
        instruction = (
            f"\n⚠️⚠️【特殊事件：深夜被迫营业】⚠️⚠️\n"
            f"当前时间：凌晨 {now_jst.strftime('%H:%M')}（JST）。用户让你查数据/资讯：'{msg}'。\n"
            f"你必须拿起手机去查。严格扮演：\n{selected}\n"
        )
        return False, instruction


def get_festival_buff(date_obj: datetime.datetime) -> str:
    """根据日期返回节日气氛注入文本；非节日返回空串。"""
    m, d = date_obj.month, date_obj.day
    hour = date_obj.hour
    calendar_map = {
        (1, 1): "元旦", (1, 15): "成人之日", (2, 3): "节分", (2, 14): "情人节",
        (3, 3): "女儿节", (3, 14): "白色情人节", (4, 1): "愚人节", (5, 5): "儿童节/黄金周",
        (7, 7): "七夕", (8, 15): "孟兰盆节", (10, 31): "万圣节", (11, 11): "Pocky Day",
        (11, 12): "东云彰人生日", (12, 24): "平安夜", (12, 25): "圣诞节", (12, 31): "大晦日",
    }
    name = calendar_map.get((m, d), "")
    if name:
        base_buff = f"【📅 今日特殊事件】今天是：{name}。\n请根据日本高中生的生活习惯，感知这个节日的气氛。\n"
        if 18 <= hour <= 23:
            base_buff += '⚠️【时间修正】虽然现在是晚上，但节日气氛正浓！禁止说"节日已经结束了"。'
        return base_buff
    return ""


def get_morning_run_buff(hour: int) -> str:
    """北京时间 6 点整段（6:00–6:59）返回晨跑状态注入，其余时间返回空字符串。"""
    if hour != 6:
        return ""
    return (
        "🏃【当前强制物理状态：晨跑中】\n"
        "现在是清晨，彰人正在户外晨跑，这是他每天雷打不动的习惯。\n"
        "· 他戴着耳机，步伐稳定，气息略微急促。\n"
        "· 如果有人发消息，他是一边跑一边低头瞄手机回复——字数偏短、语气简洁，不会长篇大论。\n"
        "· 跑步场景中可以出现：早晨空气、跑道/公园环境、脚步声、音乐。\n"
        "· **禁止**描写他停下来或坐着回复，除非对话内容非常紧急。"
    )


def get_sleep_buffer_buff(hour: int, minute: int) -> str:
    """北京时间 23:45–23:59 返回睡前准备状态注入，其余时间返回空字符串。"""
    if not (hour == 23 and minute >= 45):
        return ""

    prev = AKITO_STATUS.get("previous_context", "")
    base = (
        "😴【当前强制物理状态：睡前准备中】\n"
        "现在是深夜，彰人正在做睡前准备，马上就要睡觉了。\n"
        "· 他正在洗漱、换睡衣、设闹钟、关灯、躺下——总之在收尾这一天。\n"
        "· 说话语调比平时更低更缓，偶尔打哈欠，回复偏短。\n"
        "· 如果有人发消息会看一眼，但回复意愿极低——他马上要睡了。\n"
        "· 场景中可能出现：浴室水声、关灯声、被子翻动、手机屏幕暗光。\n"
        "· **绝对禁止**：主动开启新话题、答应去做需要离开床的事情、长篇大论。\n"
        "· **允许**：含糊应付两句然后表示要睡了，或直接说「明天再说」。"
    )
    if prev:
        base += (
            f"\n🧩【睡前准备前在做的事】：在开始准备睡觉之前，你正在「{prev}」。\n"
            "虽然你现在已经在做睡前准备了，但如果群友问起那件事的后续，"
            "你记得自己当时在做那件事——那个状态是紧接着现在发生的，"
            "不要表现得像没发生过一样。只是你现在太困了，可以用「已经弄好了」「明天再说吧」"
            "之类的简短方式带过。"
        )
    return base


# 彰冬同框时段：VBS 团队活动 / 共同行程，冬弥默认在场或近在身边
_TOYA_COPRESENT_PERIODS = {"lunch_weekday", "evening", "night_training"}

# routine→冬弥 位置推断的 IF-THEN 参考（并入自原 reactions.py 的 toya_radar 模板）
_TOYA_REASONING = (
    "推断参考：练习/团队活动→冬弥和你在一起或刚分开/在赶来路上；上课/在校→冬弥在隔壁班(2-B)，"
    "可能值日、去洗手间或被叫去帮忙；逛街买衣服→冬弥在旁边帮你提袋，或你在帮他挑；吃松饼/在咖啡店"
    "→冬弥在对面；晨跑→冬弥跟在后面，或你在想他昨晚发的消息；在家/睡前→冬弥刚发完晚安，或你在想他。"
)


def get_toya_anchor() -> str:
    """据当前缓存的 routine 推断冬弥此刻的合理位置，返回 Prompt 片段；无缓存返回空串。

    并入自原 ``冬弥呢`` 指令（toya_radar 模板）的位置推断逻辑，使主对话引擎也具备
    routine 锚定的冬弥去向推理 + 跨轮连贯锁。WL2 决裂世界线由调用方门控跳过。
    依赖调用方已先调用 ``get_daily_activity`` 使 ``AKITO_STATUS`` 缓存为热。
    """
    cached = AKITO_STATUS.get("cached_content", "")
    status = cached.get("status", cached) if isinstance(cached, dict) else cached
    key = AKITO_STATUS.get("current_key", "")
    if not status:
        return ""
    status = str(status)

    if "冬弥" in status:
        line = f"此刻冬弥就和你在一起（当前：{status}）。"
    elif key in _TOYA_COPRESENT_PERIODS:
        line = f"现在是 VBS 团队活动 / 共同行程时段，冬弥就和你在同一场所或近在身边（当前：{status}）。"
    else:
        line = f"当前：{status} 请据此把冬弥放在与当前情境自洽的位置。"

    return (
        f"\n🧭【冬弥此刻】{line}\n{_TOYA_REASONING}\n"
        "约束：冬弥必须和你在同一场景或与当前活动直接相关；理由要轻松日常（买水/帮忙/听歌等），"
        "禁止编造无关支线（如无端跑去喝咖啡，除非你正好在咖啡店），禁止升学/退学/生重病等沉重话题。\n"
        "（连贯锁）本轮对话里你已说过冬弥在做什么/在哪，就必须前后一致——换措辞可以，事实不能自相矛盾。"
    )


def parse_duration_and_content(raw_text: str) -> tuple[int, str]:
    """解析「<数字><单位> <内容>」为 (秒数, 内容)；无匹配返回 (600, 原文)。单位 s/h/d，缺省按分钟。"""
    match = re.match(r"^(\d+)\s*([a-zA-Z]*)\s+(.*)", raw_text, re.DOTALL)
    if not match:
        return 600, raw_text
    num = int(match.group(1))
    unit = match.group(2).lower()
    content = match.group(3)
    if unit.startswith("s"):   seconds = num
    elif unit.startswith("h"): seconds = num * 3600
    elif unit.startswith("d"): seconds = num * 86400
    else:                      seconds = num * 60
    return seconds, content


def check_img_permission(group_id: int, category: str) -> bool:
    """判断某群是否有权发送某类图片（"all" 表示全部放行）。"""
    allowed_list = GROUP_IMAGE_PERMISSIONS.get(group_id, [])
    if not allowed_list:
        return False
    if "all" in allowed_list:
        return True
    return category in allowed_list
