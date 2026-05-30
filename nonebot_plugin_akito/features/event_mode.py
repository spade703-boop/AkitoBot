"""WL2 世界线：超管开启 / 关闭 WL2 剧情线（临时设定植入到会话记忆）。"""

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from ..core import ALLOWED_CHAT_GROUPS, SUPERUSER_QQ, get_memory_key, get_user_memory, load_prompt_template, save_memory

# --- 1. 开启 WL2 剧情线 ---
enable_wl2_cmd = on_command("开启WL2模式", priority=5, block=True)
@enable_wl2_cmd.handle()
async def _(event: Event):
    if event.get_user_id() != SUPERUSER_QQ:
        await enable_wl2_cmd.finish("（冷漠地瞥了你一眼）……少命令我。")
        return

    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS: return

    wl2_content = load_prompt_template("wl2_persona.txt").strip()
    if not wl2_content:
        await enable_wl2_cmd.finish("❌ 找不到 wl2_persona.txt")
    mem = get_user_memory(get_memory_key(event))

    mem["temp_implants"] = [i for i in mem.get("temp_implants", []) if i.get("id") != "WL2"]
    mem["temp_implants"].append({
        "id": "WL2",
        "content": wl2_content,
        "expire_at": 4070908800.0
    })
    save_memory()
    await enable_wl2_cmd.finish("【 世界线变更完毕。】")


# --- 2. 关闭 WL2 剧情线 ---
disable_wl2_cmd = on_command("关闭WL2模式", priority=5, block=True)
@disable_wl2_cmd.handle()
async def _(event: Event):
    if event.get_user_id() != SUPERUSER_QQ:
        return

    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS: return
    mem = get_user_memory(get_memory_key(event))
    mem["temp_implants"] = [i for i in mem.get("temp_implants", []) if i.get("id") != "WL2"]
    save_memory()
    await disable_wl2_cmd.finish("【已脱离 WL2 梦境，回到了正常的现实。】")
