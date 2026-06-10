"""WL2 世界线：超管开启 / 关闭 WL2 剧情线（临时设定植入到会话记忆）。"""

from nonebot import on_command
from nonebot.adapters import Event

from ..core import ALLOWED_CHAT_GROUPS, SUPERUSER_QQ, get_memory_key, get_user_memory, load_prompt_template, save_memory


def _is_allowed_wl2_event(event: Event) -> bool:
    """Return True when the event is allowed to operate on WL2 mode."""
    group_id = getattr(event, "group_id", None)
    return group_id is None or group_id in ALLOWED_CHAT_GROUPS


def _upsert_wl2_implant(mem: dict, wl2_content: str, expire_at: float = 4070908800.0) -> None:
    """Replace any existing WL2 implant and append the current one."""
    mem["temp_implants"] = [item for item in mem.get("temp_implants", []) if item.get("id") != "WL2"]
    mem["temp_implants"].append({
        "id": "WL2",
        "content": wl2_content,
        "expire_at": expire_at,
    })


def _remove_wl2_implant(mem: dict) -> int:
    """Remove WL2 implants and return how many were removed."""
    original_len = len(mem.get("temp_implants", []))
    mem["temp_implants"] = [item for item in mem.get("temp_implants", []) if item.get("id") != "WL2"]
    return original_len - len(mem["temp_implants"])


# --- 1. 开启 WL2 剧情线 ---
enable_wl2_cmd = on_command("开启WL2模式", priority=5, block=True)
@enable_wl2_cmd.handle()
async def _(event: Event):
    if event.get_user_id() != SUPERUSER_QQ:
        await enable_wl2_cmd.finish("（冷漠地瞥了你一眼）……少命令我。")
        return

    if not _is_allowed_wl2_event(event): return

    wl2_content = load_prompt_template("wl2_persona.txt").strip()
    if not wl2_content:
        await enable_wl2_cmd.finish("❌ 找不到 wl2_persona.txt")
    mem = get_user_memory(get_memory_key(event))

    _upsert_wl2_implant(mem, wl2_content)
    save_memory()
    await enable_wl2_cmd.finish("【 世界线变更完毕。】")


# --- 2. 关闭 WL2 剧情线 ---
disable_wl2_cmd = on_command("关闭WL2模式", priority=5, block=True)
@disable_wl2_cmd.handle()
async def _(event: Event):
    if event.get_user_id() != SUPERUSER_QQ:
        return

    if not _is_allowed_wl2_event(event): return
    mem = get_user_memory(get_memory_key(event))
    _remove_wl2_implant(mem)
    save_memory()
    await disable_wl2_cmd.finish("【已脱离 WL2 梦境，回到了正常的现实。】")
