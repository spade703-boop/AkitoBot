# 对话 AI 文档

本目录按“计划、报告、操作资料”分层。总导航见 [`../WORK_PLAN_INDEX.md`](../WORK_PLAN_INDEX.md)；执行路线只看计划文件，报告由工具生成，操作资料不维护路线状态。

## 计划

- [`UPGRADE_PLAN.md`](UPGRADE_PLAN.md)：对话 AI 总路线（M0～M4）和跨功能完成门槛。
- [`auto_reply/PLAN.md`](auto_reply/PLAN.md)：自动回复 shadow、跨表面评测和人工质量复核。
- [`tooling/PLAN.md`](tooling/PLAN.md)：联网工具编排、失败降级、输出安全和灰度上线。
- [`event_memory/REFACTOR_PLAN.md`](event_memory/REFACTOR_PLAN.md)：剧情事件记忆的唯一详细计划。

## 报告

- [`baseline/M0_BASELINE.md`](baseline/M0_BASELINE.md)：离线评测集与线上 trace 基线。
- [`event_memory/M2_EVENT_RECALL.md`](event_memory/M2_EVENT_RECALL.md)：事件召回安全指标。
- [`event_memory/COVERAGE_REPORT.md`](event_memory/COVERAGE_REPORT.md)：已知剧情来源覆盖台账。
- [`rollout/ACCEPTANCE.md`](rollout/ACCEPTANCE.md)：灰度验收快照。
- [`rollout/PROBE_REVIEW_20260827.md`](rollout/PROBE_REVIEW_20260827.md)：人工探针复核记录。

## 操作资料

- [`event_memory/STORY_IMPORT.md`](event_memory/STORY_IMPORT.md)：剧情采集、审核、去重和发布。
- [`rollout/ACCEPTANCE_TEMPLATE.md`](rollout/ACCEPTANCE_TEMPLATE.md)：灰度报告生成与回滚流程。
- [`rollout/PROBE_SET.md`](rollout/PROBE_SET.md)：人工探针问题集。

报告默认输出路径和探针输入路径由 `tools/` 下的脚本固定，迁移计划文件不应改变这些路径。当前模板使用 `data/conversation_ai/traces/conversation_traces.jsonl`；历史验收快照会单独标明其实际来源，不回写历史指标。
