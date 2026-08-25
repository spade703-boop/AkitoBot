# M0 对话基线报告

> 这是 M0 的可重复基线。剧情样例使用原作证据进行结构化评测，不进行逐字匹配。

## 评测集

- 样例总数：68
- 类别分布：{"auto_chat": 4, "casual": 8, "follow_up": 6, "group_relay": 6, "impression": 4, "memory": 4, "output_robustness": 4, "plot_recall": 12, "time_gap": 4, "toya_interaction": 8, "vision": 4, "web_search": 4}
- 表面分布：{"auto_chat": 4, "impression_analysis": 2, "impression_reply": 2, "main_chat": 60}
- 已提供回复：0
- 剧情回忆样例：12 条，均绑定原作场景证据

## 回复诊断

- 期望信号字面覆盖率：待采集
- 禁止信号触发率：待采集
- 连续完全复读率：待采集
- AI 裁判样例数：0
- AI 裁判平均分：待采集

## 运行时指标

- 尚无在线回合 trace；启动 bot 并完成评测集回放后再填充。

## 解释边界

- 原作台词用于核对事实、关系和情绪方向，不是唯一正确答案。
- 字面信号覆盖率仅用于诊断，不能替代角色裁判分数。
- AI 裁判应尽量使用与生成模型不同的模型，并抽样人工复核。

## 复现方式

- 校验评测集：`python tools/evaluate_conversation_baseline.py --validate-only`
- 读取回放结果：`python tools/evaluate_conversation_baseline.py --responses path/to/responses.jsonl`
- 汇总在线 trace：设置 `AKITO_CONVERSATION_TRACE_PATH` 后运行 `python tools/evaluate_conversation_baseline.py --traces path/to/traces.jsonl`
- 启用结构化 AI 裁判：追加 `--judge --judge-model <model>`，并配置 `DEEPSEEK_API_KEY`
- 回放结果每行至少包含 `{"id":"casual-001","response":"..."}`；可选附带 `judge` 字段
