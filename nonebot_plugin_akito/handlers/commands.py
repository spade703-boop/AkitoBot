"""管理指令：查看记忆、清空记忆、人设 / 数据热重载、临时设定植入等。"""

import datetime
import random
import sqlite3
import time

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg

from ..core import (
    AKITO_STATUS,
    ALLOWED_CHAT_GROUPS,
    ALLOWED_MEMORY_GROUPS,
    DB_PATH,
    SUPERUSER_QQ,
    TZ_CN,
    get_memory_key,
    get_user_memory,
    grant_safety_pass,
    parse_duration_and_content,
    reload_assets,
    reload_persona,
    save_memory,
)


def _stamp_trigger(event: Event) -> None:
    """在任何指令回复前调用：
    1. 记录触发者身份（供 self_monitor 使用）
    2. 设置安全通行证，防止指令回复本身触发深夜抱怨
    3. 若触发者是超管，更新该群的超管触发时间戳（per-group，避免跨群误压制）
    """
    user_id = str(event.get_user_id())
    group_id = str(getattr(event, 'group_id', 'private'))
    AKITO_STATUS["last_trigger_user"] = user_id
    grant_safety_pass(5)
    if user_id == SUPERUSER_QQ:
        AKITO_STATUS.setdefault("last_superuser_trigger_time", {})[group_id] = time.time()


# --- 1. 记忆查看指令 ---
view_cmd = on_command("查看记忆", aliases={"记住了啥", "check_memory", "当前状态", "状态"}, priority=5, block=True)
@view_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_MEMORY_GROUPS: return
    mem = get_user_memory(get_memory_key(event))
    now = time.time()
    valid_implants = [(m, m.get("expire_at", m.get("expire_time", 0))) for m in mem.get("temp_implants", []) if m.get("expire_at", m.get("expire_time", 0)) > now]
    mem["temp_implants"] = [x[0] for x in valid_implants]
    _stamp_trigger(event)
    if not valid_implants:
        await view_cmd.finish("🧠 当前没有额外的设定。\n这是一个普通的东云彰人。")
    output = "🧠 【当前生效的设定】\n========================\n"
    for idx, (implant, et) in enumerate(valid_implants, 1):
        left = int(et - now)
        output += f"{idx}. {implant['content']}\n   ⏳ 剩余：{left // 60}分 {left % 60}秒\n"
    output += '========================\n💡 发送"清除记忆"可立刻恢复原状。'
    await view_cmd.finish(output.strip())


view_facts_cmd = on_command("查看长期记忆", aliases={"小彰都记住了什么", "记忆列表"}, priority=5, block=True)
@view_facts_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_MEMORY_GROUPS: return
    facts = get_user_memory(get_memory_key(event)).get("long_term_facts", [])
    _stamp_trigger(event)
    if not facts: await view_facts_cmd.finish("🧠 脑子里空空如也。")
    msg = "🧠 【已生效的长期记忆】\n========================\n"
    for idx, content in enumerate(facts, 1): msg += f"[{idx}] {content}\n"
    msg += '========================\n💡 发送"遗忘 [序号]"删除，或"遗忘 全部"清空。'
    await view_facts_cmd.finish(msg.strip())


# --- 2. 记忆植入/清除指令 ---
inject_cmd = on_command("植入记忆", aliases={"接下来的事是", "记住", "add_memory"}, priority=5, block=True)
@inject_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_MEMORY_GROUPS: return
    now = datetime.datetime.now(TZ_CN)
    _stamp_trigger(event)
    if 0 <= now.hour < 6:
        grant_safety_pass(5)
        await inject_cmd.finish(random.choice(["（呼……呼……完全没听见你在说什么……）zzZ", "……吵死了……明天再说……"]))
    raw_text = args.extract_plain_text().strip()
    if not raw_text: await inject_cmd.finish("请告诉我时间和内容，例如：接下来的事是 10m 外面下大雨了")
    duration, content = parse_duration_and_content(raw_text)
    is_limited = False
    if duration > 7200: duration, is_limited = 7200, True
    expire_time = time.time() + duration
    mem = get_user_memory(get_memory_key(event))
    mem.setdefault("temp_implants", []).append({"content": content, "expire_at": expire_time})
    end_time = datetime.datetime.fromtimestamp(expire_time, TZ_CN).strftime('%H:%M')
    msg = f"✅ 记忆追加成功！(当前共 {len(mem['temp_implants'])} 条)\n内容：{content}\n"
    msg += f"⚠️ 时间过长，已强制限制为 2 小时（直到 {end_time} 失效）" if is_limited else f"直到 {end_time} 失效"
    grant_safety_pass(5)
    await inject_cmd.finish(msg)


clear_cmd = on_command("清除记忆", aliases={"忘记记忆", "clear_memory"}, priority=5, block=True)
@clear_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_MEMORY_GROUPS: return
    get_user_memory(get_memory_key(event))["temp_implants"] = []
    _stamp_trigger(event)
    await clear_cmd.finish("（揉了揉太阳穴）……奇怪，刚才那种感觉消失了。")


clear_temp_memory = on_command("清除临时记忆", priority=5, block=True)
@clear_temp_memory.handle()
async def _(event: Event, arg: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_MEMORY_GROUPS: return
    mem = get_user_memory(get_memory_key(event))
    arg_text = arg.extract_plain_text().strip()
    _stamp_trigger(event)
    if not arg_text:
        mem["temp_implants"].clear()
        await clear_temp_memory.finish("已清除所有临时记忆")
    try:
        idx = int(arg_text) - 1
        if 0 <= idx < len(mem["temp_implants"]):
            removed = mem["temp_implants"].pop(idx)
            await clear_temp_memory.finish(f"已清除临时记忆：{removed['content']}")
    except Exception: await clear_temp_memory.finish("请输入正确的索引数字。")


forget_cmd = on_command("遗忘", aliases={"删除记忆"}, priority=5, block=True)
@forget_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_MEMORY_GROUPS: return
    user_mem = get_user_memory(get_memory_key(event))
    facts = user_mem.get("long_term_facts", [])
    arg_text = args.extract_plain_text().strip()
    _stamp_trigger(event)
    if not arg_text: await forget_cmd.finish("需要让小彰忘掉什么？例如：遗忘 1")
    if arg_text in ["全部", "所有", "all"]:
        user_mem["long_term_facts"] = []
        save_memory()
        await forget_cmd.finish("（揉了揉太阳穴）……感觉有什么从脑子里消失了。")
    try:
        index = int(arg_text) - 1
        if 0 <= index < len(facts):
            facts.pop(index)
            save_memory()
            await forget_cmd.finish("OK，已经忘了这件事。")
    except Exception: await forget_cmd.finish("请输入正确的数字序号。")


# --- 终极上下文重置指令 ---
reset_cmd = on_command("重置对话", aliases={"忘了刚才", "重启会话", "reset", "清空上下文", "忘掉刚才"}, priority=5, block=True)
@reset_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ: return
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS: return

    user_mem = get_user_memory(get_memory_key(event))
    user_mem["history"] = []
    user_mem["temp_implants"] = []
    save_memory()
    _stamp_trigger(event)

    group_id = getattr(event, 'group_id', None)
    if group_id:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE group_id=?", (str(group_id),))
            conn.commit()
            conn.close()
            logger.info(f"💣 已彻底炸毁群 {group_id} 的数据库背景流记忆！")
        except Exception as e:
            logger.error(f"清理数据库背景流失败: {e}")

    await reset_cmd.finish("（猛地晃了晃脑袋，眼神恢复了清明）……啧，刚才好像走神了。前面说到哪了？")


# --- 热更新指令 ---
reload_cmd = on_command("重载配置", aliases={"热更新", "reload_data"}, priority=5, block=True)
@reload_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ: return
    arg = args.extract_plain_text().strip().lower()
    _stamp_trigger(event)
    if arg in ("persona", "人设"):
        result = reload_persona()
        chars = len(result)
        await reload_cmd.finish(f"✅ 人设文件已重载（{chars} 字）")
    elif arg in ("assets", "数据", "json"):
        reload_assets()
        await reload_cmd.finish("✅ 所有 JSON 数据文件已重载（reactions / prompts / director / routine / songs / relationships / scripts）")
    elif arg in ("", "all", "全部"):
        reload_assets()
        result = reload_persona()
        await reload_cmd.finish(f"✅ 全部配置已重载\n· JSON 数据文件（8 项）\n· 人设文件（{len(result)} 字）")
    else:
        await reload_cmd.finish("用法：重载配置 [persona|assets|全部]\n· persona — 重载 akito_persona.txt\n· assets — 重载全部 JSON 数据文件\n· 全部（默认）— 两者都重载")
