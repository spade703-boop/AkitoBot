# 对话 AI 工具

- `baseline/`：评测集、评测辅助逻辑和基线报告生成入口。
- `rollout/`：离线探针、线上 trace 汇总和灰度验收入口。

对应测试位于 `tests/tools/conversation_ai/`，文档位于 `docs/conversation_ai/`，本地运行数据位于 `data/conversation_ai/`。

执行路线从 [`docs/WORK_PLAN_INDEX.md`](../../docs/WORK_PLAN_INDEX.md) 进入；自动回复和工具链分别看 [`docs/conversation_ai/auto_reply/PLAN.md`](../../docs/conversation_ai/auto_reply/PLAN.md) 与 [`docs/conversation_ai/tooling/PLAN.md`](../../docs/conversation_ai/tooling/PLAN.md)。

## 运行时观测

- `AKITO_CONVERSATION_TRACE_PATH`：将脱敏后的 turn trace 追加写入 JSONL；自动插嘴会写入 `auto_reply_shadow` 评估，不保存原消息或回复正文。
- `AKITO_M3_TOOL_MODE`：联网工具链模式，支持 `off`、`shadow`、`canary`、`on`，默认 `off`。
- `AKITO_M3_TOOL_GROUPS`：按群覆盖 M3 模式的 JSON 映射，例如 `{"123": "canary"}`。

M3 的 `shadow` 对明确联网请求运行有界新循环但丢弃其结果，随后仍发送旧链路回复；普通请求不会因 shadow 主动联网。`canary` 和 `on` 才会让新循环结果生效。异常时优先把群覆盖改回 `off` 或全局关闭，恢复旧路径。
