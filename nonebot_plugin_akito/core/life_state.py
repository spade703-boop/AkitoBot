import datetime
import random
import re
import time

from . import TZ_CN, TZ_JST, GROUP_IMAGE_PERMISSIONS
from .data import DAILY_ROUTINE, REACTIONS_DB

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


def grant_safety_pass(seconds: int = 5):
    global AKITO_SAFE_UNTIL
    AKITO_SAFE_UNTIL = time.time() + seconds


def get_safe_until() -> float:
    return AKITO_SAFE_UNTIL


def get_last_complaint() -> float:
    return AKITO_LAST_COMPLAINT


def set_last_complaint(value: float):
    global AKITO_LAST_COMPLAINT
    AKITO_LAST_COMPLAINT = value


def get_daily_activity(hour: int, weekday: int) -> str:
    global AKITO_STATUS
    is_weekend = weekday >= 5
    key = ""
    if 0 <= hour < 6:     key = "late_night"
    elif 6 <= hour < 8:   key = "morning_weekend" if is_weekend else "morning_weekday"
    elif 8 <= hour < 12:  key = "noon_weekend"    if is_weekend else "noon_weekday"
    elif 12 <= hour < 13: key = "lunch_weekend"   if is_weekend else "lunch_weekday"
    elif 13 <= hour < 15: key = "afternoon_weekend" if is_weekend else "afternoon_weekday"
    elif 15 <= hour < 18: key = "evening"
    elif 18 <= hour < 21: key = "night_training"
    elif 21 <= hour < 24: key = "night_home"
    else:                 key = "late_night"

    now_ts = time.time()
    if AKITO_STATUS["current_key"] != key:
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


def check_sleep_status(msg: str) -> tuple:
    now = datetime.datetime.now(TZ_CN)
    now_jst = datetime.datetime.now(TZ_JST)
    hour = now.hour

    if not (0 <= hour < 6):
        return False, ""

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
            mumbles = ["……呼……吵死了……闭嘴……", "（翻身背对着你）……呼……", "……嗯……别吵……明天再说……", "（将被子蒙过头）……zzZ……"]
            return True, random.choice(mumbles)

    relation_features = ["评价", "看法", "印象", "怎么看", "认识"]
    is_evaluation = any(k in clean_msg for k in relation_features)

    if is_evaluation:
        selected = random.choice(REACTIONS_DB.get("sleep_relation") or ["【状态：困】\n动作：闭着眼。\n台词参考：……不知道……困……"])
        instruction = (
            f"\n⚠️⚠️【特殊事件：深夜被叫醒问话】⚠️⚠️\n"
            f"当前时间：凌晨 {now_jst.strftime('%H:%M')}（JST）。用户把你吵醒了，问你对某人的看法：'{msg}'。\n"
            f"你很困，**完全没有拿手机去查**，而是闭着眼凭印象回答。\n严格扮演：\n{selected}\n"
        )
        return False, instruction
    else:
        selected = random.choice(REACTIONS_DB.get("sleep_search") or ["【状态：困】\n动作：闭着眼查手机。\n台词参考：……给你……呼……"])
        instruction = (
            f"\n⚠️⚠️【特殊事件：深夜被迫营业】⚠️⚠️\n"
            f"当前时间：凌晨 {now_jst.strftime('%H:%M')}（JST）。用户让你查数据/资讯：'{msg}'。\n"
            f"你必须拿起手机去查。严格扮演：\n{selected}\n"
        )
        return False, instruction


def get_festival_buff(date_obj) -> str:
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


def parse_duration_and_content(raw_text: str) -> tuple:
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
    allowed_list = GROUP_IMAGE_PERMISSIONS.get(group_id, [])
    if not allowed_list:
        return False
    if "all" in allowed_list:
        return True
    return category in allowed_list
