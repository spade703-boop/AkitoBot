# 对话 AI 工具

- `baseline/`：评测集、评测辅助逻辑和基线报告生成入口。
- `rollout/`：离线探针、线上 trace 汇总和灰度验收入口。

对应测试位于 `tests/tools/conversation_ai/`，文档位于 `docs/conversation_ai/`，本地运行数据位于 `data/conversation_ai/`。

## 运行时观测

- `AKITO_CONVERSATION_TRACE_PATH`：将脱敏后的 turn trace 追加写入 JSONL；自动插嘴会写入 `auto_reply_shadow` 评估，不保存原消息或回复正文。
- `AKITO_M3_TOOL_MODE`：联网工具链模式，支持 `off`、`shadow`、`canary`、`on`，默认 `off`。
- `AKITO_M3_TOOL_GROUPS`：按群覆盖 M3 模式的 JSON 映射，例如 `{"123": "canary"}`。

`shadow` 只运行并记录新工具路由，用户仍收到旧链路回复；`canary` 和 `on` 才会使用有界工具循环。
