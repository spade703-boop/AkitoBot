# nonebot_plugin_akito — 维护手册

**角色**：东云彰人（初音未来：缤纷舞台 同人 AI，CP 立场：彰冬不拆不逆）  
**框架**：NoneBot2 + OneBot V11  
**AI 后端**：DeepSeek API / 智谱 GLM-4V（视觉）/ Tavily（搜索）  
**文档更新**：2026-06-30

---

## 目录结构

```
nonebot_plugin_akito/
├── __init__.py               # 插件入口：元数据 + require() + 导入三大子包
├── core/                     # 共享基础层（无副作用，可被任意模块导入）
│   ├── __init__.py           # 常量定义（时区/密钥/客户端/群白名单）+ 统一导出入口
│   ├── memory.py             # 长期记忆 JSON 读写 + SQLite 群聊上下文
│   ├── data.py               # JSON 数据文件加载（reactions/prompts/routine 等）
│   ├── life_state.py         # 彰人状态机（routine 缓存 / 节日 buff / 安全期管理）
│   ├── api.py                # DeepSeek / 智谱 / Tavily API 封装
│   ├── context.py            # Prompt 组装（人设 / 剧本示例 / 歌曲记忆 / 关系链）
│   ├── retrieval.py          # 通用语义检索引擎（BGE-M3 + 均值中心化）
│   ├── time_awareness.py     # 时间流逝感知（追踪群对话 gap，注入时段切换提示）
│   ├── game_store.py         # 共享玩家存储层：积分/亲密度/每日数据 + 签到钩子（gift/rpg 共用）
│   └── paths.py              # 数据路径定位（find_data_path / get_data_dir）
├── handlers/                 # 主聊天处理层（响应群消息）
│   ├── __init__.py
│   ├── chat.py               # 主对话引擎（ReAct Agent 循环 + Python 端 MVVM 排版）
│   ├── commands.py           # 记忆管理指令（查看/植入/清除/遗忘/重置/热更新）
│   └── reactions.py          # 被动反应（戳一戳 / 深夜自言自语）
└── features/                 # 独立功能模块
    ├── __init__.py
    ├── impression.py         # 群印象 + 随机插嘴（AutoChat）
    ├── gallery.py            # 相册图库指令
    ├── director.py           # Galgame 级导演骰子（可安全删除）
    ├── verify.py             # 新人审核名单管理
    ├── random_paro.py        # 派生抽取器（CP 同人灵感配对）
    ├── random_keyword.py     # 今日关键词（同人写作灵感关键词）
    ├── scheduled.py          # 定时任务（早晚安 / 过期记忆清理）
    ├── event_mode.py         # WL2 世界线剧情模式开关
    ├── gift.py               # 送礼系统（积分/送礼/偷分/羁绊/签到闸门/超管重置）
    └── rpg/                  # RPG 子包：签到/打怪/世界BOSS/组队/强化/背包/群排行榜（详见 rpg/README.md）
                                   ├── __init__.py
                                   ├── config.py         # 全部数值/文案/配置（可被 rpg_config.json 热更新）
                                   ├── player.py         # 经验→等级派生/称号/今日装备 helper/战力计算
                                   ├── fortune.py        # 隐藏运势掷取（含连签保底）+ 签到钩子 on_signin
                                   ├── hunt.py           # 打怪指令 + 战斗结算（精英/今日增益/单刷/组队合力）
                                   ├── boss.py           # 世界BOSS刷出/强制开启/查询/单人攻击/双人攻击/贡献结算
                                   ├── team.py           # 组队@某人 指令（羁绊定成功率、失败退化单刷）
                                   ├── smith.py          # 强化/购买装备/重置RPG功能
                                   ├── inventory.py      # 背包/使用指令 + 道具效果 + 掉落 helper
                                   └── character.py      # 我的角色面板（含称号/战绩）+ 群排行榜 + 冒险帮助
```

---

## 依赖关系图

```
core/__init__.py ←───────────────────────────────────────┐
     ↓  (常量定义 + 统一导出)                             │
memory.py      (← __init__, data)                        │
data.py        (惰性 ← retrieval / features 热重载钩子)   │
life_state.py  (← __init__, data)                        │ core/__init__.py
api.py         (← __init__；含 LLM JSON 提取/救援工具)    │ 统一对外暴露所有符号
context.py     (← data, api, retrieval)                  │
retrieval.py   (← __init__(np)；惰性 ← data, api)        │
time_awareness.py (← __init__, data, life_state)         │
game_store.py  (← __init__；gift/rpg 共用存储层)         │
     └────────────────────────────────────────────────── ┘
                           ↓
             handlers/ 和 features/ 均通过
             `from ..core import ...` 访问
```

**导入层级规则**：

- `core/` 子模块只能用相对导入 `.` 访问同层文件，**严禁**向上引用 `handlers/` 或 `features/`
- `handlers/` 和 `features/` 均使用 `from ..core import ...`（两个点 = 上一级包）
- `handlers/` 和 `features/` 之间**无互相引用**（唯一例外：`rpg/team.py` → `gift._bond_level`，单向消费 gift 的羁绊体系——gift 不反向依赖 rpg，无环）
- `features/verify.py` 无任何内部依赖，完全独立
- `features/director.py` 仅被 `handlers/chat.py` 调用，可整体删除（chat.py 有安全降级）

---

## 配置与密钥管理

**所有密钥和敏感 ID 统一在 `.env` 中管理**，`core/__init__.py` 通过 `os.environ.get()` 读取：

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

### `__init__.py`（含原 constants.py）

无内部依赖。从 `.env` 读取密钥，定义全局常量。子模块通过 `from . import ...` 获取，外部通过 `from ..core import ...` 获取。

| 变量 | 来源 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | `.env` | DeepSeek 密钥 |
| `TAVILY_API_KEY` | `.env` | Tavily 搜索密钥 |
| `ZHIPU_API_KEY` | `.env` | 智谱 GLM 密钥 |
| `SILICONFLOW_API_KEY` | `.env` | SiliconFlow 密钥（BGE-M3 语义检索） |
| `embedding_client` | — | SiliconFlow AsyncOpenAI 客户端（无 key 时为 None，检索自动降级） |
| `SUPERUSER_QQ` | `.env` | 超级用户 QQ（未配置则超管指令全部停用，启动时告警；代码内无兜底） |
| `TOYA_QQ_ID` | `.env` | 冬弥 QQ 号（CP 模式判断；未配置则不识别冬弥本人，启动时告警；代码内无兜底） |
| `client` | — | DeepSeek AsyncOpenAI 客户端 |
| `vision_client` | — | 智谱 GLM 视觉客户端 |
| `TZ_CN` | — | UTC+8（北京时间，用于 routine/睡眠判定） |
| `TZ_JST` | — | UTC+9（东京时间，用于彰人对话报时） |
| `MAX_HISTORY_LEN` | — | 对话历史最大条数（当前 40） |
| `ALLOWED_CHAT_GROUPS` | `.env` | 允许主对话的群列表（逗号分隔） |
| `ALLOWED_CP_GROUPS` | `.env` | 允许 CP 相关功能的群列表（逗号分隔） |
| `ALLOWED_MEMORY_GROUPS` | `.env` | 允许使用记忆指令的群列表（逗号分隔） |
| `TARGET_GROUPS` | `.env` | 定时推送目标群列表（逗号分隔） |
| `GROUP_IMAGE_PERMISSIONS` | `.env` | 各群的图库分类权限（JSON 格式） |

### memory.py

管理 `data/akito_memories.json`（运行时内存 `MEMORY_DB`）。模块加载时自动调用 `load_memory()`。

| 函数/变量 | 说明 |
|-----------|------|
| `MEMORY_DB: dict` | 全部会话记忆的内存字典 |
| `get_memory_key(event)` | 从 Event 生成 `group_xxx` 键（**按群而非按人**，群内所有用户共享记忆） |
| `get_user_memory(key)` | 获取/初始化某会话的记忆字典 |
| `save_memory()` | 原子写入（先写 .tmp 再 os.replace；落点目录由 `data.get_data_dir()` 统一解析） |
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
| `REACTIONS_DB` | `content/akito_reactions.json` + `content/gallery_text.json` + `content/greetings.json` | 被动反应 / 图库文案 / 问候，合并加载回单一 dict |
| `PROMPTS_DB` | `persona/prompts_system.json` + `persona/prompts_character.json` | Prompt 模板：系统机制 + 角色演绎，合并加载回单一 dict |
| `DIRECTOR_DB` | `akito_director.json` | 导演骰子资产：toya_directions / dynamic_lexicon |
| `DAILY_ROUTINE` | `akito_routine.json` | 每日状态日程，键为时间段（每条含 `status` 和 `poke` 字段） |
| `WL2_ROUTINE` | `wl2_routine.json` | WL2 世界线状态 |
| `SONG_DATA` | `akito_songs.json` | 歌曲背景知识 |
| `RELATIONSHIP_DATA` | `akito_relationships.json` | 人物关系档案（含 `keywords` 白名单） |
| `PJSK_KNOWLEDGE_BASE` | `pjsk_knowledge.json` | PJSK 黑话知识库全文（str，热重载会重新赋值——消费方**必须**经 `get_pjsk_knowledge_base()` 在调用时读取，不可模块级导入旧引用） |
| `PJSK_INTRO` | `pjsk_knowledge.json` 的 `introduction` | 语境锁前言（同上，经 `get_pjsk_intro()` 读取） |
| `PJSK_ENTRIES` | `pjsk_knowledge.json` 的 `knowledge_list` 拍平 | 结构化条目列表 `list[dict]`，每条 `{"category": str, "text": str}`，供检索引擎使用 |

**热更新**：`reload_assets()` 用 `.clear()` + `.update()` / `.extend()` 原地修改所有全局变量，
已持有引用的模块无需重新 import，即时生效。通过 `重载配置 assets` 指令触发。

**公共工具**：`find_data_path(filename)` 定位数据文件；`get_data_dir()` 返回写回文件的统一落点目录
（memory / time_awareness 共用）；`get_pjsk_knowledge_base()` / `get_pjsk_intro()` 实时读取 PJSK 字符串（热重载安全）。

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
    "previous_context": "",      # 上一时段的状态描述文本（时段切换时自动保存，供 sleep_buffer 等过渡期引用）
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
| `compute_period_key(hour, weekday, minute=0)` | 计算 routine 时段 key 的**单一真相源**——`get_daily_activity` 与 `time_awareness` 均转调此函数，调整作息划分只需改这一处 |
| `get_daily_activity(hour, weekday, minute=0)` | 返回当前时段状态字符串，内置 30 分钟缓存 + **时段变更自动清缓存**。时段划分：`late_night`(0-6)、`morning_*`(6-8)、`noon_*`(8-12)、`lunch_*`(12-13)、`afternoon_*`(13-15)、`evening`(15-18)、`night_training`(18-21)、`night_home`(21-23:29)、`sleep_buffer`(23:45-23:59)。**任何需要 routine 的地方都应无条件调用此函数**，不要在外部判断 cached_content 是否存在后跳过调用 |
| `check_sleep_status(msg)` | 判断是否深夜并返回 `(should_ignore, instruction)` |
| `get_festival_buff(date_obj)` | 返回今日节日 Prompt 片段 |
| `get_morning_run_buff(hour)` | 返回晨跑状态 Prompt（6 点整段生效） |
| `get_sleep_buffer_buff(hour, minute)` | 返回睡前准备状态 Prompt（23:45-23:59 生效），若存在 `previous_context` 则自动注入前一时段的活动记忆 |
| `get_toya_anchor()` | 据当前缓存 routine 推断冬弥此刻位置，返回 Prompt 片段（routine 文本含「冬弥」或处于同框时段 `night_training`/`evening`/`lunch_weekday` → 声明在场；否则给「与情境自洽 + 禁无关支线 + 禁沉重话题」推断规则）+ 跨轮连贯锁；无缓存返回空串。由 chat.py 在涉冬弥话题且非 WL2 时注入 |
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
| `describe_image(list[bytes])` | 智谱 GLM-4.6V-Flash 图片分析（多图/动图抽帧），JSON 输出 + 布尔特征代码侧裁决，返回 `ImageAnalysis`；失败返回 `None`。最多 2 次调用（截图/截断时追加高清 OCR），各 45s/30s 超时；首轮 thinking 默认开启（`_VISION_THINKING` 常量可关） |
| `format_image_analysis_for_chat(analysis)` | 把 `ImageAnalysis` 渲染成注入 history/Prompt 的五段式文本（标签/识别角色/画面核心/OCR/细节，各段截断防膨胀） |
| `to_image_data(image)` | 从 AlcImage 获取原始字节（支持 raw/path/url 三种来源） |
| `embed_text(text)` | BGE-M3 单条 embedding（SiliconFlow），返回 1024 维 float list；未配置 key / 失败返回 None，不抛异常 |
| `rerank_documents(query, documents, top_n)` | bge-reranker-v2-m3 重排序（SiliconFlow，与 embed 同 key 门控），返回 `[(候选下标, 相关分)]` 按分降序；未配置 key / 失败返回 None，不抛异常 |
| `extract_json_block(raw)` | 从 LLM 原始返回中提取最外层 `{...}` 片段；无匹配时原样返回（chat / impression 共用） |
| `parse_json_object(raw)` | 提取 JSON 块并解析为 dict；失败返回 None（chat / impression 共用的完整 JSON 入口） |
| `rescue_field(raw, *fields)` | 从残缺 JSON 中正则抠出第一个命中的字符串字段值；覆盖字段值截断到 EOF 的场景。无匹配返回 None，调用方需用 `is not None` 判断命中 |
| `rescue_tail_after_field(raw, anchor_field="inner_os")` | 当 JSON 在已知锚点字段后损坏时，提取尾部残留正文；用于 key 名跑偏或 reply/dialogue 残段救援 |

> `call_deepseek_api_agent` 专供 `chat.py` 的 ReAct 循环，其他调用方用 `call_deepseek_api`。

### context.py

| 函数 | 说明 |
|------|------|
| `get_base_persona()` | 读取 `data/akito_persona.txt` 人设文本 |
| `get_random_examples(n)` | 从 `SCRIPT_DB` 随机抽取 n 条台词示例注入 Prompt（检索不可用时的兜底） |
| `get_relevant_examples(query, n)` | 语义检索剧本示例；检索不可用或无相关命中均回退到 `get_random_examples` |
| `get_relevant_pjsk(query, n)` | 语义检索 PJSK 黑话（检索前与剧本一致做 query 扩散 blend）；检索不可用回退全量 `PJSK_KNOWLEDGE_BASE`，无相关命中仅注入前言（降噪）；`PJSK_INTRO` 始终在前 |
| `get_song_memories()` | 将 `SONG_DATA` 格式化为静态曲名清单，每次对话先注入；具体点名某首歌时再补充详细记忆 |
| `get_song_mention(text)` | 对消息做 `keywords` 子串匹配，命中时最多注入 2 首歌的完整 `description` |
| `get_hybrid_relationship(text)` | 本地关键词白名单扫描 + 可选联网补充，返回 Prompt 片段 |
| `reload_persona()` | 重新读取 `akito_persona.txt`，返回新内容（`重载配置 persona` 触发） |

### retrieval.py

通用语义检索引擎，BGE-M3（1024 维）+ 均值中心化；可用时 cosine 粗召回后经 bge-reranker-v2-m3 精排 + 阈值过滤。设计为 registry 驱动，加新语料只需一条配置（含 `doc_text` 精排文本构造器）+ 跑一次 build。

| 函数 | 说明 |
|------|------|
| `retrieve(corpus, query, top_k)` | 异步语义检索（cosine 召回 → 精排重排）。三态返回：None=不可用（降级）、`[]`=无相关命中、`[id, ...]`=命中的源 DB 下标 |
| `reload_indices()` | 重读所有 `.npz` 并重建缓存，返回成功加载数（`reload_assets()` 联动调用） |

精排开关与调参均为模块常量：`_RERANK_ENABLED`（一键回退纯 cosine）、`_RERANK_RECALL_K`（召回深度，默认 20）、`_RERANK_MIN_SCORE`（相关分阈值，默认 0.0=不过滤；用 `tools/eval_retrieval.py` 调参后上调）。

**.npz schema**（每语料一份 `data/content/<name>_embeddings.npz`）：
`vectors`(N×1024 float32)、`mean`(1024 float32)、`indices`(N int32)、`count`(int)

**降级链路**（5 层）：
无 numpy → 无 `.npz` 文件 → 无 API key → embed 返回 None → count 不符 → 均回退静态/随机行为；精排失败另回退纯 cosine 顺序。不抛错、不空窗。

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
2. 文本/视觉解析   分离纯文本和图片；图片（最多 3 张）一次调用 GLM-4.6V 结构化识别
                   （JSON + 布尔特征裁决，26 角色名册，截图自动二次 OCR）
3. 并发保护        asyncio.Lock（per 会话键）防止同一会话并发
4. 睡眠检测        check_sleep_status → 深夜可能忽略或返回睡觉提示
5. Prompt 组装     人设 + 时间感知 + 临时记忆 + 关系链 + 搜索结果 +
                   剧本示例 + 歌曲知识 + 导演骰子 + 冬弥去向锚定(get_toya_anchor，涉冬弥且非WL2) + schema 格式指令
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

**搜索调度 + ReAct Agent 循环（Step 6）**：

```
有图片 ──────────────────────────────────────────────────────→ call_deepseek_api（直接生成，不搜索）
无图片 + 命中 info_keywords → 强制 smart_search → _build_search_aside 注入用户消息 → call_deepseek_api
无图片 + 未命中关键词 → call_deepseek_api_agent（带 AGENT_TOOLS，LLM 自主决定）
           ├─ 返回 tool_calls → 执行 smart_search → 塞回 messages → call_deepseek_api
           ├─ 返回普通内容   → 直接使用 agent_message.content
           └─ 返回 None（超时）→ call_deepseek_api（降级兜底）
```

> 两条搜索路径（关键词强制 / LLM 自主）都把搜索结果回灌进**人设系统提示**重新生成，
> 由彰人用自己的语气复述，绝不直出原始摘要；搜索无结果时统一走 `_search_miss_note` 兜底。

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
| `poke` | 戳一戳通知（PokeNotifyEvent） | 按时段返回反应；深夜 0-6 点返回睡觉提示；每次**无条件调用 `get_daily_activity()`** |
| `self_monitor` | bot 自身发送消息事件（`message_sent`） | 深夜 0-6 点若未在安全期内，延迟 2-4s 发送自言自语（10s 冷却，超管**per-group** 30s 窗口抑制） |

> ℹ️ **冬弥去向已收敛到主对话引擎**：原独立的 `冬弥呢` 指令（`toya_status_cmd` / `get_toya_location_reply` / `toya_radar` 模板）已移除；
> routine 锚定的冬弥位置推断 + 连贯锁现由 `core.life_state.get_toya_anchor()` 提供、在 chat.py 涉冬弥话题时统一注入（用「小彰冬弥呢」触发）。

> ⚠️ **`poke` 的 routine 获取**：必须无条件调用 `get_daily_activity(hour, weekday, minute)`，
> 让其内部做时段校验和缓存更新。不能用 `if not cached_content` 跳过调用，
> 否则上一时段的脏缓存会一直被复用（例如凌晨状态在白天继续出现）。

---

## features/ — 独立功能模块

### impression.py

> ℹ️ **已并轨**：该文件直接使用 `core` 的共享 `client`（AsyncOpenAI）调用 DeepSeek（带自定义温度/超时参数），
> JSON 提取与救援也统一走 `core.api.extract_json_block` / `rescue_field`。
> 仅 JSON schema 仍为两字段（`reply`，无 `action`），与 chat.py 的三字段不同（见风险九）。

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

### verify.py

完全独立。管理三套新人审核名单，所有指令限 `ADMIN_GROUP_ID` 群使用。

群组配置：`data/verify_config.json` → `{"TARGET_GROUP_ID": "...", "ADMIN_GROUP_ID": "..."}`
（该文件必须存在且两个 key 齐全，否则审核系统整体静默停用并在启动日志告警——群号不在代码内兜底。）

### random_paro.py

服务于固定 CP 的派生抽取器。从两个独立身份池随机抽取配对。

- `抽派生` — 受 `ALLOWED_CHAT_GROUPS` 白名单控制
- 添加/删除指令 — 受 `SUPERUSER_QQ` 权限控制
- 头像拼合：从 `data/images/paro_avatars/彰人/` 和 `data/images/paro_avatars/冬弥/` 按派生名匹配
- 限频：30 分钟内 3 次，`asyncio.Lock` 防并发穿透
- 模糊匹配：`_fuzzy_match()` 三级匹配（精确 → 前缀 → 包含），大小写不敏感；歧义时列出候选
- 数据文件：`data/paro_pools.json`，已接入 `reload_assets()` 热重载

### random_keyword.py

同人写作灵感关键词抽取器。从单一关键词池随机抽取 1-3 个意象/情境/关系张力短语。

- `今日关键词` — 受 `ALLOWED_CHAT_GROUPS` 白名单控制，仅支持群聊；普通用户每人每日 1 次，群内同日不放回
- 添加/删除指令 — 受 `SUPERUSER_QQ` 权限控制
- 限频：每日 1 次，基于 `keyword_draws.json` 持久化记录，比较 `datetime.now(TZ_CN).date()` 自动跨天失效
- 群内唯一：普通用户成功抽取后会占用该群当天关键词池；当日池子耗尽则提示次日再来；超管抽取不占用词池
- 并发保护：单个 `asyncio.Lock` 包住整次“读状态 → 过滤候选 → 抽取 → 写状态”，避免同群并发抽到同词
- 模糊匹配：`_fuzzy_match()` 三级匹配（精确 → 前缀 → 包含），大小写不敏感；歧义时列出候选
- PIL 渲染：`_render_keyword_result()` 卡片式输出（序号 + 关键词），`_render_pool_image()` 三列网格展示全池
- 数据文件：`data/fanfic_keywords.json`（关键词池）、`data/keyword_draws.json`（每日抽取记录），已接入 `reload_assets()` 热重载
- 字体：复用 `features/msyhbd.ttc`

### scheduled.py

| 任务 | 触发时间 | 说明 |
|------|----------|------|
| `akito_morning` | 06:00 (UTC+8) | 从 `REACTIONS_DB.greetings.morning` 推送到 `TARGET_GROUPS` |
| `akito_night` | 23:50 (UTC+8) | 从 `REACTIONS_DB.greetings.night` 推送（此时处于 `sleep_buffer` 睡前缓冲区） |
| `clean_expired_memory` | 每小时 | 扫描所有会话，清理过期的 temp_implants |

所有定时推送前调用 `grant_safety_pass(10)`。

> **睡眠缓冲区（sleep_buffer）**：23:45-23:59 为睡前过渡时段。`get_daily_activity()` 在时段切换时自动保存 `previous_context`，`get_sleep_buffer_buff()` 将其注入 prompt，确保角色在睡前准备中仍能回应前一时段的遗留话题。0:00 后 `check_sleep_status()` 照常接管睡眠拦截。

### event_mode.py

| 指令 | 权限 | 说明 |
|------|------|------|
| `开启WL2模式` | SUPERUSER_QQ | 注入 ID 为 `"WL2"` 的永久临时记忆（expire 2099 年） |
| `关闭WL2模式` | SUPERUSER_QQ | 移除 ID 为 `"WL2"` 的 temp_implant |

WL2 模式影响：impression.py（印象/AutoChat）、reactions.py（戳一戳）、chat.py（`get_toya_anchor` 同框锚定门控跳过，避免与决裂世界线冲突）。

### gift.py

彰冬同人圈主题的群友互送小游戏。完全自包含（不依赖其他 feature 模块），通过 `core/game_store.py` 共享存储层与 RPG 子系统共用玩家数据。

游戏闭环：`签到` 赚积分 → `送礼@对方` 送随机礼物、累积两人亲密度（同好羁绊）→ `偷@对方` 顺走少量积分（反效果：偷必掉羁绊）→ 循环。6 档羁绊梯（从「初识」到「从今往后直到永远」=Lv6），羁绊越高送礼暴击/共识收益越大，偷的惩罚也越大。被偷保护机制（protect_until + 硬上限）防止泛滥。

| 指令 | 权限 | 说明 |
|------|------|------|
| `签到` | 普通用户 | 每日 1 次领积分 + 搭车 RPG 签到钩子（暗掷运势 + 发经验 + 发今日装备）；签到前随机延迟错开其他 bot |
| `送礼@对方` | 普通用户 | 每日 1 次，系统从「你当前积分买得起的礼物」中随机送；按权重抽事件（普通/暴击/回礼/失败/意外）；顶档「自己产的彰冬饭」触发惊喜升级固定结算 |
| `偷@对方` | 普通用户 | 每日 2 次，小概率顺走对方少量积分；强保护 + 偷必掉羁绊 |
| `我的积分` | 普通用户 | 查看当前积分余额 |
| `礼物列表` | 普通用户 | 查看所用礼池中各档位的礼物清单 |
| `我的羁绊` | 普通用户 | 查看与指定群友的亲密度等级/进度 |
| `群羁绊排行` | 普通用户 | 本群所有有羁绊记录的亲密度排行（按最近送礼时间排序） |
| `测试我的羁绊` | 普通用户 | 渲染「我的羁绊」HTML 卡片预览 |
| `送礼功能帮助` / `送礼帮助` / `送礼说明` | 普通用户 | 指令帮助 |
| `重置送礼` | **SUPERUSER_QQ** | 清空本群全部送礼/积分/羁绊数据 |
| `重置本群签到` / `重置全群签到` / `重置签到次数` | **SUPERUSER_QQ** | 仅清掉本群今日签到闸门（`last_sign_in`=today → ""），不改 RPG 连签/装备/运势状态 |

**架构关系**：
- 存储层：`core/game_store.py`（`gift_data.json`，含 `LOCK`/积分/亲密度/每日重置/签到钩子注册）
- 签到衔接：`gift.py` 的签到持锁后调用 `run_signin_hooks`，RPG 的 `fortune.on_signin` 通过 `register_signin_hook` 注册订阅；gift 不反向依赖 rpg
- 配置热更：通过 `data/content/gift_config.json` 覆盖默认值；`reload_assets()` 调用 `gift.reload_gift_config()` 热更新
- 数据文件：`data/gift_data.json`（玩家数据，含积分/送礼/偷/羁绊/RPG 字段）、`data/content/gift_config.json`（配置覆盖）、`data/content/intimacy_tiers.json`（羁绊梯定义）

### rpg/

在送礼社交玩法之上的轻量群文字 RPG。设计原则是：**平时走轻量个人挑战线，低频再用世界 BOSS 承接群体参与感**。不是送礼的附庸，而是和送礼并列的积分去向：给手上有分但一时没地方送礼的人一个稳定的消耗口。

完整架构与指令说明见 `features/rpg/README.md`，此处仅记维护要点：

**文件职责速查**：

| 文件 | 职责 |
|------|------|
| `config.py` | 全部数值（战斗/运势/强化/掉落/连签/精英/世界BOSS）/ 文案 / 错误的默认配置 `DEFAULT_RPG_CONFIG`；`_cfg(key)` 读取、`_error(key)` 错误文案、`_copy(key)` 随机文案；`reload_rpg_config()` 被 `reload_assets()` 联动热更 |
| `player.py` | 纯函数：`_level_of(exp)` 经验→等级、`_level_progress(exp)` 进度、`_title_of(level)` 称号分档、`_cum_exp(level)` 升到此级所需累计经验、`_ensure_player(group, uid, name)` 初始化玩家记录；`_combat_power(user)` 计算今日装备隐藏战力；`_resolve_group(event)` 群校验 |
| `fortune.py` | `on_signin(group, uid, rng, today)` 签到钩子入口（暗掷运势 + 发经验 + 今日装备 + 连签判定含额外经验 + 断签重置）；`_fortune_combat/drop_factor` 为战力/掉落提供运势修正；连签保底机制（连凶天数达阈值自动转大吉） |
| `hunt.py` | `今日打怪` 指令 + 战斗结算管线：`_encounter_level`（装备等级分段）→ `_pick_encounter`（怪池权重按等级分档 + 精英概率按等级门槛）→ `_settle_solo`（单刷含新手保护 `_rookie_power_factor` + 随机事件 + 运势修正 + 今日增益）→ `_settle_coop`（组队合力，取双方较高等级抽怪）；普通打怪结算后会额外触发一次世界 BOSS 刷出判定 |
| `boss.py` | 世界 BOSS 逻辑：近 7 日活跃签到人数缩放、群级状态 `group["rpg"]["world_boss"]` 持久化、`世界BOSS` / `攻击世界BOSS` / `组队世界BOSS@某人` / `强制开启世界BOSS` 指令、贡献榜、按贡献发放经验/积分；12 人后血量规模 `scale_count` 会继续软扩容，但奖励规模 `reward_scale_count` 扩容更慢，避免大群秒杀后奖励也同步爆炸；每个已签到玩家在每只 BOSS 上都有独立的 `participants[uid]` 临时装备与 1 次出手机会；若当天未击败，则在隔天首次访问相关状态时按已造成进度折算补偿并清场；`强制开启世界BOSS` 仅超管可用，会跳过概率与活跃人数门槛，但不会覆盖当天已存在的 BOSS；奖励不计入 `hunt_total/hunt_wins` |
| `team.py` | `组队@某人` 指令：从 `gift._bond_level` 取羁绊→算成功率；对方未签到/装备已损坏 → 直接拒绝（不退化单刷）；成功走 `_settle_coop`（双方各得经验积分掉落、各自装备消耗，并额外结算战力/经验/掉落协作加成）；羁绊不够 → 走 `_settle_solo`（只消耗发起人，队友无损）；普通组队结算后同样会触发世界 BOSS 刷出判定 |
| `smith.py` | `强化今日装备` / `强化世界BOSS装备` / `购买装备` / `重置RPG功能` 指令（积分出口 + 超管测试辅助）：两套强化都走 `forge.costs` 分段收费 `[30,60,90]`；世界 BOSS 强化只作用于该 BOSS 的独立临时装备；购买装备花 100 积分重置已损坏普通装备（每天限 1 次，打上 `equip_rebought` 标记，打怪经验和积分减半）；`重置RPG功能` 仅为今天签到过的人重发普通装备，不改运势、连签和世界 BOSS 状态 |
| `inventory.py` | `背包` / `使用 [道具名]` 指令 + 道具效果（`exp_buff`/`exp_grant`/`gift` 三种类型）+ 礼物券分支走完整送礼流程（`_settle`/`_build_broadcast`） + `_roll_drops` 掉落判定 + `_add_item` 背包入库 |
| `character.py` | `我的角色` 面板（含称号 `_title_of`/战绩/普通装备状态/世界BOSS状态/积分/背包）+ `群排行榜`（本群 exp>0 的人按经验降序 Top 10，纯文字不 @）+ `冒险帮助`（含世界 BOSS 指令） |

**依赖方向**（design constraint）：`features/gift.py` 与 `features/rpg/*` 都依赖 `core/game_store.py`；签到走钩子表解耦（gift → `run_signin_hooks` → `fortune.on_signin`，gift 不依赖 rpg）；三条 rpg→gift 单向依赖：`rpg/team.py` / `rpg/boss.py` → `gift._bond_level`（消费羁绊）、`rpg/inventory.py` → `gift._pick_gift_by_name`/`_settle`/`_build_broadcast` 等（礼物券消费走完整送礼流程）；gift 不反向依赖 rpg，无环。

**配置热更**：修改 `data/content/rpg_config.json` → 群内 `重载配置 assets` → `reload_assets()` → `rpg_config.reload_rpg_config()`，无需重启。

**数值断言规则**：测试一律从 `rpg_config._cfg(...)` 走读取，不硬编码数字——调 `rpg_config.json` 数值不会导致测试变脆。

**当前维护重点**：
- 世界 BOSS 只会在普通 `今日打怪` / `组队@某人` 后以 `0.1%` 概率刷出；若近 7 日活跃签到人数少于 3，就算随机命中也不会生成。
- 世界 BOSS 强度按近 7 日活跃签到人数缩放，而不是按“今天当前已签到人数”计算，避免早时段触发时血量异常偏小。
- 世界 BOSS 奖励只有经验和积分；贡献结算会更新玩家 `exp/points`，但不会改普通战绩字段 `hunt_total/hunt_wins`。
- `core/game_store.py` 现在必须保留 `groups[gid]["rpg"]`，否则世界 BOSS 的群级状态会在归一化时丢失。

---

## 数据文件清单

> 只读内容文件归入 `data/persona/` 与 `data/content/` 子目录；`_find_data_path` 自动搜子目录与根目录（兼容旧 flat 布局）。`PROMPTS_DB` / `REACTIONS_DB` 由各自拆分文件合并加载，consumer 不感知。

### 只读内容 — `data/persona/`（人设 + Prompt，热重载）

| 路径 | 说明 |
|------|------|
| `persona/akito_persona.txt` | 主人设 Prompt |
| `persona/wl2_persona.txt` | WL2 世界线人设 Prompt |
| `persona/prompts_system.json` | Prompt 模板·系统机制（system_header / schema_* / memory_capture_rule / memory_*_template） |
| `persona/prompts_character.json` | Prompt 模板·角色演绎（vitality / tone_limiter / reliable_mode / cool_guy_filter / toya_*） |

### 只读内容 — `data/content/`（语料 / 行为 / 世界观，热重载）

| 路径 | 说明 |
|------|------|
| `content/akito_routine.json` | 每日状态日程（各时段 status + poke 字段） |
| `content/wl2_routine.json` | WL2 世界线状态 |
| `content/akito_sleep.json` | 睡眠场景文案（complaints / sleep_* 各场景） |
| `content/akito_reactions.json` | 被动反应（旧 flat 布局兼容读取；`behavior_seeds` 已随冬弥雷达退役、`fallback_poke` 在 routine.json） |
| `content/gallery_text.json` | 图库文案（save_img_replies / send_img_angles） |
| `content/greetings.json` | 早晚安问候（morning / night） |
| `content/akito_relationships.json` | 人物关系档案（keywords 白名单 + content） |
| `content/akito_songs.json` | 歌曲背景知识（song_name / description / keywords；其中 `keywords` 由 `get_song_mention` 消费，用于歌曲别名匹配） |
| `content/akito_scripts.json` | 台词剧本库（每条含 `type`/`category`/`topics`/`cn_key`/`context`/`dialogue`；检索 key 为 `cn_key`，缺失回退 `context`） |
| `content/pjsk_knowledge.json` | PJSK 黑话知识库（`introduction` + `knowledge_list` → `PJSK_INTRO` + `PJSK_ENTRIES`） |
| `content/scripts_embeddings.npz` | 剧本语义向量库（`tools/build_embeddings.py` 生成；embed key=`cn_key`，gitignore） |
| `content/pjsk_embeddings.npz` | PJSK 语义向量库（`tools/build_embeddings.py` 生成，gitignore） |
| `content/akito_director.json` | 导演骰子资产（toya_directions / dynamic_lexicon） |
| `content/gift_config.json` | 送礼系统配置覆盖（礼物档位/随机事件权重/偷参数/签到延迟） |
| `content/rpg_config.json` | RPG 全量配置覆盖（战斗/运势/强化/掉落/精英/世界BOSS/文案/错误）/ 热更可见 |
| `content/intimacy_tiers.json` | 羁绊梯定义（6 档正向，从「初识」到「从今往后直到永远」） |

### 功能 / 运行时 — `data/` 根目录（多为写回）

| 路径 | 读写 | 说明 |
|------|------|------|
| `data/gift_data.json` | 读写 | gift + rpg 共享玩家数据；`groups[*].users[*]` 保存积分/送礼/偷/羁绊/RPG 经验/装备/运势/背包/连签，`groups[*].rpg` 保存世界 BOSS 等群级 RPG 状态 |
| `data/akito_memories.json` | 读写 | 核心记忆库（启动时加载，记忆变更时写入） |
| `data/last_interactions.json` | 读写 | 各群最后互动时间戳和 routine 快照（time_awareness.py） |
| `data/impression_history.db` | 读写 | 群消息 SQLite（impression.py 独占） |
| `data/paro_pools.json` | 读写 | 派生抽取器池子数据（彰人池 / 冬弥池） |
| `data/fanfic_keywords.json` | 读写 | 今日关键词池子数据 |
| `data/keyword_draws.json` | 读写 | 今日关键词每日抽取记录 |
| `data/pending_verify.json` / `bond_verify.json` / `hold_verify.json` | 读写 | 待审核 / 待刷羁绊 / 特殊挂起名单 |
| `data/verify_config.json` | 只读 | 审核系统群号配置 |
| `data/images/<category>/`、`paro_avatars/彰人\|冬弥/` | 读写 / 只读 | 本地图库 / 派生头像素材 |
| `features/msyhbd.ttc` | 只读 | random_paro / random_keyword 渲染加粗字体 |

---

## 常见维护操作

### 测试与沙箱策略

- 编辑文件后**不会自动触发 pytest**。只有手动执行命令才会跑测试。
- `pytest -q` = 全量回归。
- 测试文件按模块拆分，日常维护优先跑对应文件，而不是每次全量。
- 默认执行顺序：**先按改动范围跑对应测试，再视风险决定是否补全量**。

常用命令：

```bash
ruff check .
pytest -q
pytest tests/test_chat_helpers.py -q
pytest tests/test_commands_helpers.py -q
pytest tests/test_reactions_helpers.py -q
pytest tests/test_impression_helpers.py tests/test_impression_rescue_regression.py -q
pytest tests/test_verify_helpers.py -q
pytest tests/test_gallery_helpers.py -q
pytest tests/test_random_paro_helpers.py -q
pytest tests/test_random_keyword_helpers.py -q
pytest tests/test_data.py -q
pytest tests/test_director.py -q
pytest tests/test_event_mode_helpers.py -q
pytest tests/test_scheduled_helpers.py -q
pytest tests/test_gift.py -q
pytest tests/test_rpg.py -q
```

推荐给后续 AI 的执行规则：

1. 先看本次改动落在哪些文件。
2. 只改了单个功能模块时，先跑对应的 `tests/test_xxx.py` 或 `tests/test_xxx_helpers.py`。
3. 如果改到 `core/`、`tests/conftest.py`、多模块共用函数，或一次改了多个功能模块，就补跑 `pytest -q`。
4. 准备提交、合并、交付前，仍建议再跑一次 `pytest -q` 做整体验证。

当前常用对应关系：

- `handlers/chat.py` → `pytest tests/test_chat_helpers.py -q`
- `handlers/commands.py` → `pytest tests/test_commands_helpers.py -q`
- `handlers/reactions.py` → `pytest tests/test_reactions_helpers.py -q`
- `features/impression.py` → `pytest tests/test_impression_helpers.py tests/test_impression_rescue_regression.py -q`
- `features/verify.py` → `pytest tests/test_verify_helpers.py -q`
- `features/gallery.py` → `pytest tests/test_gallery_helpers.py -q`
- `features/random_paro.py` → `pytest tests/test_random_paro_helpers.py -q`
- `features/random_keyword.py` → `pytest tests/test_random_keyword_helpers.py -q`
- `features/director.py` → `pytest tests/test_director.py -q`
- `features/event_mode.py` → `pytest tests/test_event_mode_helpers.py -q`
- `features/scheduled.py` → `pytest tests/test_scheduled_helpers.py -q`
- `features/gift.py` → `pytest tests/test_gift.py -q`
- `features/rpg/` → `pytest tests/test_rpg.py -q`
- `core/api.py` → `pytest tests/test_api.py -q`
- `core/context.py` → `pytest tests/test_context.py -q`
- `core/data.py` → `pytest tests/test_data.py -q`
- `core/life_state.py` → `pytest tests/test_life_state.py -q`
- `core/memory.py` → `pytest tests/test_memory.py -q`
- `core/retrieval.py` → `pytest tests/test_retrieval.py -q`
- `core/time_awareness.py` → `pytest tests/test_time_awareness.py -q`
- 数据路径 / 测试数据重定向逻辑 → `pytest tests/test_paths.py -q`

当前这套本地测试的设计目标不是“模拟整台云服务器”，而是“假平台 + 真业务逻辑”：

- `tests/conftest.py` 启动时会把 `tests/fixtures/test_data/` 复制到临时目录。
- 通过 `AKITO_DATA_DIR` 把 `core/data.py`、`memory.py`、`verify.py` 等读写路径统一重定向到临时测试数据。
- 通过 `AKITO_SKIP_PLUGIN_LOAD=1` 跳过真实插件加载，避免测试时拉起完整 NoneBot 插件栈。
- `nonebot`、`onebot`、`openai`、`aiohttp`、`nonebot_plugin_htmlrender` 等外部边界都在 `tests/conftest.py` 中替换成 fake / mock。

因此，本地测试能稳定覆盖：

- 纯辅助函数和参数解析
- 名单、记忆、路径、检索等核心逻辑
- “会不会写错文件 / 写到哪儿去”这类数据层问题

但默认不直接覆盖：

- 云端 `/data` 的实时聊天数据
- 真实 QQ 收发链路
- 外部 API 的在线响应

新增或重构功能时，建议优先把可抽离的判断逻辑做成纯函数，然后为它单独补一个 `tests/test_xxx_helpers.py`。这样本地沙箱也能测到核心部件，不会被平台环境卡住。

### 修改 API Key 或管理员 QQ
编辑 `.env` → 重启生效。无需改代码。

### 修改允许的群号
编辑 `.env` 中对应的群号列表（逗号分隔，`GROUP_IMAGE_PERMISSIONS` 为 JSON）→ 重启生效。

### 生产环境部署形态（运维备忘）

> 2026-06 实测记录，避免每次维护重新摸索环境。

- bot 运行在 Docker 容器 **`mybot`** 内；宿主机仓库目录 `/akito_bot` 整体 bind-mount 为容器内 `/app`，容器默认工作目录就是仓库根。
- **宿主机系统 Python 是 3.6（CentOS 自带），跑不动本项目任何代码**（会报误导性的 SyntaxError）。所有 `tools/` 脚本一律进容器跑，依赖 / `.env` / `data/` 在容器环境里全部现成：
  ```bash
  docker exec mybot python tools/<脚本>.py ...
  ```
- 代码是挂载的：宿主机 `git pull` 后容器内代码即同步；但**让运行中的 bot 加载新代码必须 `docker restart mybot`**——群内 `重载配置` 只热重载数据与检索索引，不重载 Python 代码。
- 文档示例中的 `py xxx.py` 是 Windows 启动器写法，Linux 环境读作 `python`。

### 热更新 Prompt 和数据文件
修改 `data/` 下的 JSON 文件后，在群内发送 `重载配置 assets`（更新 JSON 数据）或 `重载配置 persona`（更新人设文本），无需重启。`重载配置 assets` 会同步重建语义检索索引。

### 构建语义检索向量库
修改 `akito_scripts.json`（台词剧本）或 `pjsk_knowledge.json`（PJSK 黑话）后，需要重建 `.npz` 向量库：

```bash
# 先分类剧本（仅首次，或剧本文件重新导入后）
py tools/classify_scripts.py --write --yes

# 构建向量库（需配置 SILICONFLOW_API_KEY + pip install numpy）
py tools/build_embeddings.py all     # 全量构建
py tools/build_embeddings.py scripts # 仅剧本
py tools/build_embeddings.py pjsk    # 仅 PJSK

# 检索精度评测（考题集 tools/eval_set.json；阈值调参看输出末尾的分数统计）
py tools/eval_retrieval.py compare   # cosine 基线 vs 精排逐题对比
py tools/eval_retrieval.py rerank 0.2  # 用指定阈值试跑精排臂
```

生成的 `data/content/*_embeddings.npz` 不纳入 Git（已在 `/data` gitignore）。服务器端部署：本地建好 `.npz` 上传到服务器 `data/content/` 目录，或服务器上直接运行 build 工具。

**向后兼容**：若无 `.npz` 文件或未配置 `SILICONFLOW_API_KEY`，bot 自动降级为原有静态/随机行为，不影响正常对话。

### 维护剧本语料（`akito_scripts.json`）

#### Schema（每条一个对象）

```json
{
  "type":     "home | story | noise",
  "category": "冬弥·彰冬 | VBS伙伴 | VBS虚拟歌手 | 跨团客串 | 其他NPC·路人 | 彰人独白 | 家园·对事件 | 家园·对物品 | 家园·对人&共度 | 其它",
  "topics":   ["音乐·演出", "情绪·内心", ...],
  "cn_key":   "一句中文情境概括（15–30 字），embed 检索键",
  "context":  "前文（日文原文不动）",
  "dialogue": "彰人台词（日文原文不动）"
}
```

- **检索只看 `cn_key`**（runtime 注入仍是原文 `context` + `dialogue`）——中文键消除 home↔story 的语言鸿沟。
- home 的 `cn_key` 自动复用 `context`（本来就是中文概览），无需手动填。
- `category`/`topics` 仅供人工组织浏览和横切筛选，**不参与运行时检索**。

#### Category 闭集（10 类，按优先级）

| 优先级 | 类别 | 含义 |
|--------|------|------|
| 1 | 冬弥·彰冬 | 与青柳冬弥的互动、提及冬弥、或他人对彰冬关系的看法 |
| 2 | VBS伙伴 | 与小豆沢こはね（心羽）、白石杏（アン）的互动 |
| 3 | VBS虚拟歌手 | 与初音ミク、镜音リン、镜音レン、巡音ルカ、MEIKO、KAITO 的互动（VBS SEKAI 内常驻） |
| 4 | 跨团客串 | 与 Leo/need、MMJ、W×S、25時 各团可养成角色的互动（含彰人姐姐东云绘名） |
| 5 | 其他NPC·路人 | 与白石谦、古柳大河、凪、远野新等传奇/对手，或社长、店员、路人等 |
| 6 | 彰人独白 | 无特定他人、纯彰人内心独白或旁白 |
| 7 | 家园·对事件 | 家园系统：对某事件/状况的看法 |
| 8 | 家园·对物品 | 家园系统：对家具/物品的看法 |
| 9 | 家园·对人&共度 | 家园系统：对某人的看法 & 与某人共度的经历回忆 |
| 10 | 其它 | 兜底 |

#### Topics 标签（9 类，多选）

| 标签 | 含义 |
|------|------|
| 音乐·演出 | 唱歌、表演、LIVE、舞台 |
| 街头·比赛 | 街头表演、RAD WEEKEND、竞赛对抗 |
| 练习·努力·信念 | 练习、排练、坚持、梦想 |
| 过去·RAD WEEKEND | RAD WEEKEND 历史、谦/大河/凪的过去 |
| 情绪·内心 | 心境、烦恼、孤独、喜悦、反思 |
| 怕狗 | 狗/怕狗/犬相关 |
| 足球·过去 | 足球、棒球等运动或过去经历 |
| 游戏黑话·抽卡 | PJSK 游戏机制、抽卡、打歌黑话 |
| 其它话题 | 兜底 |

#### 可养成角色花名册（26 人）

| 归属 | 成员 |
|------|------|
| 冬弥·彰冬 | 青柳冬弥（トウヤ） |
| VBS伙伴 | 小豆沢こはね、白石杏（アン） |
| VBS虚拟歌手 | 初音ミク、镜音リン、镜音レン、巡音ルカ、MEIKO、KAITO |
| 跨团客串 | Leo/need：星乃一歌、天马咲希、望月穗波、日野森志步<br>MMJ：花里实乃理、桐谷遥、桃井爱莉、日野森雫<br>W×S：天马司、凤えむ、草薙寧々、神代类<br>25時：宵崎奏、朝比奈まふゆ、东云绘名(=彰人姐姐)、晓山瑞希 |
| 其他NPC·路人 | 26 人外的任何人物（白石谦、古柳大河、凪、远野新、社长、店员、路人等） |

#### 加新剧本内容的工作流

1. **编辑** `data/content/akito_scripts.json`，在数组中追加条目。必填 `type`（home/story）、`context`（日文前文）、`dialogue`（彰人台词）。`category`/`topics`/`cn_key` 留空。
2. **分类打标**（仅首次/大量改）：
   ```bash
   py tools/classify_scripts.py --write --yes
   ```
3. **LLM 富集**（补 category/topics/cn_key，断点续跑）：
   ```bash
   py tools/enrich_scripts.py --write
   ```
4. **重建向量库**（embed key=cn_key）：
   ```bash
   py tools/build_embeddings.py scripts
   ```
5. 上传 `.npz` → `重载配置 assets`（或重启）。

> ⚠️ `.env` 需要 `DEEPSEEK_API_KEY`（富集用）和 `SILICONFLOW_API_KEY`（embed 用）。缺少任一 key 则跳过对应步骤，bot 自动降级。

### 新增歌曲知识
在 `data/content/akito_songs.json` 追加一个 key：
```json
"song_key": {
  "song_name": "《歌名》",
  "description": "50-120 字第一人称情感回忆",
  "keywords": ["歌名", "别名"]
}
```
`keywords` 会被 `get_song_mention(text)` 做不区分大小写的子串匹配；只收高区分度别名，避免把日常词也加进去。
热更新后会先静态注入曲名清单；消息命中 `keywords` 时再额外注入对应歌曲记忆，无需改代码。

### 新增剧本台词 & type 字段
在 `data/akito_scripts.json` 追加条目，每条需含 `type` 字段（`home`/`story`/`noise`），仅 `home`（中文 context，约 176 条）参与语义检索。
`type` 字段由 `tools/classify_scripts.py` 自动打标，`SCRIPT_DB` 加载零改动（consumer 用 `.get()` 访问）。
修改后需运行 `py tools/build_embeddings.py scripts` 重建向量库。

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

8. **`impression.py` 与 chat.py 的 schema 差异**：两者已共用 `core` 的 `client` 与 `core.api` 的 JSON 提取/救援工具，但 impression 的 JSON schema 是两字段（`reply`，无 `action`），chat.py 是三字段；救援字段集也不同（impression 只救 `reply`，chat 救 `dialogue`/`reply`），调用形态不能互换。

9. **handler 注册时机**：`on_command`/`on_message` 在模块被 import 时立即注册。`features/__init__.py` 中缺少某行 `from . import xxx`，对应功能会完全静默失效，不报任何错误。

10. **渲染字体路径**：`random_paro.py` / `random_keyword.py` 用 `os.path.join(os.path.dirname(__file__), "msyhbd.ttc")` 定位字体，`msyhbd.ttc` 必须与模块同目录（`features/`）。

11. **冬弥去向 = 当前 routine 派生 + 连贯锁，单一大脑收敛到 chat.py**：曾有两套「冬弥在哪」逻辑（reactions.py 的 `冬弥呢` 指令 + chat.py 的窄触发片段），推断规则重复且主对话路径无连贯锁，导致队友去向跨轮自相矛盾（如先说去买咖啡、再说请假）。现统一为 `core.life_state.get_toya_anchor()`：读当前缓存 routine（`get_daily_activity` 已置热），同框时段/文本含「冬弥」判定在场，否则给自洽推断规则，并恒附「本轮已述事实不得自相矛盾」连贯锁；chat.py 在涉冬弥话题且非 WL2 时注入到「物理现实」段。独立的 `冬弥呢` 指令与 `toya_radar.json` 已退役。

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

impression.py（群印象 + AutoChat）使用的 schema 是两字段：`{"inner_os": ..., "reply": ...}`，没有 `action` 字段。chat.py 是三字段。两者虽共用 `core.api.extract_json_block` / `rescue_field` 工具，但救援字段集不同（chat 救 `dialogue`/`reply`，impression 只救 `reply`），解析后续的排版逻辑也完全不同，不能互相套用。

---

### 风险十：routine 数据文件的字段结构

`akito_routine.json` 中每个时段的条目必须包含 `status`（文本描述）和 `poke`（list，戳一戳反应词列表）两个字段。若新增条目时遗漏 `poke` 字段，`reactions.py` 的 poke handler 会回落到 `fallback_poke` 而不报错，但戳一戳的个性化反应会失效。

---

## 项目规范

本项目有完整的编码规范文档，位于 `docs/PROJECT_SPEC.md`。所有维护者在修改代码时需遵守其中的约定。

具体内容（命名、导入顺序、类型注解 / docstring、错误处理、全局状态、文件 I/O、版本号与 Commit、
安全规则），以及推送前的质量检查命令（`ruff check nonebot_plugin_akito/`、`pytest tests/ -v`），
**均以 `docs/PROJECT_SPEC.md` 为准**，此处不再重复，避免与规范正文产生分歧。
