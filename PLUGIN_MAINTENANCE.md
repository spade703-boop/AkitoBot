# nonebot_plugin_akito — 维护手册

**角色**：东云彰人（初音未来：缤纷舞台 同人 AI，CP 立场：彰冬不拆不逆）  
**框架**：NoneBot2 + OneBot V11  
**AI 后端**：DeepSeek API / 智谱 GLM-4.6V-Flash（视觉）/ Tavily（搜索）
**文档更新**：2026-09-01

---

## 目录结构

```
nonebot_plugin_akito/
├── __init__.py               # 插件入口：元数据 + require() + 导入三大子包
├── core/                     # 共享基础层（部分模块导入时会加载本地配置）
│   ├── __init__.py           # 常量定义（时区/密钥/客户端/群白名单）+ 统一导出入口
│   ├── ambiguity_guard.py    # 主对话模糊指代护栏
│   ├── context_orchestrator.py # 上下文选择与 Token 预算
│   ├── memory.py             # 长期记忆 JSON 读写 + SQLite 群聊上下文
│   ├── event_memory.py       # 事件记忆召回与安全门控
│   ├── event_memory_scoring.py # 事件候选评分
│   ├── observability.py      # 脱敏 trace 与 shadow 观测
│   ├── rollout.py            # 灰度实验臂与回退
│   ├── story_import.py       # 剧情导入运行时适配
│   ├── data.py               # JSON 数据文件加载（reactions/prompts/routine 等）
│   ├── life_state.py         # 彰人状态机（routine 缓存 / 节日 buff / 安全期管理）
│   ├── api.py                # DeepSeek / 智谱 / Tavily API 封装
│   ├── context.py            # Prompt 组装（人设 / 剧本示例 / 歌曲记忆 / 关系链）
│   ├── prompt_builder.py     # 主聊天 / 群印象 / 自动插嘴的 Prompt 骨架与 schema
│   ├── retrieval.py          # 通用语义检索引擎（BGE-M3 + 均值中心化）
│   ├── retrieval_assets.py   # 检索语料规范化与 Prompt 文本构建
│   ├── time_awareness.py     # 时间流逝感知（追踪群对话 gap，注入时段切换提示）
│   ├── game_store.py         # 共享玩家存储层：积分/亲密度/每日数据 + 签到钩子（gift/rpg 共用）
│   ├── types.py              # core 层 TypedDict 数据结构
│   └── paths.py              # 数据路径定位（find_data_path / get_data_dir）
├── handlers/                 # 主聊天处理层（响应群消息）
│   ├── __init__.py
│   ├── chat.py               # 主对话适配、会话锁与发送出口
│   ├── chat_pipeline.py      # 回合流水线（上下文 + ReAct Agent + 后处理 + 提交）
│   ├── commands.py           # 记忆管理指令（查看/植入/清除/遗忘/重置/热更新）
│   └── reactions.py          # 被动反应（戳一戳 / 深夜自言自语）
└── features/                 # 独立功能模块（按功能分包）
    ├── __init__.py
    ├── _shared/              # 共享资源 / helper（含渲染字体）
    ├── impression/           # 群印象 + 随机插嘴（AutoChat）
    ├── gallery/              # 相册图库指令
    ├── director/             # Galgame 级导演骰子（可安全删除）
    ├── verify/               # 新人审核名单管理
    ├── random_paro/          # 派生抽取器（CP 同人灵感配对）
    ├── random_keyword/       # 今日关键词（同人写作灵感关键词）
    ├── daily_wordcloud/      # 每日群聊词云、热词贡献榜与屏蔽词
    ├── scheduled/            # 定时任务（早晚安 / 过期记忆清理 / 世界 BOSS 收尾）
    ├── event_mode/           # WL2 世界线剧情模式开关
    ├── gift/                 # 送礼系统（积分/送礼/偷分/羁绊/签到闸门/超管重置）
    └── rpg/                  # RPG 子包：签到/打怪/小奇遇/世界BOSS/组队/强化/背包/群排行榜（详见 nonebot_plugin_akito/features/rpg/README.md）
                                   ├── __init__.py
                                   ├── config.py         # 全部数值/文案/配置 + 热更新前强校验
                                   ├── types.py          # RPG 存档与结算 TypedDict
                                   ├── state.py           # RPG 玩家/群状态访问与规范化 helper
                                   ├── player.py         # 经验→等级派生/称号/今日装备 helper/战力计算
                                   ├── fortune.py        # 隐藏运势掷取（含连签保底）+ 签到钩子 on_signin
                                   ├── hunt.py           # 今日打怪指令 + 战斗结果播报
                                   ├── combat.py         # 遭遇/精英/今日增益/胜负判定
                                   ├── events.py         # 战斗事件/援护追击/小奇遇抽取
                                   ├── rewards.py        # 经验积分掉落 + 单人/组队结算
                                   ├── utils.py          # 组队概率/战力与运势公共公式
                                   ├── simulation.py     # 可复现的单人成长模拟
                                   ├── analytics.py      # 30日滚动统计 + 超管 RPG数据
                                   ├── boss.py           # 世界BOSS刷出/强制开启/查询/单人攻击/双人攻击/贡献结算
                                   ├── team.py           # 组队@某人 指令（羁绊事件、小额羁绊增长、失败退化单刷）
                                   ├── smith.py          # 强化/购买装备/重置RPG功能
                                   ├── supply.py         # 每周冒险补给/阶梯成本/战备投放
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
prompt_builder.py (共享 Prompt 骨架与 JSON schema)       │
retrieval.py   (← __init__(np)；惰性 ← data, api)        │
retrieval_assets.py (检索语料规范化，无运行时状态)       │
time_awareness.py (← __init__, data, life_state)         │
game_store.py  (← __init__；gift/rpg 共用存储层)         │
types.py       (共享 TypedDict)                           │
     └────────────────────────────────────────────────── ┘
                           ↓
             handlers/ 和 features/ 优先通过
             `core` 公共接口访问；受控 helper 可显式导入
```

**导入层级规则**：

- `core/` 子模块只能用相对导入 `.` 访问同层文件，**严禁**向上引用 `handlers/` 或 `features/`
- `handlers/` 和 `features/` 优先使用 `from ..core import ...`（两个点 = 上一级包）；共享存储/类型等稳定基础设施可按需显式导入 `core.game_store` / `core.types`
- `handlers/` 和 `features/` 之间**无互相引用**；`features/rpg/` 对 `features/gift/` 有单向依赖：`team.py` / `boss.py` 消费羁绊等级，`inventory.py` 复用礼物结算，gift 不反向依赖 rpg，无环
- `features/scheduled/` 只允许调用 `features/rpg/boss.py` 的世界 BOSS 定时收尾 helper；不得扩散为 feature 之间的循环依赖
- `features/verify/` 无任何内部依赖，完全独立
- `features/director/` 仅被 `handlers/chat.py` 调用，可整体删除（chat.py 有安全降级）

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
GLOBAL_PROFILE_SOURCE_GROUP=691188576  # gift_data v2→v3 合并时的优先来源群
```

> ⚠️ **修改密钥或管理员 QQ 只需改 `.env`，无需动代码。重启后生效。**

---

## core/ — 基础层

### `__init__.py`（含原 constants.py）

无内部依赖。从 `.env` 读取密钥，定义全局常量。普通共享能力通过 `from ..core import ...` 获取；`core.game_store`、`core.types` 等稳定基础设施允许显式导入其子模块。

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
| `GLOBAL_PROFILE_SOURCE_GROUP` | `.env` | 旧版分群玩家数据升级为全局档案时的优先来源群；默认 `691188576` |
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
| `SCRIPT_DB` | `content/akito_scripts.json` | 台词剧本库（list），每条含 `type`/`context`/`dialogue`；检索使用 `cn_key`，缺失时回退 `context` |
| `REACTIONS_DB` | `content/akito_reactions.json` + `content/gallery_text.json` + `content/greetings.json` | 被动反应 / 图库文案 / 问候，合并加载回单一 dict |
| `PROMPTS_DB` | `persona/prompts_system.json` + `persona/prompts_character.json` | Prompt 模板：系统机制 + 角色演绎，合并加载回单一 dict |
| `DIRECTOR_DB` | `akito_director.json` | 导演骰子资产：toya_directions / dynamic_lexicon |
| `DAILY_ROUTINE` | `akito_routine.json` | 每日状态日程，键为时间段（每条含 `status` 和 `poke` 字段） |
| `WL2_ROUTINE` | `wl2_routine.json` | WL2 世界线状态 |
| `SONG_DATA` | `akito_songs.json` | 歌曲背景知识 |
| `RELATIONSHIP_DATA` | `akito_relationships.json` | 人物关系档案（含 `keywords` 关键词） |
| `PJSK_KNOWLEDGE_BASE` | `pjsk_knowledge.json` | PJSK 黑话知识库全文（str，热重载会重新赋值——消费方**必须**经 `get_pjsk_knowledge_base()` 在调用时读取，不可模块级导入旧引用） |
| `PJSK_INTRO` | `pjsk_knowledge.json` 的 `introduction` | 语境锁前言（同上，经 `get_pjsk_intro()` 读取） |
| `PJSK_ENTRIES` | `pjsk_knowledge.json` 的 `knowledge_list` 拍平 | 结构化条目列表 `list[dict]`，每条 `{"category": str, "text": str}`，供检索引擎使用 |

**热更新**：`reload_assets()` 用 `.clear()` + `.update()` / `.extend()` 原地修改所有全局变量，
已持有引用的模块无需重新 import，即时生效。通过 `重载配置 assets` 指令触发。

**公共工具**：`find_data_path(filename)` 定位数据文件；`get_data_dir()` 返回写回文件的统一落点目录
（memory / time_awareness 共用）；`get_pjsk_knowledge_base()` / `get_pjsk_intro()` 实时读取 PJSK 字符串（热重载安全）。

> ⚠️ **Prompt JSON 编辑注意**：当前运行时主要读取 `data/persona/prompts_system.json` 与
> `data/persona/prompts_character.json`；值中不能出现裸 ASCII 双引号 `"`，需用 `\"` 转义或改用中文书名号 `「」`。
> 两个拆分文件都缺失时才会回退旧的 `akito_prompts.json`。

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
| `classify_query_intent(msg)` | 返回 `QueryIntent(intent, explicit_request, explicit_search, query, confidence)`；`intent` 分 `mention` / `local_question` / `web_search`。第三方“要查/正在查”优先判为提及，角色看法/设定优先本地，明确“帮我查/搜一下/查询”才置 `explicit_search=True` |
| `check_sleep_status(msg)` | 北京时间 0–6 点复用 `QueryIntent`：仅 `web_search + explicit_search` 放行并注入“被迫营业”指令；其余消息 80% 返回 `ignore`、20% 从 `sleep_mumbles` 取梦话。返回 `(should_block, instruction)`，注意 `False` 表示继续完整生成 |
| `get_festival_buff(date_obj)` | 返回今日节日 Prompt 片段 |
| `get_morning_run_buff(hour)` | 返回晨跑状态 Prompt（6 点整段生效） |
| `get_sleep_buffer_buff(hour, minute)` | 返回睡前准备状态 Prompt（23:45-23:59 生效），若存在 `previous_context` 则自动注入前一时段的活动记忆 |
| `get_toya_anchor()` | 据当前 routine 推断冬弥去向并附跨轮连贯锁；文本明确提到冬弥时才可据此回答，同框时段只能保守推断“在附近/刚分开/稍后碰头”，没有位置证据时只能遵守普通世界线“住在家里”的保守规则；WL2 或无更高证据时不编造具体位置。由 `chat_pipeline.py` 在涉冬弥话题且非 WL2 时注入 |
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

> `call_deepseek_api_agent` 专供 `chat_pipeline.py` 的 ReAct 循环，其他调用方用 `call_deepseek_api`。

### context.py

| 函数 | 说明 |
|------|------|
| `get_base_persona()` | 读取 canonical path `data/persona/akito_persona.txt` 人设文本 |
| `get_random_examples(n)` | 从 `SCRIPT_DB` 随机抽取 n 条语气示例，并为每条附加“发言者=彰人 / 不得迁移主语、事实和因果”的归因锁（仅检索不可用时兜底） |
| `get_relevant_examples(query, n)` | 语义检索剧本示例；命中条目附 `cn_key` 事实标签与彰人发言者锁；检索不可用时回退带锁随机样本，精排明确无相关时返回空串 |
| `get_relevant_pjsk(query, n)` | 语义检索 PJSK 黑话（检索前与剧本一致做 query 扩散 blend）；检索不可用回退全量 `PJSK_KNOWLEDGE_BASE`，无相关命中仅注入前言（降噪）；`PJSK_INTRO` 始终在前 |
| `get_song_memories()` | 将 `SONG_DATA` 格式化为静态曲名清单，每次对话先注入；具体点名某首歌时再补充详细记忆 |
| `get_song_mention(text)` | 对消息做 `keywords` 子串匹配，命中时最多注入 2 首歌的完整 `description` |
| `get_hybrid_relationship(text)` | 仅扫描本地关系档案并返回 Prompt 片段；不得自行调用 `smart_search`，所有联网统一由 `chat_pipeline.py` 的 `QueryIntent` 调度 |
| `reload_persona()` | 重新读取 `akito_persona.txt`，返回新内容（`重载配置 persona` 触发） |

### retrieval.py

通用语义检索引擎，BGE-M3（1024 维）+ 均值中心化；可用时 cosine 粗召回后经 bge-reranker-v2-m3 精排 + 阈值过滤。设计为 registry 驱动，加新语料只需一条配置（含 `doc_text` 精排文本构造器）+ 跑一次 build。

| 函数 | 说明 |
|------|------|
| `retrieve(corpus, query, top_k)` | 异步语义检索（cosine 召回 → 精排重排）。三态返回：None=不可用（降级）、`[]`=无相关命中、`[id, ...]`=命中的源 DB 下标 |
| `reload_indices()` | 重读所有 `.npz` 并重建缓存，返回成功加载数（`reload_assets()` 联动调用） |

精排开关与调参均为模块常量：`_RERANK_ENABLED`（一键回退纯 cosine）、`_RERANK_RECALL_K`（召回深度，默认 20）、`_RERANK_MIN_SCORE`（相关分阈值，默认 `0.1`；低于此分数的候选视为无相关命中；用 `tools/eval_retrieval.py` 调参）。

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

强/中提示触发时，`chat_pipeline.py` 调用 `chat.py` 的兼容 helper，将 `history` 压缩为背景摘要注释并清空，防止模型续接旧话题。

持久化文件：`data/last_interactions.json`

---

## handlers/ — 指令响应层

### chat.py

主对话适配层。触发条件：消息以 `TRIGGER_NAMES`（`"小彰"` / `"东云小彰"`）开头，且发自 `ALLOWED_CHAT_GROUPS`。实际回合编排位于同目录的 `chat_pipeline.py`；`chat.py` 负责 NoneBot 事件接入、会话锁、发送出口和最外层异常边界。

**完整对话流程**：

```
1. 回复溯源        提取 Reply 引用的原始文本和图片
2. 文本/视觉解析   分离纯文本和图片；图片（最多 3 张）一次调用 GLM-4.6V-Flash 结构化识别
                   （JSON + 布尔特征裁决，26 角色名册，截图自动二次 OCR）
3. 并发保护        asyncio.Lock（per 会话键）防止同一会话并发
4. 睡眠检测        check_sleep_status → 仅明确联网请求可继续；其余 80% 静默 / 20% 梦话
5. Prompt 组装     人设 + 时间感知 + 临时记忆 + 关系链 + 搜索结果 +
                   对话对象态度轴 + 被谈论人物关系轴 + 事实/归因裁决轴 +
                   带主语锁剧本示例 + 歌曲知识 + 导演骰子 + 冬弥去向锚定(get_toya_anchor，涉冬弥且非WL2) + schema 格式指令
6. 查询意图调度    mention/local 直答；明确搜索强制联网；事实候选交给 ReAct Agent
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
无图片 + QueryIntent.explicit_search → 强制 smart_search → _build_search_aside 注入用户消息 → call_deepseek_api
无图片 + QueryIntent.intent=web_search 且非明确搜索 → call_deepseek_api_agent（带 AGENT_TOOLS，LLM 自主决定）
           ├─ 返回 tool_calls → 执行 smart_search → 塞回 messages → call_deepseek_api
           ├─ 返回普通内容   → 直接使用 agent_message.content
           └─ 返回 None（超时）→ call_deepseek_api（降级兜底）
无图片 + mention/local_question → call_deepseek_api（直接生成，不搜索）
```

> 两条搜索路径（明确请求强制 / 事实查询候选由 LLM 自主判断）都把搜索结果回灌进**人设系统提示**重新生成，
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

记忆查询/植入/清除/遗忘指令使用 `ALLOWED_MEMORY_GROUPS` 白名单；`重置对话` 还要求 `SUPERUSER_QQ` 并使用 `ALLOWED_CHAT_GROUPS`，`重载配置` 仅要求 `SUPERUSER_QQ`。私聊事件不受群白名单判断。`_stamp_trigger(event)` 为统一前置函数，所有实际回复的指令处理器在逻辑前调用，负责：
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
> routine 锚定的冬弥位置推断 + 连贯锁现由 `core.life_state.get_toya_anchor()` 提供、在 `chat_pipeline.py` 涉冬弥话题时统一注入（用「小彰冬弥呢」触发）。

> ⚠️ **`poke` 的 routine 获取**：必须无条件调用 `get_daily_activity(hour, weekday, minute)`，
> 让其内部做时段校验和缓存更新。不能用 `if not cached_content` 跳过调用，
> 否则上一时段的脏缓存会一直被复用（例如凌晨状态在白天继续出现）。

---

## features/ — 独立功能模块

### impression.py

> ℹ️ **已并轨**：该文件直接使用 `core` 的共享 `client`（AsyncOpenAI）调用 DeepSeek（带自定义温度/超时参数），
> JSON 提取与救援也统一走 `core.api.extract_json_block` / `rescue_field`。
> 群印象现在分为两个阶段：材料分析输出 `mode/evidence/observations/uncertainties/avoid_patterns`，表达阶段输出 `inner_os/replies`；AutoChat 使用 `inner_os/anchor/reply`，主对话使用 `inner_os/action/dialogue`。

| 功能 | 说明 |
|------|------|
| `recorder` (priority=1) | 静默录制群聊消息到 SQLite `impression_history.db` |
| `um_cmd`（群印象） | 精确指令触发；目标用户最近 50 条整体样本 + 最近 14 天最多 6 段对话片段；支持 @；WL2 状态覆写；先做证据锚定材料分析，再生成 3 条候选并进行称呼/事实/近期表达复用校验；3-5s 思考延迟 |
| `random_chat` (priority=99) | 3% 概率随机插嘴；10 秒冷却；深夜 0-6 点不触发；有 JSON 解析救援；LLM 调用 10 秒超时 |

JSON 输出格式：群印象分析使用 `{"mode": "specific|limited", "evidence": [...], "observations": [...], "uncertainties": [...], "avoid_patterns": [...]}`；表达阶段使用 `{"inner_os": "...", "replies": [...]}`；AutoChat 使用 `{"inner_os": "...", "anchor": "...", "reply": "..."}`，均不含 `action`。

### gallery.py

图片权限由 `GROUP_IMAGE_PERMISSIONS` 控制。本地存储：`data/images/<category>/`

### director.py

Galgame 级导演骰子，由 `chat_pipeline.py` 通过 `chat.py` 暴露的 helper 调用 `build_director_note()`。

**可安全删除**：删除后 `chat_pipeline.py` 自动降级：
- `is_physical_or_drama = False`
- `is_really_spicy = False`
- `acting_guide = ""`（cool_guy_filter 不生效）
- `format_breaker = ""`（不附加导演指令）

### verify.py

完全独立。管理三套新人审核名单，所有指令限 `ADMIN_GROUP_ID` 群使用。

群组配置：`data/verify_config.json` → `{"TARGET_GROUP_ID": "...", "ADMIN_GROUP_ID": "..."}`
（该文件必须存在且两个 key 齐全，否则审核系统整体静默停用并在启动日志告警——群号不在代码内兜底。）

### random_paro/

服务于固定 CP 的派生抽取器。从两个独立身份池随机抽取配对。

- `抽派生` — 受 `ALLOWED_CHAT_GROUPS` 白名单控制
- `派生帮助` — 列出普通用户可用的派生相关指令；仅精确匹配这四个字，带额外参数 / 别名 / 私聊均静默
- 添加/删除指令 — 受 `SUPERUSER_QQ` 权限控制
- 头像拼合：从 `data/images/paro_avatars/彰人/` 和 `data/images/paro_avatars/冬弥/` 按派生名匹配
- 限频：30 分钟内 3 次，`asyncio.Lock` 防并发穿透
- 统计口径：个人页与群级派生角色榜统一按“最终展示结果”累计；定向抽取会计入被固定一侧与随机一侧，狐狸 / 兔子 / 狐兔 / 狐兔饭这类未展示正常角色的结果不计入角色榜
- 运行时缓存：`PARO_STATS` 在模块导入时载入内存；手动替换 `data/paro_stats.json` 后，必须执行 `重载配置 assets` 或重启进程，群里看到的排行才会切到新文件
- 模糊匹配：`_fuzzy_match()` 三级匹配（精确 → 前缀 → 包含），大小写不敏感；歧义时列出候选
- 数据文件：`data/paro_pools.json`（池子）、`data/paro_stats.json`（限频 + 个人/群排行累计）、`data/paro_egg_log.jsonl`（个人做饭/狐兔饭历史）；已接入 `reload_assets()` 热重载

### random_keyword/

同人写作灵感关键词抽取器。从单一关键词池随机抽取 1-3 个意象/情境/关系张力短语。

- `今日关键词` — 受 `ALLOWED_CHAT_GROUPS` 白名单控制，仅支持群聊；普通用户每人每日 1 次，群内同日不放回
- 添加/删除指令 — 受 `SUPERUSER_QQ` 权限控制
- 限频：每日 1 次，基于 `keyword_draws.json` 持久化记录，比较 `datetime.now(TZ_CN).date()` 自动跨天失效
- 群内唯一：普通用户成功抽取后会占用该群当天关键词池；当日池子耗尽则提示次日再来；超管抽取不占用词池
- 并发保护：单个 `asyncio.Lock` 包住整次“读状态 → 过滤候选 → 抽取 → 写状态”，避免同群并发抽到同词
- 模糊匹配：`_fuzzy_match()` 三级匹配（精确 → 前缀 → 包含），大小写不敏感；歧义时列出候选
- PIL 渲染：`_render_categories_image()` 分类展示全池；`今日关键词`命令直接发送文本结果
- 数据文件：`data/fanfic_keywords.json`（关键词池）、`data/keyword_draws.json`（每日抽取记录），已接入 `reload_assets()` 热重载
- 字体：复用 canonical path `nonebot_plugin_akito/features/_shared/msyhbd.ttc`

### daily_wordcloud/

独立记录群成员自然文本，按北京时间自然日生成词频、词云和前三热词贡献榜；不复用 `impression_history.db`，避免改变群印象历史口径。

- `store.py`：`data/daily_wordcloud.db` 的原始消息、日报聚合与超管屏蔽词；原始正文由定时任务保留 7 天
- `analysis.py`：Unicode/链接/CQ 清理、已注册 Bot 指令过滤、Jieba 分词、停用词及全局屏蔽词过滤、确定性排名
- `render.py`：WordCloud 词云和 HTMLRender 日报图片；词云采用橙/蓝/白色阶，消息饼图采用橙/蓝/灰色阶并用深灰表示“其他”（单人 TOP 5，其余合并为“其他”）；榜单头像加载失败时由模板降级为首字占位
- `commands.py`：`今日群聊词云` / `实时群聊词云` / `群聊词云 今天` 对目标群成员开放，并按群共享 30 分钟实时渲染冷却；历史日报查询、`重算群聊词云`、`测试群聊词云`、`词云帮助`、`词云屏蔽词` 和 `词云排除用户` 仍限 `SUPERUSER_QQ`；`回填群聊词云` 也限超管，指定当天时会导入历史快照并标记零点刷新
- `jobs.py`：每天 00:00 发送前一天日报，启动连接时只恢复昨天未发送的日报，并执行 7 天原始消息清理
- `WORDCLOUD_GROUPS`：独立于其他白名单的目标群配置；未配置时只注册空闲任务，不记录或发送
- `WORDCLOUD_HISTORY_DB`：历史回填库路径，默认 `data/impression_history.db`；可指向在线复制的 SQLite 切片
- 屏蔽词和排除用户均为全局配置；前者过滤词 token，后者按 QQ 号过滤整条消息

### scheduled.py

| 任务 | 触发时间 | 说明 |
|------|----------|------|
| `akito_morning` | 06:00 (UTC+8) | 从 `REACTIONS_DB.greetings.morning` 推送到 `TARGET_GROUPS` |
| `akito_night` | 23:50 (UTC+8) | 从 `REACTIONS_DB.greetings.night` 推送（此时处于 `sleep_buffer` 睡前缓冲区） |
| `clean_expired_memory` | 每小时 | 扫描所有会话，清理过期的 temp_implants |
| `world_boss_settlement` | 每日 00:00 (UTC+8) | 结算并广播前一天未完成的世界 BOSS；查询路径仍有幂等补结算兜底 |

所有定时推送前调用 `grant_safety_pass(10)`。

> **睡眠缓冲区（sleep_buffer）**：23:45-23:59 为睡前过渡时段。`get_daily_activity()` 在时段切换时自动保存 `previous_context`，`get_sleep_buffer_buff()` 将其注入 prompt，确保角色在睡前准备中仍能回应前一时段的遗留话题。0:00 后 `check_sleep_status()` 照常接管睡眠拦截。

### event_mode.py

| 指令 | 权限 | 说明 |
|------|------|------|
| `开启WL2模式` | SUPERUSER_QQ | 注入 ID 为 `"WL2"` 的永久临时记忆（expire 2099 年） |
| `关闭WL2模式` | SUPERUSER_QQ | 移除 ID 为 `"WL2"` 的 temp_implant |

WL2 模式影响：impression.py（印象/AutoChat）、reactions.py（戳一戳）、`chat_pipeline.py`（`get_toya_anchor` 同框锚定门控跳过，避免与决裂世界线冲突）。

### gift/

彰冬同人圈主题的群友互送小游戏。通过 `core/game_store.py` 与 RPG 共用全局玩家档案；同一 QQ 的积分、每日闸门、羁绊和 RPG 用户字段跨群共享，群记录只保留成员索引与世界 BOSS 等群级状态。

游戏闭环：`签到` 赚积分 → `送礼@对方` 送随机礼物、累积两人亲密度（同好羁绊）→ `偷@对方` 顺走少量积分（反效果：偷必掉羁绊）→ 循环。羁绊梯由 `gift_config.json` 的 `bond_levels` 驱动，当前共 14 档（6 个非负/正向等级、8 个负向/摩擦等级，从「不共戴天」到「从今往后直到永远」），羁绊越高送礼暴击/共识收益越大，偷的惩罚也越大。被偷保护机制（protect_until + 硬上限）防止泛滥。

| 指令 | 权限 | 说明 |
|------|------|------|
| `签到` | 普通用户 | 每日 1 次领积分 + 搭车 RPG 签到钩子（暗掷运势 + 发经验 + 发今日装备）；签到前随机延迟错开其他 bot |
| `送礼@对方` | 普通用户 | 每日 1 次，系统从「你当前积分买得起的礼物」中随机送；达到 1112 积分后先以独立 40% 概率判定婚礼邀请函，未命中再按原权重抽其他礼物；邀请函基础 +819，仅当送出者从未送过邀请函且该关系尚无 +1314 邀请函时另加 +495，同一关系可重复互送 |
| `偷@对方` | 普通用户 | 每日 2 次，小概率顺走对方少量积分；强保护 + 偷必掉羁绊 |
| `我的积分` | 普通用户 | 查看当前积分余额 |
| `礼物列表` | 普通用户 | 查看所用礼池中各档位的礼物清单 |
| `我的羁绊` | 普通用户 | 带 @ 查看指定群友的亲密度等级/进度；不带 @ 列出自己的羁绊关系 |
| `群羁绊排行` | 普通用户 | 全局所有羁绊关系排行 |
| `测试我的羁绊` | **SUPERUSER_QQ** | 使用固定测试数据渲染「我的羁绊」HTML 卡片预览 |
| `送礼功能帮助` / `送礼帮助` / `送礼说明` | 普通用户 | 指令帮助 |
| `重置送礼` | **SUPERUSER_QQ** | 清空全局送礼/积分/羁绊/RPG 玩家数据及群级状态 |
| `重置本群签到` / `重置全群签到` / `重置签到次数` | **SUPERUSER_QQ** | 清掉当前群成员的全局签到闸门，不改 RPG 连签/装备/运势状态 |
| `重置偷群友` | **SUPERUSER_QQ** | 重置当前群成员的全局偷积分次数、被偷次数与保护闸门，不改积分与羁绊 |

**架构关系**：
- 存储层：`core/game_store.py`（`gift_data.json` schema v3；全局 users/intimacy/counts/wedding_invitations + 群级成员索引/rpg）
- 签到衔接：`gift/` 的签到持锁后调用 `run_signin_hooks`，RPG 的 `fortune.on_signin` 通过 `register_signin_hook` 注册订阅；gift 不反向依赖 rpg
- 配置热更：通过 `data/content/gift_config.json` 覆盖默认值；`reload_assets()` 调用 `gift.reload_gift_config()` 热更新
- 数据文件：`data/gift_data.json`（玩家数据，含积分/送礼/偷/羁绊/RPG 字段）、`data/content/gift_config.json`（配置覆盖与羁绊梯定义）

### rpg/

在送礼社交玩法之上的轻量群文字 RPG。设计原则是：**平时走轻量个人挑战线，低频再用世界 BOSS 承接群体参与感**。不是送礼的附庸，而是和送礼并列的积分去向：给手上有分但一时没地方送礼的人一个稳定的消耗口。

完整架构与指令说明见 `nonebot_plugin_akito/features/rpg/README.md`，此处仅记维护要点：

**文件职责速查**：

| 文件 | 职责 |
|------|------|
| `config.py` | 全部数值（战斗/运势/强化/掉落/连签/精英/小奇遇/冒险补给/世界BOSS）、文案和错误提示的默认配置 `DEFAULT_RPG_CONFIG`；`validate_rpg_config()` 会在启动和热更新前校验怪物、遭遇分段、补给成本/奖池/倍率、概率与称号结构；热更新校验失败时保留当前运行配置 |
| `player.py` | 纯函数：`_level_of(exp)` 经验→等级、`_level_progress(exp)` 进度、`_title_of(level)` 称号分档、`_cum_exp(level)` 升到此级所需累计经验、`_ensure_player(group, uid, name)` 初始化玩家记录；`_combat_power(user)` 计算今日装备隐藏战力；`_resolve_group(event)` 群校验 |
| `fortune.py` | `on_signin(group, uid, rng, today)` 签到钩子入口（暗掷运势 + 发经验 + 今日装备 + 连签记录 + 断签重置）；`_fortune_by_key` 提供运势配置查询；连签保底机制（连凶天数达阈值自动转大吉）。战斗和掉落系数由 `utils.py` 统一接入 |
| `hunt.py` | `今日打怪` / `test打怪掉落` 指令入口和普通战斗播报组装；调用 `rewards._settle_solo` 取得结构化结果，再展示事件、奖励、援护追击和小奇遇，最后触发一次世界 BOSS 刷出判定 |
| `combat.py` | 战斗纯逻辑：按装备等级选择怪物分段和精英概率、计算今日增益、生成有效怪物数值，并由 `resolve_hunt` 完成胜负判定；单刷新手保护 `_rookie_power_factor` 仅在此处维护 |
| `events.py` | 普通战斗事件、组队战斗事件、援护追击场景和单人/双人小奇遇的配置读取与随机抽取；只决定事件结果，不直接修改存档 |
| `rewards.py` | 普通 RPG 的经验、积分、掉落、战备、援护奖励和小奇遇入账；`_settle_solo` / `_settle_coop` 串联 `combat`、`events`、`inventory` 后返回统一结算结果，供 `hunt.py` 与 `team.py` 复用 |
| `utils.py` | 普通组队与世界 BOSS 共用的组队成功率、协作战力加成、失败事件抽取及运势战力/掉落系数，避免两条战斗线重复维护同一公式 |
| `simulation.py` | 使用生产战斗函数和独立随机源模拟“连续签到 + 每日主动单刷”；默认输出 30/90/180/270/360 天等级分布、Lv30 到达时间、总体胜率和成长基线偏差；CLI 入口为 `py tools/simulate_rpg_growth.py` |
| `analytics.py` | 群级 30 日滚动 RPG 聚合数据：普通战斗模式/胜率/投放、组队成立率、冒险补给开启/消耗/固定经验、世界 BOSS 刷新/参与/结算；`RPG数据` 仅超管可查，以图片看板对比近 7/30 天数据，不保存聊天内容 |
| `boss.py` | 世界 BOSS 逻辑：近 7 日活跃签到人数缩放、群级状态 `group["rpg"]["world_boss"]` 持久化、`世界BOSS` / `攻击世界BOSS` / `组队世界BOSS@某人` / `强制开启世界BOSS` / `test世界排行` 指令、贡献榜、按贡献发放经验/积分；12 人后血量规模 `scale_count` 会继续软扩容，但奖励规模 `reward_scale_count` 扩容更慢，避免大群秒杀后奖励也同步爆炸；每个已签到玩家在每只 BOSS 上都有独立的 `participants[uid]` 临时装备与 1 次出手机会；双人挑战会记录轻量羁绊成长；击杀结算还会按 3% 独立概率发放不重复的世界BOSS专属收藏；若当天未击败，则在隔天首次访问相关状态时按已造成进度折算补偿并清场；`强制开启世界BOSS` 仅超管可用，会跳过概率与活跃人数门槛，但不会覆盖当天已存在的 BOSS；奖励不计入 `hunt_total/hunt_wins` |
| `team.py` | `组队@某人` 指令：从 `gift._bond_level` 取羁绊，再通过 `utils._team_success_rate` 判定组队；成功或失败退化单刷分别调用 `rewards._settle_coop` / `_settle_solo`。本模块保留负羁绊摩擦/磨合、每日羁绊增长、组队失败援护和播报逻辑；普通组队结算后同样会触发世界 BOSS 刷出判定 |
| `smith.py` | `强化今日装备` / `强化世界BOSS装备` / `购买装备` / `重置RPG功能` 指令（积分出口 + 超管测试辅助）：两套强化都走 `forge.costs` 分段收费 `[30,60,90]`；世界 BOSS 强化只作用于该 BOSS 的独立临时装备；购买装备花 100 积分重置已损坏普通装备（每天限 1 次，打上 `equip_rebought` 标记，打怪经验和积分减半）；`重置RPG功能` 仅为今天签到过的人重发普通装备，不改运势、连签和世界 BOSS 状态 |
| `supply.py` | `开启冒险补给` 精确指令：读取全局用户的 ISO 周记录，执行 `[140,140,140,140,140,200,300]` 阶梯扣分，每次给 30 经验并按 `34/30/20/12/3/1` 奖池投放一件战备 |
| `inventory.py` | `背包` / `使用 [道具名]` 指令 + `exp_buff`/`exp_grant`/`gift`/`battle_supply`/`battle_guard` 效果；常规战备单槽、护符独立槽；礼物券仍走完整送礼流程 |
| `character.py` | `我的角色` 面板（含称号/战绩/装备/积分/背包/世界BOSS、本周两线投入与当前战备）+ `群排行榜`（本群 exp>0 的人按经验降序 Top 10 图片看板，失败回退文字）+ `冒险帮助` |

**依赖方向**（design constraint）：`features/gift/` 与 `features/rpg/*` 都依赖 `core/game_store.py`；签到走钩子表解耦（gift → `run_signin_hooks` → `fortune.on_signin`，gift 不依赖 rpg）；三条 rpg→gift 单向依赖：`rpg/team.py` / `rpg/boss.py` → `gift._bond_level`（消费羁绊）、`rpg/inventory.py` → `gift._pick_gift_by_name`/`_settle`/`_build_broadcast` 等（礼物券消费走完整送礼流程）；gift 不反向依赖 rpg，无环。

**配置热更**：修改 `data/content/rpg_config.json` → 群内 `重载配置 assets` → `reload_assets()` → `rpg_config.reload_rpg_config()`，无需重启。遭遇分段引用不存在的怪物、旧版位置权重长度不符、分段顺序/概率/称号结构非法时会拒绝更新并保留上一版。

**睡眠写入门控**：北京时间 00:00–06:00，普通用户的签到、送礼、偷取、RPG 战斗/组队、强化、购买等写操作统一拦截，超管可绕过这些指令；`使用` 道具单独拦截且超管也不享有豁免。查询类指令照常。世界 BOSS 每日 00:00 由 `world_boss_settlement` 结算并广播前一天未完成实例，查询入口保留幂等补结算，避免重复发奖。

**数值断言规则**：测试一律从 `rpg_config._cfg(...)` 走读取，不硬编码数字——调 `rpg_config.json` 数值不会导致测试变脆。

**当前维护重点**：
- RPG 的成长硬约束已整理到 `docs/rpg/GROWTH_BASELINE.md`，RPG 玩法/看板/代码维护待办统一在 `docs/rpg/PLAN.md`；以后先看这两份文档，再调签到、怪物、组队或世界 BOSS 数值。
- `py tools/simulate_rpg_growth.py --runs 1000` 默认复现 360 天单人成长；`--strict` 仍以 30/90/180 天基线判定。当前 1000 条轨迹约为 Lv7/Lv14/Lv22/Lv29/Lv37，Lv30 中位第 271 天到达（P10/P90 第 264/280 天）。
- Lv16-30 已按每 3 级一档补齐高阶怪，依次配置 `reward_exp_mult=1.03/1.05/1.07/1.09/1.12`，只修正经验、不增加积分。`combat.encounter_brackets[*].weights` 默认使用“怪物名 → 权重”映射；新增怪物不会进入旧分段，只有在新分段中显式配置后才会出现。继续扩等级时，把当前 `max_level=null` 回退档改成有限上限，再追加新分段和新的 `null` 回退档。
- 小奇遇现在分单人 `events` 与双人 `team_events` 两张表：主动单刷与成功组队都可能触发；双人小奇遇里，道具奖励两人各拿一份，数值奖励按总额对半结算。组队失败后退化单刷时，不应误吃到这层双人奖励。
- 冒险补给按全局玩家跨群共享周次数，满周 7 次共消耗 1200 积分、固定投放 210 经验。常规战备互斥，护符独立；组队各算各的战备，护符失败救场按发起人优先。远征套装不与双倍经验卡叠加或消耗卡片，所有战备都不得进入 `boss.py` 世界 BOSS 链路。
- `weekly_investment` 记录补给次数/支出和普通送礼净支出；送礼返还会降低当周计入值，礼物券不记录。该字段按 ISO 周懒重置，只用于 `我的角色` 的软倾向展示，不限制任一玩法。
- 世界 BOSS 只会在普通 `今日打怪` / `组队@某人` 后以 `3%` 概率刷出；若近 7 日活跃签到人数少于 3，就算随机命中也不会生成。
- 世界 BOSS 强度按近 7 日活跃签到人数缩放，而不是按“今天当前已签到人数”计算，避免早时段触发时血量异常偏小。
- 世界 BOSS 基础奖励仍只有经验和积分；贡献结算会更新玩家 `exp/points`，但不会改普通战绩字段 `hunt_total/hunt_wins`。额外的世界BOSS专属收藏仅作展示，不带数值。
- `core/game_store.py` 必须同时保留顶层全局玩家/社交状态与 `groups[gid]["rpg"]`；旧群数据升级时默认以 `691188576` 为重复用户和重复羁绊的优先来源。

---

## 数据文件清单

> 只读内容文件归入 canonical `data/persona/` 与 `data/content/` 子目录；公共 `find_data_path()` 自动搜这两个子目录与 `data/` 根目录（兼容旧 flat 布局）。`PROMPTS_DB` / `REACTIONS_DB` 由各自拆分文件合并加载，consumer 不感知。
> Git 当前追踪 `data/content/akito_event_memories.json`、`data/content/gift_config.json`、`data/content/rpg_config.json`、`data/content/wordcloud_stopwords.txt`、`data/content/wordcloud_user_dict.txt` 五个静态内容文件；其余 `data/` 内容为本地语料、索引、素材或运行时数据，不纳入 Git。

### 只读内容 — `data/persona/`（人设 + Prompt，热重载）

| 路径 | 说明 |
|------|------|
| `data/persona/akito_persona.txt` | 主人设 Prompt |
| `data/persona/wl2_persona.txt` | WL2 世界线人设 Prompt |
| `data/persona/prompts_system.json` | Prompt 模板·系统机制（system_header / schema_* / memory_capture_rule / memory_*_template） |
| `data/persona/prompts_character.json` | Prompt 模板·角色演绎（vitality / tone_limiter / reliable_mode / cool_guy_filter / toya_*） |

### 只读内容 — `data/content/`（语料 / 行为 / 世界观，热重载）

| 路径 | 说明 |
|------|------|
| `data/content/akito_routine.json` | 每日状态日程（各时段 status + poke 字段） |
| `data/content/wl2_routine.json` | WL2 世界线状态 |
| `data/content/akito_sleep.json` | 睡眠场景文案（complaints / sleep_* 各场景） |
| `data/content/akito_reactions.json` | 被动反应（旧 flat 布局兼容读取；`behavior_seeds` 已随冬弥雷达退役、`fallback_poke` 在 routine.json） |
| `data/content/gallery_text.json` | 图库文案（save_img_replies / send_img_angles） |
| `data/content/greetings.json` | 早晚安问候（morning / night） |
| `data/content/akito_relationships.json` | 人物关系档案（keywords 关键词 + content） |
| `data/content/akito_songs.json` | 歌曲背景知识（song_name / description / keywords；其中 `keywords` 由 `get_song_mention` 消费，用于歌曲别名匹配） |
| `data/content/akito_scripts.json` | 台词剧本库（每条含 `type`/`category`/`topics`/`cn_key`/`context`/`dialogue`；检索 key 为 `cn_key`，缺失回退 `context`） |
| `data/content/pjsk_knowledge.json` | PJSK 黑话知识库（`introduction` + `knowledge_list` → `PJSK_INTRO` + `PJSK_ENTRIES`） |
| `data/content/scripts_embeddings.npz` | 剧本语义向量库（`tools/build_embeddings.py` 生成；embed key=`cn_key`，gitignore） |
| `data/content/pjsk_embeddings.npz` | PJSK 语义向量库（`tools/build_embeddings.py` 生成，gitignore） |
| `data/content/akito_director.json` | 导演骰子资产（toya_directions / dynamic_lexicon） |
| `data/content/akito_event_memories.json` | 事件记忆静态语料（纳入 Git；运行时只读） |
| `data/content/gift_config.json` | 送礼系统默认配置模板（礼物档位/随机事件权重/偷参数/签到延迟，纳入 Git） |
| `data/content/rpg_config.json` | RPG 默认配置模板（战斗/运势/强化/掉落/精英/小奇遇/世界BOSS/文案/错误，纳入 Git） |
| `data/content/wordcloud_stopwords.txt` | 词云静态停用词表（纳入 Git；运行时只读） |
| `data/content/wordcloud_user_dict.txt` | Jieba 词云静态用户词典（纳入 Git；运行时只读） |

### 功能 / 运行时 — `data/` 根目录（多为写回）

| 路径 | 读写 | 说明 |
|------|------|------|
| `data/gift_data.json` | 读写 | gift + rpg 共享数据；顶层 `users[*]` 保存全局玩家档案，顶层 `intimacy/counts/wedding_invitations` 保存全局社交状态，`groups[*].user_ids` 保存群成员索引，`groups[*].rpg` 保存世界 BOSS 等群级状态 |
| `data/akito_memories.json` | 读写 | 核心记忆库（启动时加载，记忆变更时写入） |
| `data/last_interactions.json` | 读写 | 各群最后互动时间戳和 routine 快照（time_awareness.py） |
| `data/impression_history.db` | 读写 | 群消息 SQLite（impression 负责记录，core.memory 提供统一读取接口） |
| `data/paro_pools.json` | 读写 | 派生抽取器池子数据（彰人池 / 冬弥池） |
| `data/paro_stats.json` | 读写 | 派生抽取限频、个人累计与群级排行统计 |
| `data/paro_egg_log.jsonl` | 读写 | 派生做饭 / 狐兔饭历史明细（供个人页回放） |
| `data/fanfic_keywords.json` | 读写 | 今日关键词池子数据 |
| `data/keyword_draws.json` | 读写 | 今日关键词每日抽取记录 |
| `data/daily_wordcloud.db` | 读写 | 词云原始消息（7 天）/日报聚合/全局屏蔽词与排除用户 |
| `data/pending_verify.json` / `bond_verify.json` / `hold_verify.json` | 读写 | 待审核 / 待刷羁绊 / 特殊挂起名单 |
| `data/verify_config.json` | 只读 | 审核系统群号配置 |
| `data/images/<category>/`、`paro_avatars/彰人\|冬弥/` | 读写 / 只读 | 本地图库 / 派生头像素材 |
| `nonebot_plugin_akito/features/_shared/msyhbd.ttc` | 只读 | random_paro / random_keyword 渲染加粗字体 |

---

## 常见维护操作

### 测试与沙箱策略

- 测试结构、目录映射、执行命令、沙箱与 fake 平台约束，统一维护在 `tests/README.md`。
- 这里仅保留维护层策略，不重复列测试文件路径，避免目录调整后出现多份过期说明。

### random_paro 历史统计回补

- 当前运行时会在读取群统计时，按 `groups[].users[*].akito_hits/toya_hits/draw_count/egg_count/foxbun_count` 自动回补旧版 `history` 桶；因此旧口径文件在完成 `重载配置 assets` / 重启后，面板读数会直接按个人累计纠正，不依赖 HTML 层单独修补。
- 若线上旧版 `data/paro_stats.json` 仍存在“个人页已计入定向抽取、群级历史角色榜未计入”的历史口径，可运行 `python -X utf8 tools/backfill_paro_stats.py data/paro_stats.json` 一次性按 `groups[].users[*].akito_hits/toya_hits` 重建 `history` 桶。
- 脚本会先生成同目录 `.bak` 备份；`--dry-run` 只打印变更摘要，不落盘。

维护时默认遵循这四条：

1. 编辑文件后不会自动跑测试，必须手动执行。
2. 先按改动范围跑对应的测试子目录或单文件。
3. 改到 `core/`、`tests/conftest.py`、共享 helper、跨模块逻辑时，补跑 `pytest -q`。
4. 准备提交、合并、发版前，原则上完成一次全量回归。

如果要补测试，优先让测试目录继续跟源码功能结构保持一致；不要恢复成根层大而平的 `tests/test_xxx.py` 布局。

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
在 `data/content/akito_scripts.json` 追加条目，每条需含 `type` 字段（`home`/`story`/`noise`）；`home` 与 `story` 都可参与语义检索，`noise` 仅作为语料保留。
`type` 字段由 `tools/classify_scripts.py` 自动打标，`SCRIPT_DB` 加载零改动（consumer 用 `.get()` 访问）。
修改后需运行 `py tools/build_embeddings.py scripts` 重建向量库。

### 新增人物关系档案
在 canonical path `data/content/akito_relationships.json` 追加一条 entry：
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
编辑 `features/scheduled/__init__.py` 中 `@scheduler.scheduled_job("cron", ...)` 的 `hour`/`minute` 参数。

### 调整随机插嘴概率
`features/impression/__init__.py` 顶部的 `CHAT_PROBABILITY = 0.03`（当前 3%）。

---

## 关键设计说明

1. **QueryIntent + ReAct 搜索调度**：`chat_pipeline.py` 先用 `classify_query_intent()` 把消息分成闲聊提及、本地问题、联网候选。`explicit_search=True` 时直接 `smart_search`；普通 `web_search` 候选才进入 `call_deepseek_api_agent`，返回 `tool_calls` 后执行搜索并二次生成；`mention/local_question` 与图片消息都直接调用标准对话，不经过搜索 Agent。关系档案、RAG 等上下文构建层不得私自联网。

2. **MVVM 渲染分离**：LLM 输出 `action`（动作）+ `dialogue`（台词）纯语义字段，Python 端随机拼装最终格式。history 存储已渲染的纯文本，切断格式复读传染链。

3. **Prompt 设计原则（"正向引导"替代"严禁"）**：`schema_action` 和 `schema_dialogue` 使用正向描述（"写成打字的语感"/"例如「叹气」「抓头发」"），不使用"严禁"句式。大量负向约束会导致模型反复注意被禁止的内容（粉红大象效应），在长 RP 后尤其明显。若未来需要修改 Prompt 约束，优先改写成正向示例和期望形态描述。

4. **`get_daily_activity()` 是 routine 的唯一入口**：它内部处理时段切换检测和缓存清除。任何需要获取当前 routine 的代码，都应直接调用它，而不是先检查 `AKITO_STATUS["cached_content"]` 是否存在再决定是否调用。绕过调用会导致跨时段的脏缓存持续生效。

5. **`AKITO_STATUS` vs 浮点量**：`AKITO_STATUS` 是 dict（可变），`from ..core import AKITO_STATUS` 后操作其字段是安全的。`AKITO_SAFE_UNTIL` 和 `AKITO_LAST_COMPLAINT` 是 float（不可变），必须用 getter/setter。

6. **self_monitor 的超管抑制逻辑**：`last_superuser_trigger_time` 是一个 `{group_id: timestamp}` dict，不是全局单值。A 群超管说话不应压制 B 群的深夜抱怨，`commands.py` 和 `chat_pipeline.py` 更新时都要写 `[group_id]` 子键。

7. **JSON 历史记录格式**：`chat_pipeline.py` 将 assistant 回复以 `{"inner_os": ..., "reply": ...}` 存入 history，但 system prompt 要求输出 `{"inner_os": ..., "action": ..., "dialogue": ...}`。读取历史时（time_awareness 压缩、复读检测等）需同时兼容 `reply` 和 `dialogue` 两个字段名。

8. **群印象与主对话 schema 差异**：两者已共用 `core` 的 `client` 与 `core.api` 的 JSON 提取/救援工具，但群印象分析阶段是 `mode/evidence/observations/uncertainties/avoid_patterns`，表达阶段是 `inner_os/replies`，AutoChat 是 `inner_os/anchor/reply`，主对话是 `inner_os/action/dialogue`；调用形态不能互换。

9. **handler 注册时机**：`on_command`/`on_message` 在模块被 import 时立即注册。`features/__init__.py` 中缺少某行 `from . import xxx`，对应功能会完全静默失效，不报任何错误。

10. **渲染字体路径**：`nonebot_plugin_akito/features/random_paro/` 与 `nonebot_plugin_akito/features/random_keyword/` 统一通过 `nonebot_plugin_akito/features/_shared/__init__.py` 的 `load_msyhbd_font()` 取字体，`msyhbd.ttc` 固定放在 `nonebot_plugin_akito/features/_shared/`。

11. **冬弥去向 = 当前 routine 派生 + 连贯锁，单一大脑收敛到 `chat_pipeline.py`**：曾有两套「冬弥在哪」逻辑（`reactions.py` 的 `冬弥呢` 指令 + 旧主对话窄触发片段）。现统一为 `core.life_state.get_toya_anchor()`：当前状态明确提到冬弥时才据此回答；常见共同活动时段只允许保守推断“在附近/刚分开/稍后碰头”，其余情况不得补写具体位置，并始终附带跨轮连贯锁和普通世界线住处规则。`chat_pipeline.py` 在涉冬弥话题且非 WL2 时注入；独立指令与 `toya_radar.json` 已退役。

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

### 风险二：Prompt JSON 中使用裸 ASCII 双引号

`data/persona/prompts_system.json` 和 `data/persona/prompts_character.json` 的字符串值内的 `"` 必须转义为 `\"`，或改用 `「」`。加载失败时日志只有 WARNING，会静默回落到代码内默认值，功能不报错，行为悄悄退化。

**检查方法**：修改 JSON 文件后用 Python 校验：
```bash
python -c "import json; [json.load(open(path, encoding='utf-8')) for path in ('data/persona/prompts_system.json', 'data/persona/prompts_character.json')]; print('OK')"
```

---

### 风险三：把负向约束加进 Prompt

添加"严禁 X"/"绝对禁止 X"类约束时，模型会在每次生成时都关注 X（粉红大象效应），高创意场景下反而更容易触发 X，并导致回复整体变得机械。

**替代方案**：用正向示例描述期望行为。不写"严禁包含动作描写"，写"写成打字的语感"。

---

### 风险四：修改 `self_monitor` 的超管抑制逻辑

`AKITO_STATUS["last_superuser_trigger_time"]` 必须是 `{group_id: timestamp}` dict。若简化成单个时间戳，超管在 A 群说话会导致 B 群的深夜抱怨也被静默。三处更新位置：`commands.py` 的 `_stamp_trigger()`、`chat_pipeline.py` 的超管触发记录段、`reactions.py` 的 `self_monitor`。

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

`chat_pipeline.py` 存入 history 的 assistant 条目格式：
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

### 风险九：impression.py 的 JSON 格式与主对话不同

群印象已拆成“材料分析 → 表达候选”两次调用：分析使用 `mode/evidence/observations/uncertainties/avoid_patterns`，表达使用 `inner_os/replies`；AutoChat 使用 `inner_os/anchor/reply`，主对话使用 `inner_os/action/dialogue`。它们虽共用 `core.api` 的 JSON 提取/救援工具，但解析和校验逻辑不同，不能互相套用。

---

### 风险十：routine 数据文件的字段结构

`akito_routine.json` 中每个时段的条目必须包含 `status`（文本描述）和 `poke`（list，戳一戳反应词列表）两个字段。若新增条目时遗漏 `poke` 字段，`reactions.py` 的 poke handler 会回落到 `fallback_poke` 而不报错，但戳一戳的个性化反应会失效。

---

## 项目规范

本项目有完整的编码规范文档，位于 `docs/PROJECT_SPEC.md`。所有维护者在修改代码时需遵守其中的约定。

具体内容（命名、导入顺序、类型注解 / docstring、错误处理、全局状态、文件 I/O、版本号与 Commit、
安全规则），以及推送前的质量检查命令（`ruff check nonebot_plugin_akito/`、`pytest tests/ -v`），
**均以 `docs/PROJECT_SPEC.md` 为准**，此处不再重复，避免与规范正文产生分歧。
