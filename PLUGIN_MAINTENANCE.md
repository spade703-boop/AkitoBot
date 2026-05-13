# nonebot_plugin_akito — 维护手册

**角色**：东云彰人（Project SEKAI 同人 AI）  
**框架**：NoneBot2 + OneBot V11  
**AI 后端**：DeepSeek API / 智谱 GLM-4V（视觉）/ Tavily（搜索）  
**文档更新**：2026-04-30

---

## 目录结构

```
nonebot_plugin_akito/
├── __init__.py               # 插件入口：元数据 + require() + 导入三大子包
├── core/                     # 共享基础层（无副作用，可被任意模块导入）
│   ├── __init__.py           # 统一 re-export，外部一律 from ..core import ...
│   ├── constants.py          # 常量 / API 客户端 / 群组白名单（从 .env 读取密钥）
│   ├── memory.py             # 长期记忆 JSON 读写 + SQLite 群聊上下文
│   ├── data.py               # JSON 数据文件加载（reactions/prompts/routine 等）
│   ├── life_state.py         # 彰人状态机（routine 缓存 / 节日 buff / 安全期管理）
│   ├── api.py                # DeepSeek / 智谱 / Tavily API 封装
│   ├── context.py            # Prompt 组装（人设 / 剧本示例 / 歌曲记忆 / 关系链）
│   └── time_awareness.py     # 时间流逝感知（追踪群对话 gap，注入时段切换提示）
├── handlers/                 # 主聊天处理层（响应群消息）
│   ├── __init__.py
│   ├── chat.py               # 主对话引擎（ReAct Agent 循环 + Python 端 MVVM 排版）
│   ├── commands.py           # 记忆管理指令（查看/植入/清除/遗忘/重置/热更新）
│   └── reactions.py          # 被动反应（冬弥雷达 / 戳一戳 / 深夜自言自语）
└── features/                 # 独立功能模块
    ├── __init__.py
    ├── impression.py         # 群印象 + 随机插嘴（AutoChat）
    ├── gallery.py            # 相册图库指令
    ├── director.py           # Galgame 级导演骰子（可安全删除）
    ├── snowy.py              # PJSK 榜线预测（sn预测/cn预测）
    ├── verify.py             # 新人审核名单管理
    ├── random_paro.py        # 派生抽取器（CP 同人灵感配对）
    ├── scheduled.py          # 定时任务（早晚安 / 过期记忆清理）
    └── event_mode.py         # WL2 世界线剧情模式开关
```

---

## 依赖关系图

```
constants.py ←────────────────────────────────────────────┐
     ↓                                                     │
memory.py      (← constants)                              │
data.py        (无内部依赖)                               │ core/__init__.py
life_state.py  (← constants, data)                        │ 统一对外暴露所有符号
api.py         (← constants)                              │
context.py     (← data, api)                              │
time_awareness.py (← constants, data, life_state)         │
     └──────────────────────────────────────────────────── ┘
                           ↓
             handlers/ 和 features/ 均通过
             `from ..core import ...` 访问
```

**导入层级规则**：

- `core/` 子模块只能用相对导入 `.` 访问同层文件，**严禁**向上引用 `handlers/` 或 `features/`
- `handlers/` 和 `features/` 均使用 `from ..core import ...`（两个点 = 上一级包）
- `handlers/` 和 `features/` 之间**无互相引用**
- `features/snowy.py` 和 `features/verify.py` 无任何内部依赖，完全独立
- `features/director.py` 仅被 `handlers/chat.py` 调用，可整体删除（chat.py 有安全降级）

---

## 配置与密钥管理

**所有密钥和敏感 ID 统一在 `.env` 中管理**，`core/constants.py` 通过 `os.environ.get()` 读取：

```ini
# .env
DEEPSEEK_API_KEY=sk-xxx
TAVILY_API_KEY=tvly-xxx
ZHIPU_API_KEY=xxx

SUPERUSER_QQ=123456789    # 重置对话 / WL2 模式的授权 QQ
TOYA_QQ_ID=987654321      # 冬弥本人的 QQ，影响 CP 模式触发
```

> ⚠️ **修改密钥或管理员 QQ 只需改 `.env`，无需动代码。重启后生效。**

---

## core/ — 基础层

### constants.py

无内部依赖。从 `.env` 读取密钥，定义全局常量。

| 变量 | 来源 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | `.env` | DeepSeek 密钥 |
| `TAVILY_API_KEY` | `.env` | Tavily 搜索密钥 |
| `ZHIPU_API_KEY` | `.env` | 智谱 GLM 密钥 |
| `SUPERUSER_QQ` | `.env` | 超级用户 QQ |
| `TOYA_QQ_ID` | `.env` | 冬弥 QQ 号（CP 模式判断） |
| `client` | — | DeepSeek AsyncOpenAI 客户端 |
| `vision_client` | — | 智谱 GLM 视觉客户端 |
| `TZ_CN` | — | UTC+8（北京时间，用于 routine/睡眠判定） |
| `TZ_JST` | — | UTC+9（东京时间，用于彰人对话报时） |
| `MAX_HISTORY_LEN` | — | 对话历史最大条数（当前 40） |
| `ALLOWED_CHAT_GROUPS` | 代码 | 允许主对话的群列表 |
| `ALLOWED_CP_GROUPS` | 代码 | 允许 CP 相关功能的群列表 |
| `ALLOWED_MEMORY_GROUPS` | 代码 | 允许使用记忆指令的群列表 |
| `TARGET_GROUPS` | 代码 | 定时推送目标群列表 |
| `GROUP_IMAGE_PERMISSIONS` | 代码 | 各群的图库分类权限 `{group_id: ["all"]}` |

### memory.py

管理 `data/akito_memories.json`（运行时内存 `MEMORY_DB`）。模块加载时自动调用 `load_memory()`。

| 函数/变量 | 说明 |
|-----------|------|
| `MEMORY_DB: dict` | 全部会话记忆的内存字典 |
| `get_memory_key(event)` | 从 Event 生成 `group_xxx` 键（**按群而非按人**，群内所有用户共享记忆） |
| `get_user_memory(key)` | 获取/初始化某会话的记忆字典 |
| `save_memory()` | 原子写入（先写 .tmp 再 os.replace） |
| `get_group_context(gid, limit)` | 从 SQLite 读取最近 N 条群聊上下文字符串（bot 消息去重上限 2 条） |

**记忆结构**（每个 `group_xxx` 键下）：
```python
{
  "history": [...],           # 对话历史（list of {"role": ..., "content": ...}）
                              # assistant 条目以 JSON 字符串存储：{"inner_os": ..., "reply": ...}
  "temp_implants": [...],     # 临时记忆，含 expire_at 字段
  "long_term_facts": [...]    # 长期事实记忆（字符串列表）
}
```

> ⚠️ **history 格式注意**：assistant 条目存的是 `{"inner_os": ..., "reply": ...}` 两字段 JSON 字符串，
> 但 system prompt 要求模型输出 `{"inner_os": ..., "action": ..., "dialogue": ...}` 三字段。
> 两者格式不一致，chat.py 解析时同时兼容 `reply` 和 `dialogue` 字段名。

### data.py

模块加载时自动执行所有 `load_json_file()` 调用。文件不存在时使用内联默认值，不会崩溃。

| 变量 | 对应文件 | 说明 |
|------|----------|------|
| `SCRIPT_DB` | `akito_scripts.json` | 台词剧本库（list），每条含 `context`/`dialogue` |
| `REACTIONS_DB` | `akito_reactions.json` | 反应资源包：complaints / behavior_seeds / greetings / fallback_poke / sleep_relation / sleep_search 等 |
| `PROMPTS_DB` | `akito_prompts.json` | Prompt 模板库（dict）：system_header / schema 字段 / 各类 acting_guide 等 |
| `DIRECTOR_DB` | `akito_director.json` | 导演骰子资产：toya_directions / dynamic_lexicon |
| `DAILY_ROUTINE` | `akito_routine.json` | 每日状态日程，键为时间段（每条含 `status` 和 `poke` 字段） |
| `WL2_ROUTINE` | `wl2_routine.json` | WL2 世界线状态 |
| `SONG_DATA` | `akito_songs.json` | 歌曲背景知识 |
| `RELATIONSHIP_DATA` | `akito_relationships.json` | 人物关系档案（含 `keywords` 白名单） |
| `PJSK_KNOWLEDGE_BASE` | `pjsk_knowledge.json` | PJSK 黑话知识库（拼接为字符串） |

**热更新**：`reload_assets()` 用 `.clear()` + `.update()` / `.extend()` 原地修改所有全局变量，
已持有引用的模块无需重新 import，即时生效。通过 `重载配置 assets` 指令触发。

> ⚠️ **`akito_prompts.json` 编辑注意**：文件内容为 JSON 字符串，值中不能出现裸 ASCII 双引号 `"`。
> 需用 `\"` 转义，或改用中文书名号 `「」` 代替。否则加载失败时会静默回落到代码内默认值。

### life_state.py

彰人的运行时状态机。`AKITO_STATUS` 是可变 dict，跨模块引用安全。

```python
AKITO_STATUS = {
    "current_key": "",           # 当前时段 key（如 "noon_weekday"）
    "cached_content": "",        # 当前 routine 条目（dict，含 status/poke 字段）
    "expire_time": 0.0,          # 缓存过期时间戳（30 分钟有效期）
    "event_history": [],         # 本时段已出现过的 routine 条目（防重复）
    "last_trigger_user": "",     # 上一条 chat 回复由谁触发
    "last_superuser_trigger_time": {}  # 超管在各群的最后触发时间 {group_id: timestamp}
}
```

**两个全局浮点量必须通过函数访问**（Python 不可变量跨模块赋值陷阱）：

```python
# ✅ 正确
grant_safety_pass(seconds)     # 设置安全期（定时推送/指令回复前调用）
get_safe_until()               # 读取安全期截止时间戳
set_last_complaint(time.time()) # 记录深夜抱怨时间
get_last_complaint()           # 读取上次抱怨时间戳

# ❌ 错误——导入后直接赋值只改了本模块的局部绑定，其他模块看不到
from ..core import AKITO_SAFE_UNTIL
AKITO_SAFE_UNTIL = time.time() + 10   # 无效！
```

| 函数 | 说明 |
|------|------|
| `get_daily_activity(hour, weekday)` | 返回当前时段状态字符串，内置 30 分钟缓存 + **时段变更自动清缓存**。**任何需要 routine 的地方都应无条件调用此函数**，不要在外部判断 cached_content 是否存在后跳过调用 |
| `check_sleep_status(msg)` | 判断是否深夜并返回 `(should_ignore, instruction)` |
| `get_festival_buff(date_obj)` | 返回今日节日 Prompt 片段 |
| `get_morning_run_buff(hour)` | 返回晨跑状态 Prompt（6 点整段生效） |
| `parse_duration_and_content(text)` | 解析 `"10m 下雨了"` → `(600, "下雨了")` |
| `check_img_permission(group_id, category)` | 判断该群是否有某分类图库权限 |

> ⚠️ **`get_daily_activity()` 的正确调用姿势**：
> 内部先算 key，若 key 与缓存不同则清空缓存，再根据过期情况决定是否重新抽取。
> 调用方不应在外部写 `if not cached_content: get_daily_activity(...)` ——这绕过了时段变更检测，
> 会导致跨时段的脏缓存被持续复用（例如凌晨的 late_night 状态在白天仍然生效）。

### api.py

| 函数 | 说明 |
|------|------|
| `call_deepseek_api(messages, model, force_json)` | 标准调用，15s 超时熔断，失败返回中文提示字符串 |
| `call_deepseek_api_agent(messages, tools, model)` | 带 Function Calling 的 Agent 调用，返回完整 `ChatCompletionMessage`，失败返回 `None` |
| `smart_search(query)` | Tavily 搜索，返回摘要字符串，失败返回空字符串 |
| `describe_image(bytes)` | 智谱 GLM-4V 图片分析，返回结构化描述文本 |
| `to_image_data(image)` | 从 AlcImage 获取原始字节（支持 raw/path/url 三种来源） |

> `call_deepseek_api_agent` 专供 `chat.py` 的 ReAct 循环，其他调用方用 `call_deepseek_api`。

### context.py

| 函数 | 说明 |
|------|------|
| `get_base_persona()` | 读取 `data/akito_persona.txt` 人设文本 |
| `get_random_examples(n)` | 从 `SCRIPT_DB` 随机抽取 n 条台词示例注入 Prompt |
| `get_song_memories()` | 将 `SONG_DATA` 格式化为背景知识条目，每次对话静态注入 |
| `get_hybrid_relationship(text)` | 本地关键词白名单扫描 + 可选联网补充，返回 Prompt 片段 |
| `reload_persona()` | 重新读取 `akito_persona.txt`，返回新内容（`重载配置 persona` 触发） |

### time_awareness.py

追踪每个群的"最后一次 bot 回复时间 + routine 快照"，在下次回复时按 gap 大小注入时间感知文本。

| 函数 | 说明 |
|------|------|
| `record_bot_response(group_id)` | bot 发完回复后调用，持久化时间戳和当前 routine 快照 |
| `build_time_gap_prompt(group_id)` | 构建注入文本（gap < 30min 返回空字符串） |

**注入规则**：

| 条件 | 行为 |
|------|------|
| gap < 30 分钟 | 不注入（正常接话） |
| gap ≥ 30 分钟，同一时段 | 轻提示：对话已结束，不续接上次话题 |
| gap ≥ 30 分钟，时段变化 1 次 | 中提示：场景已切换，自然开启新话题 |
| gap ≥ 8 小时，或时段变化 ≥ 2 次 | 强提示：场景重置，旧话题以「那会儿」带过 |

强/中提示触发时，`chat.py` 同步将 `history` 压缩为背景摘要注释并清空，防止模型续接旧话题。

持久化文件：`data/last_interactions.json`

---

## handlers/ — 指令响应层

### chat.py

主对话引擎。触发条件：消息以 `TRIGGER_NAMES`（`"小彰"` / `"东云小彰"`）开头，且发自 `ALLOWED_CHAT_GROUPS`。

**完整对话流程**：

```
1. 回复溯源        提取 Reply 引用的原始文本和图片
2. 文本/视觉解析   分离纯文本和图片；图片调用 GLM-4V 识别
3. 并发保护        asyncio.Lock（per 会话键）防止同一会话并发
4. 睡眠检测        check_sleep_status → 深夜可能忽略或返回睡觉提示
5. Prompt 组装     人设 + 时间感知 + 临时记忆 + 关系链 + 搜索结果 +
                   剧本示例 + 歌曲知识 + 导演骰子 + schema 格式指令
6. ReAct Agent     见下方
7. JSON 解析       提取 inner_os / action / dialogue；两层正则救援兜底
8. 内联动作回收    action 为空时尝试从 dialogue 开头提取「(动作)」
9. MVVM 排版       Python 端随机拼装最终文本（动作前置/后置/省略）
10. 长期记忆提取   检测 [[记下:xxx]] 写入 long_term_facts
11. 复读检测       与近期回复比对；相同则重新生成（注入去重指令）
12. 更新上下文     history 追加；超过 MAX_HISTORY_LEN 截断头部
13. 打字延迟       random(0.8, 2.5)s + 字数×0.12s（上限 7.5s）
14. 发送 & 记录    smart_finish 发送；record_bot_response 更新时间戳
```

**ReAct Agent 循环（Step 6）**：

```
有图片 ──────────────────────────────────────→ call_deepseek_api（直接生成）
无图片 → call_deepseek_api_agent（带 AGENT_TOOLS）
           ├─ 返回 tool_calls → 执行 smart_search → 塞回 messages → call_deepseek_api
           ├─ 返回普通内容   → 直接使用 agent_message.content
           └─ 返回 None（超时）→ call_deepseek_api（降级兜底）
```

**JSON 解析 + 两层救援（Step 7）**：

```
json.loads() 成功 → 提取 dialogue / action / inner_os，正常走排版
           失败 → 救援一：正则匹配 "dialogue" 或 "reply" key 的值
                        失败 → 救援二：定位 inner_os 值结束位置，
                                        提取其后剩余内容（处理 key 名幻觉场景）
                                        失败 → result = raw_result（原样发送，日志 WARNING）
```

**MVVM 排版逻辑（Step 9）**：

LLM 输出三字段，Python 端决定最终格式：
- 有 `action` 且为**交互/指向类**（递/指/看/拿/接/扔/抱/拉）→ 强制前置
- 有 `action` 且为**情绪/状态类** → 随机：前置 15% / 后置 15% / 省略动作 20% / 纯文本 50%
- `action` 为空 → 尝试从 `dialogue` 开头提取 `（动作）` 后交给上述逻辑
- `is_toya_context` 为 True 时去掉权重，完全随机

**关键本地函数**：

| 函数 | 说明 |
|------|------|
| `starts_with_trigger(event)` | on_message 触发规则 |
| `smart_finish(matcher, result)` | 统一发送出口：空字符串 / strip 后为空均不发；含图片 URL 时组装 UniMessage；超 800 字渲染图片 |
| `get_session_lock(key)` | 返回该会话键对应的 asyncio.Lock |
| `AGENT_TOOLS` | Function Calling 工具描述常量（模块级） |

### commands.py

全部指令受 `ALLOWED_MEMORY_GROUPS` 白名单保护。`_stamp_trigger(event)` 为统一前置函数，所有指令处理器在实际逻辑前调用，负责：
1. 记录触发者身份到 `AKITO_STATUS["last_trigger_user"]`
2. 调用 `grant_safety_pass(5)` 防止指令回复触发深夜抱怨
3. 若触发者为超管，更新 `AKITO_STATUS["last_superuser_trigger_time"][group_id]`（per-group dict）

| 指令 | 别名 | 权限 | 功能 |
|------|------|------|------|
| `查看记忆` | 记住了啥 / 当前状态 / 状态 | 普通用户 | 列出当前生效的临时设定和剩余时间 |
| `查看长期记忆` | 小彰都记住了什么 / 记忆列表 | 普通用户 | 列出长期事实记忆条目 |
| `植入记忆 [时长] [内容]` | 接下来的事是 / 记住 | 普通用户 | 注入临时设定，最长 2 小时 |
| `清除记忆` | 忘记记忆 | 普通用户 | 清空所有临时设定 |
| `清除临时记忆 [序号]` | — | 普通用户 | 按序号或全量清除 |
| `遗忘 [序号/全部]` | 删除记忆 | 普通用户 | 删除长期记忆条目 |
| `重置对话` | 忘了刚才 / 清空上下文 等 | **SUPERUSER_QQ** | 清空 history + temp_implants + SQLite 背景流 |
| `重载配置 [persona\|assets\|全部]` | 热更新 | **SUPERUSER_QQ** | 热更新人设文件和/或 JSON 数据文件，无需重启 |

### reactions.py

| 处理器 | 触发 | 说明 |
|--------|------|------|
| `toya_status_cmd` | "冬弥呢"等 5 个别名 | 结合当前 routine 生成冬弥位置描述；WL2 模式下切换为冷漠台词；每次**无条件调用 `get_daily_activity()`** |
| `poke` | 戳一戳通知（PokeNotifyEvent） | 按时段返回反应；深夜 0-6 点返回睡觉提示；每次**无条件调用 `get_daily_activity()`** |
| `self_monitor` | bot 自身发送消息事件（`message_sent`） | 深夜 0-6 点若未在安全期内，延迟 2-4s 发送自言自语（10s 冷却，超管**per-group** 30s 窗口抑制） |

> ⚠️ **`poke` 和 `toya_status_cmd` 的 routine 获取**：必须无条件调用 `get_daily_activity(hour, weekday)`，
> 让其内部做时段校验和缓存更新。不能用 `if not cached_content` 跳过调用，
> 否则上一时段的脏缓存会一直被复用（例如凌晨状态在白天继续出现）。

---

## features/ — 独立功能模块

### impression.py

> ℹ️ **孤岛现状**：该文件使用 `httpx` 直接调用 DeepSeek API，与 `core/api.py` 并行存在。
> 行为正确，但日后如需统一，可将其 `httpx` 调用替换为 `call_deepseek_api`。

| 功能 | 说明 |
|------|------|
| `recorder` (priority=1) | 静默录制群聊消息到 SQLite `impression_history.db` |
| `um_cmd`（群印象） | 读取目标用户最近 50 条发言，AI 生成侧写；支持 @；WL2 模式切换；3-5s 思考延迟 |
| `random_chat` (priority=99) | 3% 概率随机插嘴；10 分钟冷却；深夜 0-6 点不触发；有 JSON 解析救援 |

JSON 输出格式（impression 和 AutoChat）：`{"inner_os": "...", "reply": "..."}`（两字段，不含 action）

### gallery.py

图片权限由 `GROUP_IMAGE_PERMISSIONS` 控制。本地存储：`data/images/<category>/`

### director.py

Galgame 级导演骰子，由 `chat.py` 调用 `build_director_note()`。

**可安全删除**：删除后 chat.py 自动降级：
- `is_physical_or_drama = False`
- `is_really_spicy = False`
- `acting_guide = ""`（cool_guy_filter 不生效）
- `format_breaker = ""`（不附加导演指令）

### snowy.py

完全独立。PJSK 榜线预测，从 Moesekai API 拉取数据，PIL 渲染为 JPEG 图片。

字体文件（渲染依赖）：`font.ttf`（正文）和 `msyhbd.ttc`（加粗）必须与 `snowy.py` 同目录（`features/`）。

活动 ID 缓存：`data/pjsk_event_cache.json`

### verify.py

完全独立。管理三套新人审核名单，所有指令限 `ADMIN_GROUP_ID` 群使用。

群组配置：`data/verify_config.json` → `{"TARGET_GROUP_ID": "...", "ADMIN_GROUP_ID": "..."}`

### random_paro.py

服务于固定 CP 的派生抽取器。从两个独立身份池随机抽取配对。

- `抽派生` — 受 `ALLOWED_CHAT_GROUPS` 白名单控制
- 添加/删除指令 — 受 `SUPERUSER_QQ` 权限控制
- 头像拼合：从 `data/images/paro_avatars/彰人/` 和 `data/images/paro_avatars/冬弥/` 按派生名匹配
- 限频：30 分钟内 3 次，`asyncio.Lock` 防并发穿透
- 数据文件：`data/paro_pools.json`，已接入 `reload_assets()` 热重载

### scheduled.py

| 任务 | 触发时间 | 说明 |
|------|----------|------|
| `akito_morning` | 06:00 (UTC+8) | 从 `REACTIONS_DB.greetings.morning` 推送到 `TARGET_GROUPS` |
| `akito_night` | 23:50 (UTC+8) | 从 `REACTIONS_DB.greetings.night` 推送 |
| `clean_expired_memory` | 每小时 | 扫描所有会话，清理过期的 temp_implants |

所有定时推送前调用 `grant_safety_pass(10)`。

### event_mode.py

| 指令 | 权限 | 说明 |
|------|------|------|
| `开启WL2模式` | SUPERUSER_QQ | 注入 ID 为 `"WL2"` 的永久临时记忆（expire 2099 年） |
| `关闭WL2模式` | SUPERUSER_QQ | 移除 ID 为 `"WL2"` 的 temp_implant |

WL2 模式影响：impression.py（印象/AutoChat）、reactions.py（冬弥雷达/戳一戳）。

---

## 数据文件清单

| 路径 | 读写 | 说明 |
|------|------|------|
| `data/akito_memories.json` | 读写 | 核心记忆库（启动时加载，记忆变更时写入） |
| `data/last_interactions.json` | 读写 | 各群最后互动时间戳和 routine 快照（time_awareness.py） |
| `data/akito_persona.txt` | 只读 | 主人设 Prompt |
| `data/wl2_persona.txt` | 只读 | WL2 世界线人设 Prompt |
| `data/akito_scripts.json` | 只读 | 台词剧本库 |
| `data/akito_reactions.json` | 只读 | 反应资源包（complaints / greetings / poke / sleep 等） |
| `data/akito_prompts.json` | 只读 | Prompt 模板库（schema 定义 / acting_guide / system_header 等） |
| `data/akito_director.json` | 只读 | 导演骰子资产（toya_directions / dynamic_lexicon） |
| `data/akito_routine.json` | 只读 | 每日状态日程（各时段 status + poke 字段） |
| `data/wl2_routine.json` | 只读 | WL2 世界线状态 |
| `data/akito_songs.json` | 只读 | 歌曲背景知识（song_name / description / keywords） |
| `data/akito_relationships.json` | 只读 | 人物关系档案（keywords 白名单 + content） |
| `data/pjsk_knowledge.json` | 只读 | PJSK 黑话知识库 |
| `data/impression_history.db` | 读写 | 群消息 SQLite（impression.py 独占） |
| `data/pjsk_event_cache.json` | 读写 | PJSK 活动 ID 缓存（snowy.py 独占） |
| `data/pending_verify.json` | 读写 | 待审核名单 |
| `data/bond_verify.json` | 读写 | 待刷羁绊名单 |
| `data/hold_verify.json` | 读写 | 特殊挂起名单 |
| `data/verify_config.json` | 只读 | 审核系统群号配置 |
| `data/paro_pools.json` | 读写 | 派生抽取器池子数据（彰人池 / 冬弥池） |
| `data/images/paro_avatars/彰人/` `data/images/paro_avatars/冬弥/` | 只读 | 派生头像素材 |
| `data/images/<category>/` | 读写 | 本地图库 |
| `features/font.ttf` | 只读 | snowy.py 渲染字体 |
| `features/msyhbd.ttc` | 只读 | snowy.py 渲染加粗字体 |

---

## 常见维护操作

### 修改 API Key 或管理员 QQ
编辑 `.env` → 重启生效。无需改代码。

### 修改允许的群号
编辑 `core/constants.py` 中对应的群号列表 → 重启生效。

### 热更新 Prompt 和数据文件
修改 `data/` 下的 JSON 文件后，在群内发送 `重载配置 assets`（更新 JSON 数据）或 `重载配置 persona`（更新人设文本），无需重启。

### 新增歌曲知识
在 `data/akito_songs.json` 追加一个 key：
```json
"song_key": {
  "song_name": "《歌名》",
  "description": "50-120 字第一人称情感回忆",
  "keywords": ["歌名", "别名"]
}
```
热更新后自动注入 Prompt，无需改代码。

### 新增人物关系档案
在 `data/akito_relationships.json` 追加一条 entry：
```json
{"keywords": ["角色名", "别名"], "content": "关系描述"}
```
`keywords` 决定触发条件；命中后若消息含提问词则自动联网补充。

### 新增一个功能模块
1. 在 `features/` 下创建 `xxx.py`
2. 需要共享功能时：`from ..core import ...`
3. 在 `features/__init__.py` 末尾加：`from . import xxx`
   > ⚠️ 缺少这一行则模块静默不生效，不会报错。

### 修改定时任务时间
编辑 `features/scheduled.py` 中 `@scheduler.scheduled_job("cron", ...)` 的 `hour`/`minute` 参数。

### 调整随机插嘴概率
`features/impression.py` 顶部的 `CHAT_PROBABILITY = 0.03`（当前 3%）。

---

## 关键设计说明

1. **ReAct Agent 循环**：chat.py 主对话用两阶段调用。第一阶段 `call_deepseek_api_agent` 携带 `AGENT_TOOLS` 让 LLM 自主决定是否搜索；返回 `tool_calls` 则执行搜索后发起第二阶段 `call_deepseek_api`。有图片时跳过 Agent 直接走第二阶段。

2. **MVVM 渲染分离**：LLM 输出 `action`（动作）+ `dialogue`（台词）纯语义字段，Python 端随机拼装最终格式。history 存储已渲染的纯文本，切断格式复读传染链。

3. **Prompt 设计原则（"正向引导"替代"严禁"）**：`schema_action` 和 `schema_dialogue` 使用正向描述（"写成打字的语感"/"例如「叹气」「抓头发」"），不使用"严禁"句式。大量负向约束会导致模型反复注意被禁止的内容（粉红大象效应），在长 RP 后尤其明显。若未来需要修改 Prompt 约束，优先改写成正向示例和期望形态描述。

4. **`get_daily_activity()` 是 routine 的唯一入口**：它内部处理时段切换检测和缓存清除。任何需要获取当前 routine 的代码，都应直接调用它，而不是先检查 `AKITO_STATUS["cached_content"]` 是否存在再决定是否调用。绕过调用会导致跨时段的脏缓存持续生效。

5. **`AKITO_STATUS` vs 浮点量**：`AKITO_STATUS` 是 dict（可变），`from ..core import AKITO_STATUS` 后操作其字段是安全的。`AKITO_SAFE_UNTIL` 和 `AKITO_LAST_COMPLAINT` 是 float（不可变），必须用 getter/setter。

6. **self_monitor 的超管抑制逻辑**：`last_superuser_trigger_time` 是一个 `{group_id: timestamp}` dict，不是全局单值。A 群超管说话不应压制 B 群的深夜抱怨，commands.py 和 chat.py 更新时都要写 `[group_id]` 子键。

7. **JSON 历史记录格式**：chat.py 将 assistant 回复以 `{"inner_os": ..., "reply": ...}` 存入 history，但 system prompt 要求输出 `{"inner_os": ..., "action": ..., "dialogue": ...}`。读取历史时（time_awareness 压缩、复读检测等）需同时兼容 `reply` 和 `dialogue` 两个字段名。

8. **`impression.py` 孤岛现状**：使用 `httpx` 直接调用 API，而非 `core/api.py` 封装。行为正确，但统一时需注意：impression 的 JSON schema 是两字段（`reply`，无 `action`），与 chat.py 的三字段格式不同，不能直接套用 chat.py 的解析逻辑。

9. **handler 注册时机**：`on_command`/`on_message` 在模块被 import 时立即注册。`features/__init__.py` 中缺少某行 `from . import xxx`，对应功能会完全静默失效，不报任何错误。

10. **`features/snowy.py` 字体路径**：渲染函数用 `os.path.abspath(__file__)` 定位字体，字体必须与 `snowy.py` 同目录。

---

## AI 辅助维护的常见风险点

本节记录实际维护过程中 AI 容易误判或引入 bug 的场景，供后续接手时参考。

### 风险一：修改 `get_daily_activity()` 的调用姿势

**错误做法**：
```python
if not isinstance(AKITO_STATUS.get("cached_content"), dict):
    get_daily_activity(now.hour, now.weekday())
current_state = AKITO_STATUS.get("cached_content", "")
```

**问题**：跳过了 `get_daily_activity()` 内部的时段 key 校验，导致上一时段的 routine 在新时段继续生效（例如凌晨状态在白天出现）。

**正确做法**：永远无条件调用 `get_daily_activity()`，由它自己决定是否刷新缓存。

---

### 风险二：`akito_prompts.json` 中使用裸 ASCII 双引号

JSON 字符串值内的 `"` 必须转义为 `\"`，或改用 `「」`。用于举例时尤其容易忘记转义（如 `例如"叹气""抓头发"`）。加载失败时日志只有 WARNING，会静默回落到代码内默认值，功能不报错，行为悄悄退化。

**检查方法**：修改 JSON 文件后用 Python 校验：
```bash
python -c "import json; json.load(open('data/akito_prompts.json', encoding='utf-8')); print('OK')"
```

---

### 风险三：把负向约束加进 Prompt

添加"严禁 X"/"绝对禁止 X"类约束时，模型会在每次生成时都关注 X（粉红大象效应），高创意场景下反而更容易触发 X，并导致回复整体变得机械。

**替代方案**：用正向示例描述期望行为。不写"严禁包含动作描写"，写"写成打字的语感"。

---

### 风险四：修改 `self_monitor` 的超管抑制逻辑

`AKITO_STATUS["last_superuser_trigger_time"]` 必须是 `{group_id: timestamp}` dict。若简化成单个时间戳，超管在 A 群说话会导致 B 群的深夜抱怨也被静默。三处更新位置：`commands.py` 的 `_stamp_trigger()`、`chat.py` 的 Section 13、`reactions.py` 的 `self_monitor`。

---

### 风险五：`smart_finish` 的空值判断

发送前必须有两次空值检查：
```python
async def smart_finish(matcher, result):
    if not result: return          # 第一次：原始空值
    result = result.strip()
    if not result: return          # 第二次：strip 后空值
    ...
```
只做一次检查会导致纯空白字符串发出空消息（NapCat 会报错）。

---

### 风险六：新增模块时忘记注册

在 `features/__init__.py` 或 `handlers/__init__.py` 中必须有对应的 `from . import xxx`，否则模块静默不生效。这不是运行时错误，纯粹是功能消失，日志里没有任何报错信息。

---

### 风险七：history 中助手消息的字段名

chat.py 存入 history 的 assistant 条目格式：
```json
{"inner_os": "...", "reply": "..."}
```
但 system prompt 要求 LLM 输出：
```json
{"inner_os": "...", "action": "...", "dialogue": "..."}
```

任何读取 history 做分析的代码（压缩摘要、复读检测等）必须同时兼容 `reply` 和 `dialogue` 两个字段名，不能只认其中一个。

---

### 风险八：`AKITO_SAFE_UNTIL` 的跨模块赋值

见 life_state.py 一节。这是 Python 不可变量跨模块绑定陷阱，不会报错，只是安全期不生效，导致深夜抱怨被意外触发。

---

### 风险九：impression.py 的 JSON 格式与 chat.py 不同

impression.py（群印象 + AutoChat）使用的 schema 是两字段：`{"inner_os": ..., "reply": ...}`，没有 `action` 字段。chat.py 是三字段。两个文件的 JSON 解析代码不能混用，impression 的救援正则也只认 `reply` 字段。

---

### 风险十：routin 数据文件的字段结构

`akito_routine.json` 中每个时段的条目必须包含 `status`（文本描述）和 `poke`（list，戳一戳反应词列表）两个字段。若新增条目时遗漏 `poke` 字段，`reactions.py` 的 poke handler 会回落到 `fallback_poke` 而不报错，但戳一戳的个性化反应会失效。
