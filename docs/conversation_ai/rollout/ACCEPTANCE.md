# 灰度验收报告

> 本报告只使用匿名化 trace 元数据；它不能替代对实际回复内容的人工抽查。
>
> 这是当前灰度快照；自动回复与工具链的待办分别见 [`../auto_reply/PLAN.md`](../auto_reply/PLAN.md) 和 [`../tooling/PLAN.md`](../tooling/PLAN.md)。

- 结论：**single_arm_observation**
- Trace 文件（本报告历史来源）：`data/conversation_ai/traces/conversation_traces.jsonl`
- 总回合：252（历史快照数字，保留不改）
- 路径核对：仓库根路径 `data/conversation_traces.jsonl` 当前有 863 行；它与上述嵌套路径不是同一批采集，不能合并或互相替代。
- 对照臂：未启用（单臂观察，不提供因果比较）
- 实验臂：`combined`（233 回合）
- 最低样本要求：实验臂 30 回合

## 指标对比

| 指标 | 对照臂 | 实验臂 | 差值/比例 | 状态 |
| --- | ---: | ---: | ---: | --- |
| 单臂绝对指标 | - | - | - | 仅供观察 |
| completed_rate | - | 0.3605 | - | 观察 |
| failed_rate | - | 0.0 | - | 观察 |
| parse_success_rate | - | 0.9957 | - | 观察 |
| p95_latency_ms | - | 12268.65 | - | 观察 |
| avg_tokens | - | 6836.35 | - | 观察 |
| event_hit_rate | - | 0.0687 | - | 观察 |
| fallback_rate | - | 0.0 | - | 观察 |

## 分面与实验臂

- 实验臂分布：`{"combined": 233, "default": 19}`
- 群组分布：`{"1041487251": 3, "691188576": 233, "761599729": 4, "unknown": 12}`
- 表面分布：`{"auto_chat": 171, "impression": 32, "main_chat": 49}`
- 详细分面指标已保存在本报告生成所用的 JSON 汇总中；本 Markdown 不展开每个分面表格。

## 人工复核清单

- [ ] 抽查至少 10 条主动对话：是否答非所问、是否自然承接追问。
- [ ] 抽查至少 5 条剧情回忆：是否错误认领、是否把不确定细节说成事实。
- [ ] 抽查至少 5 条虚构/模糊问法：是否安全拒绝或澄清，而不是补写剧情。
- [ ] 抽查自动回复：不该插嘴时是否保持安静，只回应当前消息。
- [ ] 抽查群印象：材料分析是否中性，最终表达是否仍像彰人。
- [ ] 对发现的问题记录 request id、surface、experiment arm 和简短现象；不要记录用户原文到报告。

## 放量建议

- `insufficient_data`：继续收集，不据此扩大或回滚。
- `single_arm_observation`：单群阶段的稳定性观察，不等价于 A/B 通过。
- `review`：先人工复核回退项和失败样例，必要时切回 `control`。
- `pass`：仍需完成上面的人工清单后，才考虑扩大到更多群。
