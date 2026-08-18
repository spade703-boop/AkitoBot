# 东云彰人 Bot 维护问题清单（Maintenance Backlog）

> 生成日期：2026-08-04
> 范围：基于一次代码审查，聚焦**可维护性**与**架构整洁性**两个维度。
> 排序：按重要性由高到低。每项带稳定编号（M1…M15），可用复选框跟踪进度。

## 图例与用法

- 优先级：🔴 高（结构性、影响面大） / 🟡 中（重复与臃肿） / 🟢 低（值得顺手收拾）
- 每项结构：**问题 → 证据（`文件:行`） → 影响 → 建议**
- 进度标记：`[ ]` 待办　`[~]` 进行中　`[x]` 完成
- 本清单是“活文档”，改动落地后请勾选并在条目末尾补一行 `> 已处理：<commit/PR>`。

## 总体判断

基础扎实、文档完备：三层依赖方向清晰（`features/ → core/ ← handlers/`）、`PROJECT_SPEC` 规范详尽、降级/原子写/热重载模式统一、`rpg/config.py` 有真正的配置校验、`game_store.py` 用钩子注册表解耦、测试量可观（约 9200 行且目录镜像源码）。下列问题都是在“已经不错”的基础上继续提升，不是救火。

贯穿全局的一条主线：**“东云彰人的系统提示词组装”与“数据读写”这两块核心逻辑没有各自的唯一归属，被复制到了多个调用点。** 优先级最高的几项都指向这条主线。

---

## 🔴 高优先级（结构性、影响面大）

### M1. 主对话处理器是一个 ~410 行的上帝函数
- [ ] 待办
- **证据**：`handlers/chat.py:393-804` 的单个 `@chat.handle()`。
- **问题**：顺序编排了 13 个阶段（溯源回复→解析图文→睡眠拦截→算时间→交互对象→检索→记忆融合→导演骰子→拼 prompt→搜索/Agent 循环→解析回复→OOC 过滤→落库→发送），并在函数体内直接读写 `user_mem`、`messages_list` 等共享状态。
- **影响**：最高频代码路径，却几乎无法单元测试；改任何一步都要通读全函数；异常边界只能靠最外层一个大 `try/except` 兜底。
- **建议**：抽成显式流水线（parse → build_context → assemble_prompt → dispatch → parse_reply → post_process → persist → send），每段是可独立测试的纯函数。已抽出的 `_build_*` helper 是好开端，但真正的编排控制流仍全压在此函数里。

### M2. 系统提示词组装逻辑存在三份平行实现
- [x] 完成
- **证据**：`handlers/chat.py:_build_final_system_prompt`、`features/impression/__init__.py:_build_impression_system_prompt`、`features/impression/__init__.py:_build_auto_chat_system_prompt`；共享上下文入口为 `core/prompt_builder.py:build_shared_prompt_context`。
- **问题**：“人设 + 世界线覆写 + 检索片段 + JSON 输出 schema → system prompt”被手写了三遍。
- **影响**：改人设结构、改 JSON 输出格式、改检索策略都要多处同步，极易漏改导致主对话与插嘴行为不一致。**架构整洁性上最实的缺口**——没有任何模块“拥有”彰人的系统提示词。
- **建议**：新增 `core/prompt_builder.py`（或扩展 `context.py`），提供按“任务类型”参数化的 prompt 组装器，三个调用点都走它。
- **已落地（共享上下文层）**：主聊天与自动插嘴已统一复用 `SharedPromptContext`，集中组装人设、关系匹配、剧本示例、PJSK 片段和歌曲片段；检索上下文只构建一次，两个检索源共享同一上下文。关系匹配和主聊天格式化也已收敛到 `core/context.py`。
- **已落地（任务 Prompt 层）**：`core/prompt_builder.py` 现在提供统一五层骨架（环境与状态 → 角色与知识 → 当前任务上下文 → 任务规则 → JSON 输出），并由主聊天、群印象、自动插嘴三个 renderer 分别填充任务差异。三类 JSON schema 改为结构化字段定义统一渲染；旧入口仅保留兼容包装。
- **验收记录**：已增加跨入口共享片段等价、固定 section 顺序、schema 字段集合和动态转义测试；当前全量 `pytest` 与 `ruff` 均通过。
> 已处理：`464dcbd`（共享上下文层）；`7eef890`（任务级 renderer 与统一 schema）。

### M3. 数据层泄漏：原始 SQLite 访问散落在全部三层
- [x] 完成
- **证据**：裸 `sqlite3.connect(DB_PATH)` + 手写 SQL 出现在 `core/memory.py:21,136,200`（合理）、`features/impression/__init__.py:438,490`、`handlers/commands.py:196`。
- **问题**：共享 `messages` 表的结构/列名知识被复制到跨三层的三个文件，违反 spec 自己声明的“core 是数据层”；连接管理不一致（部分 `with`、部分手动 `close()`）。
- **影响**：一次 schema 迁移要动三处，容易漏改或出连接泄漏。
- **建议**：`messages` 表的所有读写收敛到 `memory.py` 的函数里，features/handlers 只调函数、不碰 SQL。
> 已处理：当前工作区变更（commit 待提交）：运行时 SQLite 访问已收口到 `core.memory`，改用 `aiosqlite`，群印象保留单连接异步读会话；依据 2026-08-18 线上库快照补充 `(group_id, id)` 索引，避免稀疏消息 ID 下的窗口查询临时排序，并以进程内写锁串行化消息写入与清群事务。

### M4. 全局可变字典当数据模型，核心实体零类型
- [ ] 待办
- **证据**：全库无类定义（spec §3 亦承认）；`core/game_store.py:249 get_user()` 返回裸 dict，gift/rpg 各自往上挂私有键；`pyproject.toml:101-102` mypy `disallow_untyped_defs=false` + `check_untyped_defs=false`。
- **问题**：User/Group/记忆会话/RPG·gift 记录全是无类型 `dict`，靠散落各处的 `setdefault` 拼字段。
- **影响**：想知道“一条用户记录有哪些字段”必须全仓库 grep；字段拼错不报错；mypy 对这些 dict 完全失明。
- **建议**：至少为共享记录（user / group / 记忆会话 / RPG state）引入 `TypedDict` 或 `dataclass`，并在 `core/` 逐步打开 mypy 的 untyped-def 检查。不必全仓重写，先给最常被传递的几个 dict 定形状。

### M5. 人设 Prompt 文案在“代码硬编码”与“可热重载 JSON”之间随意分布
- [ ] 待办
- **证据**：JSON 侧 `PROMPTS_DB`（`system_header`/`tone_limiter`/`toya_acting_guide`…，可热重载）；代码侧硬编码 `handlers/chat.py:100-147`（`_build_interact_instruction`/`_build_image_director_instruction`）、`core/life_state.py` 的 `get_morning_run_buff`/`get_sleep_buffer_buff`/`get_toya_anchor`/`_TOYA_REASONING`/`check_sleep_status`。
- **问题**：同等重要的人设指令，一部分能热更新、一部分必须改代码重启，没有规则可循；prompt 工程与业务逻辑混在一起。
- **建议**：定一条明确策略——凡属“可调语气/文案”的进 JSON DB（符合 §14），代码里只留控制流。

---

## 🟡 中优先级（重复与臃肿）

### M6. HTML 渲染基础设施四处重复，且并发信号量没真正共享
- [ ] 待办
- **证据**：同一段 `render_*_page` + Jinja 环境 + `_RENDER_SEM = asyncio.Semaphore(2)` 复制在 `features/gift/render.py`、`features/random_paro/render.py`、`features/bond_render.py`、`features/random_paro_render.py`。
- **实质缺陷**：每个 `render.py` 各自 `Semaphore(2)`，所以“全局最多 2 个无头 Chromium 并发渲染”实际是 **4 个**——Chromium 很吃内存，是真实资源风险。
- **建议**：抽到 `features/_shared/htmlrender.py`，用**单个**模块级信号量，模板目录作参数传入。

### M7. 两个功能配置的访问器脚手架完全重复
- [ ] 待办
- **证据**：`features/gift/config.py:244-265` 与 `features/rpg/config.py:778-810` 各有一份一模一样的 `_cfg`/`_copy`/`_error`/`_load_config`/`reload_*` + `DEFAULT_*_CONFIG` 回退模式；`_weighted_choice` 亦有两份（`core/game_store.py:298` 与 gift）。
- **建议**：抽一个共享的 config-base（加载+回退+`_line`/`_error` 格式化）放 core 或 `_shared`。

### M8. `random_paro/__init__.py` 是 1984 行的上帝模块
- [ ] 待办
- **证据**：`features/random_paro/__init__.py`——~30 个 PIL 渲染 helper、模糊匹配、抽卡逻辑、22 个命令，含 5 个面向线上的 `测试…` 预览孪生命令（`:1710-1774`）；`_render_X`/`_build_X`/`_..._from_stats` 四变体包装（`:1136-1224`）是纯样板膨胀。
- **建议**：拆成 `draw.py` / `ranking.py` / `commands.py`（`render.py` 已存在但大部分 PIL 却在 `__init__`）；`测试…` 孪生命令合并进正式 handler，用调试开关或超管门控。

### M9. 模型 ID 等魔法字符串散落
- [ ] 待办
- **证据**：`"deepseek-v4-flash"` 出现在 4 处（`core/api.py:91,112,148` + `features/impression/__init__.py:43`）；`glm-4.6v-flash`/`BAAI/bge-m3`/`BAAI/bge-reranker-v2-m3` 均为裸字面量（`core/api.py:213,594,604`）。
- **影响**：换对话模型要改 4 个地方，且 `impression` 那份会和 `api.py` 悄悄漂移。
- **建议**：core 里一处 `MODEL_CHAT`/`MODEL_VISION`/`MODEL_EMBED`/`MODEL_RERANK` 常量，最好支持 `.env` 覆盖。

### M10. 重复的小工具函数
- [ ] 待办
- **证据**：`core/memory.py:100 _parse_sqlite_timestamp` 与 `features/impression/__init__.py:102 _parse_message_timestamp` 是同一份 SQLite 时间戳解析；`_weighted_choice` 两份（见 M7）。
- **建议**：并入 core 公共工具，单一真相源。

### M11. `core/api.py` 一个文件承载 5 类互不相关的外部依赖
- [ ] 待办
- **证据**：`core/api.py`（666 行）内含 LLM 对话/Agent、JSON 救援工具、Tavily 搜索、GLM-4V 视觉（约 330 行）、embedding/rerank。
- **问题**：虽内聚为“外部 API 封装”，但视觉子系统体量已可独立成模块；JSON 救援其实是 LLM 输出解析，不属 API 调用。
- **建议**：拆为 `api/llm.py` / `api/vision.py` / `api/search.py` / `api/embedding.py`，JSON 救援移到 `core/json_rescue.py`。属渐进式改善，不紧急。

---

## 🟢 低优先级（值得顺手收拾）

### M12. 违反自己规范：源码里硬编码 QQ 号/群号
- [ ] 待办
- **证据**：`features/gift/config.py:34-37` 的 `wedding_invitation.historical_records` 内嵌真实 QQ 号；`core/game_store.py:28` 的 `GLOBAL_PROFILE_SOURCE_GROUP` 默认硬编码群号 `691188576`。
- **问题**：spec §15.3 明令“代码中禁止硬编码 QQ 号”。虽非密钥，但既违规又本应属数据而非代码。
- **建议**：移入 data 文件或 `.env`。

### M13. 有 ruff/pytest/mypy 配置，却无任何自动化质量门
- [ ] 待办
- **证据**：无 `.github/workflows`、无 `.pre-commit-config.yaml`；§15.4 的“推送前检查清单”全靠人肉执行。
- **影响**：约 9200 行测试的价值被“可能忘了跑”稀释；lint/测试回归无法拦在合入前。
- **建议**：加一个 GitHub Actions 跑 `ruff check` + `pytest`（mypy 可选），或加 pre-commit hook。**投入最小、收益最直接。**

### M14. `SCHEMA_VERSION` 写而不用
- [ ] 待办
- **证据**：`core/game_store.py:27` 定义并写入 `SCHEMA_VERSION = 3`，但 `_normalize_data` 从不按版本分支迁移，只做尽力归一化。
- **建议**：要么接上真正的版本化迁移，要么删掉这个会误导后人的字段。

### M15. 复读重生成分支的 JSON 解析路径与主路径不一致
- [ ] 待办
- **证据**：主解析走 `_parse_model_reply → parse_json_object`（`handlers/chat.py:279`），但复读检测后的重生成分支 `handlers/chat.py:768-770` 改用 `json.loads(extract_json_block(...))`，对畸形输出的容错弱于主路径的 rescue 链。
- **建议**：两处统一走同一 `parse_json_object`/`rescue_*` 链。

---

## 建议的前三步（投入产出比最高）

1. **先加 CI（M13）** —— 最低成本，立刻让后续所有重构有安全网。
2. **抽出统一的 prompt 组装器（M2）+ 收敛 SQLite 访问到 memory.py（M3）** —— 消除最大的两处跨模块复制，之后改人设/改表结构只需一处。
3. **给共享 dict 记录定 `TypedDict`（M4）** —— 为拆分上帝函数（M1）和后续所有改动提供类型护栏。

---

## 附：优先级速览表

| 编号 | 优先级 | 主题 | 关键文件 |
|------|--------|------|----------|
| M1 | 🔴 高 | 主对话上帝函数（~410 行） | `handlers/chat.py:393-804` |
| M2 | 🔴 高 | 系统提示词组装三份重复 | `handlers/chat.py`, `features/impression/__init__.py` |
| M3 | 🔴 高 | SQLite 访问散落三层 | `core/memory.py`, `features/impression/__init__.py`, `handlers/commands.py` |
| M4 | 🔴 高 | 无类型 dict 数据模型 | `core/game_store.py`, `pyproject.toml` |
| M5 | 🔴 高 | 人设文案代码/JSON 混放 | `handlers/chat.py`, `core/life_state.py` |
| M6 | 🟡 中 | HTML 渲染四处重复 + 信号量未共享 | `features/*/render.py`, `features/*_render.py` |
| M7 | 🟡 中 | 功能配置访问器脚手架重复 | `features/gift/config.py`, `features/rpg/config.py` |
| M8 | 🟡 中 | `random_paro` 1984 行上帝模块 | `features/random_paro/__init__.py` |
| M9 | 🟡 中 | 模型 ID 魔法字符串散落 | `core/api.py`, `features/impression/__init__.py` |
| M10 | 🟡 中 | 重复小工具函数 | `core/memory.py`, `features/impression/__init__.py` |
| M11 | 🟡 中 | `api.py` 承载 5 类外部依赖 | `core/api.py` |
| M12 | 🟢 低 | 硬编码 QQ/群号（违反 §15.3） | `features/gift/config.py`, `core/game_store.py` |
| M13 | 🟢 低 | 无 CI / pre-commit | 仓库根 |
| M14 | 🟢 低 | `SCHEMA_VERSION` 写而不用 | `core/game_store.py` |
| M15 | 🟢 低 | 复读分支 JSON 解析不一致 | `handlers/chat.py` |
