# 工作计划总览

> 最后核对：2026-09-01
>
> 本页是仓库工作计划的导航和状态快照。具体任务只在对应的功能计划中维护；基线、验收报告和操作手册只记录证据或流程，不再复制待办清单。
>
> 范围仅限仓库内的文件；`tmp_pjsk/` 是被 `.gitignore` 排除的外部卡面工作区，不纳入本次计划整合。

## 状态口径

- ✅ **已完成**：代码已经落地，并有测试、评测或可复现命令支撑。
- 🟡 **进行中**：核心链路已落地，但仍有覆盖、上线观察或验收缺口。
- ⏳ **待开始**：尚未实现，或没有足够证据标记完成。
- 📊 **报告/基线**：事实快照，不在报告里维护计划状态。

## 当前进度

| 功能 | 当前状态 | 已完成部分 | 下一步 | 唯一计划入口 |
| --- | --- | --- | --- | --- |
| 对话 AI 总体架构 | 🟡 | M0 数据契约、68 条基线、M1 编排器 shadow/灰度开关 | 补回复样本/角色评分，再做新旧上下文对照、历史摘要和长期记忆迁移 | [`conversation_ai/UPGRADE_PLAN.md`](conversation_ai/UPGRADE_PLAN.md) |
| 自动回复与跨表面评测 | 🟡 | 匿名 shadow 评估、统一 trace、隐私边界 | 三类 surface 离线回放、专属指标和人工复核 | [`conversation_ai/auto_reply/PLAN.md`](conversation_ai/auto_reply/PLAN.md) |
| 事件记忆 | 🟡 | 826 条事件资产、44 条安全召回集、导入/去重/只读召回 | 人工覆盖矩阵、会话承接、Hybrid 对照 | [`conversation_ai/event_memory/REFACTOR_PLAN.md`](conversation_ai/event_memory/REFACTOR_PLAN.md) |
| 工具编排与输出安全 | 🟡 | QueryIntent、ToolResult、有界工具循环和降级已实现 | 完整失败路径测试、canary、M4 质量/安全检查 | [`conversation_ai/tooling/PLAN.md`](conversation_ai/tooling/PLAN.md) |
| RPG 玩法、看板与代码维护 | 🟡 | 成长线、两条战斗线、二期埋点/7/30 日看板，以及公共公式与战斗/事件/奖励拆分已落地 | 历史缺失指标不回填；继续采集并校准成长节奏，推进配置缓存、dataclass、模块拆分和导出审查 | [`rpg/PLAN.md`](rpg/PLAN.md) |

## 当前优先级

1. **先补事件记忆的真实覆盖和角色质量**：当前事件库总量为 826，其中仅 2 条是人工审核剧情，824 条仍是旧脚本生成内容；先扩覆盖和负例，再决定是否切换 Hybrid。
2. **完成跨表面离线回放与人工验收**：`combined` 灰度报告目前是单臂观察，不能当作 A/B 通过或放量结论。
3. **补 RPG 维护性缺口**：配置缓存、配置/玩家 dataclass，以及 `hunt.py`/`rewards.py` 的职责边界；不改变数值和单人/世界 BOSS 的独立设计。
4. **再推进工具链上线**：保持 `off → shadow → canary → on`，补齐超时、错误、提示注入和 0/1/3 次循环验收后再扩大范围。

## 文档地图

### 计划（唯一维护入口）

- [`conversation_ai/UPGRADE_PLAN.md`](conversation_ai/UPGRADE_PLAN.md)：对话 AI 总路线，包含 M0/M1/M2 边界和完成门槛。
- [`conversation_ai/auto_reply/PLAN.md`](conversation_ai/auto_reply/PLAN.md)：自动回复 shadow 与主动对话/自动回复/群印象的跨表面指标。
- [`conversation_ai/tooling/PLAN.md`](conversation_ai/tooling/PLAN.md)：M3 工具编排、M4 输出安全和上线策略。
- [`conversation_ai/event_memory/REFACTOR_PLAN.md`](conversation_ai/event_memory/REFACTOR_PLAN.md)：剧情事件记忆唯一详细计划。
- [`rpg/PLAN.md`](rpg/PLAN.md)：RPG 玩法、看板和代码调优合并计划。

### 报告与基线（由工具生成或定期回填）

- [`conversation_ai/baseline/M0_BASELINE.md`](conversation_ai/baseline/M0_BASELINE.md)：68 条离线样例和线上 trace 基线。
- [`conversation_ai/event_memory/M2_EVENT_RECALL.md`](conversation_ai/event_memory/M2_EVENT_RECALL.md)：44 条事件召回安全评测。
- [`conversation_ai/event_memory/COVERAGE_REPORT.md`](conversation_ai/event_memory/COVERAGE_REPORT.md)：已知来源覆盖台账；分母不是全游戏剧情。
- [`conversation_ai/rollout/ACCEPTANCE.md`](conversation_ai/rollout/ACCEPTANCE.md)：当前灰度验收快照；样本不足或单臂时不作放量结论。
- [`conversation_ai/rollout/PROBE_REVIEW_20260827.md`](conversation_ai/rollout/PROBE_REVIEW_20260827.md)：人工探针缺口和后续复核目标。
- [`rpg/GROWTH_BASELINE.md`](rpg/GROWTH_BASELINE.md)：RPG 数值/成长硬约束（基线，不是路线清单）。

### 操作手册与规范

- [`conversation_ai/event_memory/STORY_IMPORT.md`](conversation_ai/event_memory/STORY_IMPORT.md)：剧情采集网页/CLI、审核和发布流程。
- [`conversation_ai/rollout/ACCEPTANCE_TEMPLATE.md`](conversation_ai/rollout/ACCEPTANCE_TEMPLATE.md)：灰度采集、报告生成和回滚 SOP。
- [`conversation_ai/rollout/PROBE_SET.md`](conversation_ai/rollout/PROBE_SET.md)：人工探针夹具；不在这里维护计划状态。
- [`PROJECT_SPEC.md`](PROJECT_SPEC.md)：编码、数据和安全规范。
- 根目录 [`PLUGIN_MAINTENANCE.md`](../PLUGIN_MAINTENANCE.md)：维护手册；不再承载路线待办。

### 兼容入口

- [`conversation_ai/NON_MEMORY_ROADMAP.md`](conversation_ai/NON_MEMORY_ROADMAP.md)：旧非记忆路线的指针，不维护第二份待办。

## 维护规则

1. 新增或调整待办时，只修改对应功能计划和本页的状态摘要，不在 README、报告或操作手册复制同一份清单。
2. 报告中的数字以生成命令和报告文件为准：基线、事件召回、覆盖台账和灰度验收分别有独立生成入口。
3. 计划中的“已完成”必须能指向代码、测试或可复现命令；仅有设计文档或本地运行样本时标为“进行中”。
4. 历史草案统一放在 [`archive/legacy-plans/`](archive/legacy-plans/) 供追溯，归档规则见 [`archive/README.md`](archive/README.md)，不作为当前执行依据。
