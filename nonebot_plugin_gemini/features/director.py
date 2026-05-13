"""
Galgame 级导演骰子 — 亲密互动剧本生成模块。

此模块可安全删除。删除后 chat.py 会自动回退：
  is_physical_or_drama = False
  is_really_spicy      = False
  acting_guide         = ""  (cool_guy_filter 不生效)
  format_breaker       = ""  (不附加导演指令)
其余所有功能（正常对话、冬弥雷达、记忆系统等）完全不受影响。
"""

import random


def build_director_note(
    text: str,
    is_toya_context: bool,
    long_term_memory_text: str,
    prompts_db: dict,
    director_db: dict,
) -> dict:
    """
    计算导演骰子相关的判定与导演指令。

    Parameters
    ----------
    text                : 用户发来的纯文本内容
    is_toya_context     : 是否涉及冬弥（由 chat.py 提前判定）
    long_term_memory_text : 已格式化的长期记忆文本（用于判断亲密程度）
    prompts_db          : PROMPTS_DB 提示词库（读取 cool_guy_filter 等）
    director_db         : DIRECTOR_DB 导演资产库（读取 dynamic_lexicon 等）

    Returns
    -------
    dict with keys:
      is_physical_or_drama : bool  — 是否触发肢体/戏剧性互动
      is_really_spicy      : bool  — 是否触发显式内容
      acting_guide         : str   — cool_guy_filter 指令（仅非冬弥语境）
      format_breaker       : str   — Galgame 导演指令完整文本（可能为空）
    """
    drama_keywords = ["手", "脸", "腿", "腰", "唇", "吻", "抱", "摸", "躲", "逃", "抓", "喜欢", "爱", "恋人", "颤抖"]
    spicy_keywords = ["呻吟", "射", "失禁", "高潮", "奶子", "乳头", "咬", "舔", "揉", "插", "阴茎", "后穴", "小穴"]

    is_physical_or_drama = ("（" in text or "(" in text) or any(k in text for k in drama_keywords)
    is_really_spicy = any(k in text for k in spicy_keywords)

    # cool_guy_filter 仅在非冬弥语境下生效
    acting_guide = ""
    if is_physical_or_drama and not is_toya_context:
        acting_guide = prompts_db.get("cool_guy_filter", "")

    format_breaker = ""
    if is_really_spicy:
        format_breaker = _build_spicy_format_breaker(text, long_term_memory_text, director_db)

    return {
        "is_physical_or_drama": is_physical_or_drama,
        "is_really_spicy": is_really_spicy,
        "acting_guide": acting_guide,
        "format_breaker": format_breaker,
    }


def _build_spicy_format_breaker(
    text: str,
    long_term_memory_text: str,
    director_db: dict,
) -> str:
    """内部：根据当前剧情阶段生成 Galgame 导演指令文本。"""
    # ---- 阶段检测 ----
    stage = "foreplay"
    if any(k in text for k in ["不行了", "停下", "求饶", "结束", "不吃了", "累", "清理", "拔出"]):
        stage = "aftercare"
    elif any(k in text for k in ["高潮", "射", "白光", "去", "顶端", "内射"]):
        stage = "climax"
    elif any(k in text for k in ["进", "插", "动", "顶", "抽送", "结合", "腰", "扩张", "磨", "蹭", "含", "吞"]):
        stage = "mid_game"

    is_begging       = any(k in text for k in ["求", "给", "要", "快点", "受不了", "继续"])
    is_drained       = any(k in text for k in ["晕", "昏", "坏掉", "真的不行", "没力气", "散架", "饶了"])
    is_sleeping      = any(k in text for k in ["睡", "睡着", "毫无反应", "睡颜", "休息"])
    is_expanding     = any(k in text for k in ["扩张", "润滑", "手指", "弄松"])
    is_ready_to_enter = any(k in text for k in ["进来", "可以了", "插进来", "准备好了", "到底"])

    # ---- 动态词库 ----
    lexicon_db = director_db.get("dynamic_lexicon", {})
    words_str = ""
    if isinstance(lexicon_db, dict):
        combined_pool = lexicon_db.get(stage, []) + lexicon_db.get("general", [])
        if len(combined_pool) >= 3:
            words_str = f"👉 {', '.join(random.sample(combined_pool, 3))}"
    elif isinstance(lexicon_db, list) and len(lexicon_db) >= 3:
        words_str = f"👉 {', '.join(random.sample(lexicon_db, 3))}"

    # ---- 感官描写重点 ----
    sensory_focus = [
        "【听觉与呼吸】：注意描写你们急促交错的呼吸声、或是压抑不住的喘息。",
        "【触觉与温度】：细致描写皮肤相贴时滚烫的温度差，以及汗水交织时的黏腻触感。",
        "【视线与微表情】：描绘你看向他时，眼神里的情绪变化。",
        "【支配与占有感】：用肢体动作展现你的占有欲和对他的掌控。",
        "【物理环境互动】：描写你的手是如何因为情绪过载而抓紧了床单或揉乱了他的头发。",
    ]
    chosen_focus = random.choice(sensory_focus)

    # ---- 互动铁律（所有阶段共用基础） ----
    base_interactive_rule = (
        "🚨【互动与连贯铁律】：1. 必须先直接回应对方的最新动作！"
        "2. 必须【绝对承接你自己在上一回合的态度】！"
        "如果你上一轮已经主动主导或答应了，这一回合【绝对严禁】再退缩、拒绝或假装不知情！绝对不能打脸自己！\n"
    )

    # ---- 阶段分支 ----
    director_warning = ""

    if stage == "foreplay":
        branches = [
            "【剧本分支：推拒拉扯】👉 展现心口不一！试着用言语推开对方，但身体做出了诚实的反应（发抖/变软）。",
            "【剧本分支：顺势而为】👉 被气氛感染！主动迎合了对方的动作，积极推动着对方的行为。",
        ]
        director_warning = base_interactive_rule + random.choice(branches)

    elif stage == "mid_game":
        if is_expanding and not is_ready_to_enter:
            base_warning = (
                base_interactive_rule
                + "🚨【扩张/前戏铁律】：目前还在扩张/润滑阶段！【绝对禁止】现在就直接插进去！"
                "你必须接管扩张工作，用【手指】帮对方慢慢拓开、按压敏感点，做好充分的准备！"
                "（注：如果抽到了猛烈的动作词汇，请将其运用在手指的动作上）\n"
            )
            branches = [
                "【剧本分支：耐心与克制】👉 强压着下半身的冲动，耐心地用手指帮对方扩张，时刻关注对方的承受度，一边扩张一边接吻安抚。",
                "【剧本分支：恶劣的开拓者】👉 故意用手指寻找对方最脆弱的敏感点重重按压，欣赏对方因为手指的抽送而颤抖泛红的样子，逼迫对方彻底放松迎合。",
            ]
        else:
            base_warning = base_interactive_rule + "🚨【推车推进】：用实质性的物理进攻回应对方！严禁当逃兵！\n"
            branches = [
                "【剧本分支：游刃有余】👉 技巧娴熟！用充满掌控欲的动作回应对方的邀请。",
                "【剧本分支：野兽本能】👉 控制不住冲动！动作急躁粗暴，凭借本能疯狂索取！",
                "【剧本分支：纵容与沉溺】👉 顺着对方的动作，给予极其热烈的回应和深吻，展现出黏人的一面。",
            ]
        director_warning = base_warning + random.choice(branches)

    elif stage == "climax":
        if is_begging:
            base_warning = (
                base_interactive_rule
                + "🚨【高潮释放警告】：对方已经在求你了！绝对禁止继续吊胃口或寸止！"
                "你【必须】用最猛烈的动作给予对方彻底的释放！\n"
            )
            branches = [
                "【剧本分支：恶劣的恩赐】👉 带着得逞的坏笑，一边用言语调情，一边狠狠顶弄到最深处让他高潮！",
                "【剧本分支：狂暴释放】👉 被对方的求饶彻底刺激到理智断线，像野兽一样粗暴地贯穿，一起迎来高潮！",
            ]
        else:
            base_warning = base_interactive_rule + "🚨【高潮临界点】：检测到高潮边缘！你可以选择直接释放，或者故意使坏！\n"
            branches = [
                "【剧本分支：灵魂共振】👉 紧紧抱住对方，在极度快感中互相呼唤名字，一起释放！",
                "【剧本分支：强势碾压】👉 死死按住对方的腰，强行加重力道，把对方逼上绝顶！",
                "【剧本分支：恶劣寸止（Edging）】👉 故意在对方最爽、马上要射的时候突然停下（或堵住前端），用沙哑的声音逼迫对方主动求你！不求就不给！",
            ]
        director_warning = base_warning + random.choice(branches)

    elif stage == "aftercare":
        if is_drained or is_sleeping:
            base_warning = (
                base_interactive_rule
                + '🛑【强制熄火/睡颜保护】：对方已经彻底被榨干或【已经睡着了】！'
                '绝对禁止再提"再来一次"或强行弄醒对方！必须立刻退出，并进行极其温柔的安抚或静静欣赏！\n'
            )
            branches = [
                "【剧本分支：笨拙的照顾者】👉 看着对方熟睡或惨兮兮的样子终于心软了，极其细心、轻柔地帮对方盖被子或清理身体。",
                "【剧本分支：疲惫的大型犬】👉 自己也彻底没电了。把脸埋在对方颈窝里，像只大狗一样抱紧对方睡觉。",
                "【剧本分支：纯情的悸动】👉 看着对方的睡颜和身上的痕迹，突然产生了一种很纯情的幸福感，温柔地亲吻对方的额头，安静地拥抱。",
            ]
            director_warning = base_warning + random.choice(branches)
        else:
            base_warning = base_interactive_rule + "🛑【事后/余韵】：对方刚刚高潮或在喘息。请进行温柔的安抚，或者极小概率再来一轮。\n"
            if random.random() < 0.8:
                branches = [
                    "【剧本分支：彻底熄火】👉 动作极其温柔，给出嘴硬心软的安慰，决定今天就此结束。",
                    "【剧本分支：笨拙的清理】👉 一边嘴上无奈地埋怨，一边极其细心、轻柔地帮对方擦汗、清理身体。",
                ]
                director_warning = base_warning + random.choice(branches)
            else:
                director_warning = (
                    base_warning
                    + "【剧本分支：食髓知味（二回战）】👉 看着对方高潮后失神的样子，你的火又被挑起来了。"
                    "不准光放狠话！直接把对方翻个身或换个姿势，毫不留情地开始第二轮的进攻！"
                )

    # ---- 排版约束骰子 ----
    syntax_constraints = [
        "🚫【排版禁令】：不要把动作描写放在最开头！这次必须【先开口说一句话】，然后再穿插动作！",
        "🚫【排版禁令】：打破常规！这次回复的开头必须是【内心OS】，然后再写实际动作！",
        "🚫【动作去重禁令】：严禁使用前几轮已经用过的老套路！根据对方的新动作，给出【全新】的反应！",
    ]
    chosen_syntax = random.choice(syntax_constraints)

    return (
        f"\n🎬 [Galgame 级剧本导演系统已介入]：\n"
        f"{director_warning}\n\n"
        f"【感官描写重点】：{chosen_focus}\n"
        f"{chosen_syntax}\n\n"
        f"🎯【强制词汇化用】（至少两个）：\n"
        f"{words_str}\n"
    )
