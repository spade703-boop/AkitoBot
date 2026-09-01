# 自动回复与跨表面评测计划

> 最后核对：2026-09-01。本文是自动回复功能的唯一计划入口；主对话、自动回复和群印象共用的架构任务仍记在 [`../UPGRADE_PLAN.md`](../UPGRADE_PLAN.md)。

## 当前判断

核心观测链路已经落地，但质量验收尚未完成。当前 trace 只保存脱敏元数据；不能因为模型“看起来能答”就跳过离线回放和人工抽查。

| 阶段 | 状态 | 依据/缺口 |
| --- | --- | --- |
| 自动回复 shadow 评估器 | ✅ 已完成 | `AutoReplyShadowReport`、确定性评估和匿名聚合已接入 `core/observability.py` 与 `features/impression/`。 |
| 统一跨表面 trace | ✅ 已完成 | trace 记录 `surface`、`stage`、`context_sources`，可按表面/实验臂聚合。 |
| 行为安全边界 | ✅ 已完成 | 评估器不发送、不重试、不替换上下文，失败时忽略；不新增线上模型调用。 |
| 离线三表面回放 | ⏳ 待完成 | 需要覆盖 `main_chat`、`auto_chat`、`impression_*` 的真实夹具和连续追问。 |
| 线上分面指标 | 🟡 进行中 | 已能采集，但样本量和人工质量标签不足。 |
| 放量验收 | ⏳ 待完成 | 当前 [`../rollout/ACCEPTANCE.md`](../rollout/ACCEPTANCE.md) 是单臂观察，不是 A/B 通过。 |

## 已完成能力

- [x] 评估当前消息、模型输出、锚点和已有群聊上下文，不保存用户原文或回复正文。
- [x] 记录 `should_interject`、`silence_reason`、`anchor_valid`、`current_message_only`、`cross_turn_breach`、`actual_interjected` 和 `relevance`。
- [x] 自动回复入口在成功插嘴、静默、无效锚点和异常路径都写入匿名 shadow 结果。
- [x] 共享 Context Orchestrator 的选择结果和事件记忆来源，支持按表面拆分基线。
- [x] 保留旧路径和灰度开关；shadow 只观测，不改变当前发送行为。

## 待完成任务

### 离线评测

- [ ] 为主动对话增加多轮连续性、追问承接、记忆命中和工具结果使用指标。
- [ ] 为自动回复增加“是否应该插嘴”、静默决策、当前消息锚点、只回应当前消息和群聊越界指标。
- [ ] 为群印象分析/表达分别增加材料 grounding、不确定性边界、称呼、长度、候选多样性和复用控制指标。
- [ ] 先完成三类表面的离线回放，再采集线上分面指标；评测未通过前不勾选放量。

### 线上复核

- [ ] 在 `combined` 与至少一个 `default` 群分别采集足够样本，报告完成率、解析率、Token、延迟、事件命中率和回退率。
- [ ] 按 [`../rollout/PROBE_SET.md`](../rollout/PROBE_SET.md) 复核主动对话、虚构/模糊剧情、自动回复和群印象；只记录 request id、surface、实验臂和简短现象。
- [ ] 将发现的答非所问、无端插嘴、跨轮越界或角色偏移回填到离线夹具，不把用户原文写入报告。

## 验收门槛

1. 现有自动回复触发、静默和 `block=False` 行为不变。
2. shadow 评估失败不影响主流程，且 trace 不包含用户原文、回复正文或完整上下文。
3. 三类 surface 的离线回放通过后，才允许用线上分面数据讨论放量。
4. 任何单臂报告只能说明稳定性观察；扩大范围前必须有对照或明确的回滚依据。

## 证据

- 实现：`nonebot_plugin_akito/core/observability.py`、`nonebot_plugin_akito/features/impression/__init__.py`。
- 测试：`tests/core/test_auto_reply_shadow.py`、`tests/core/test_observability.py`。
- 报告与流程：[`../baseline/M0_BASELINE.md`](../baseline/M0_BASELINE.md)、[`../rollout/ACCEPTANCE.md`](../rollout/ACCEPTANCE.md)、[`../rollout/ACCEPTANCE_TEMPLATE.md`](../rollout/ACCEPTANCE_TEMPLATE.md)。
