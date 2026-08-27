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

- total_turns: 252
- completed_turns: 96
- failed_turns: 0
- silent_turns: 156
- model_calls: 343
- prompt_tokens: 1714336
- completion_tokens: 38684
- total_tokens: 1753020
- avg_tokens: 6956.43
- parse_success_rate: 0.996
- repeat_rate: 0.0
- memory_hit_rate: 0.1825
- event_hit_rate: 0.0687
- fallback_rate: 0.0
- search_requests: 0
- search_success_rate: None
- retries: 0
- context_shadow_reports: 283
- context_shadow_total_blocks: 2719
- context_shadow_estimated_tokens: 648872
- context_shadow_omitted_sources: {}
- p50_latency_ms: 1684.26
- p95_latency_ms: 12520.98
- surface_counts: {'auto_chat': 171, 'impression': 32, 'main_chat': 49}
- stage_counts: {'reply': 32, 'response': 220}
- experiment_arm_counts: {'combined': 233, 'default': 19}
- group_counts: {'1041487251': 3, '691188576': 233, '761599729': 4, 'unknown': 12}
- surface_metrics: {'auto_chat': {'total_turns': 171, 'completed_turns': 15, 'failed_turns': 0, 'silent_turns': 156, 'model_calls': 171, 'prompt_tokens': 951058, 'completion_tokens': 10635, 'total_tokens': 961693, 'avg_tokens': 5623.94, 'parse_success_rate': 0.9942, 'repeat_rate': 0.0, 'memory_hit_rate': 0.0, 'event_hit_rate': 0.0061, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 171, 'context_shadow_total_blocks': 1701, 'context_shadow_estimated_tokens': 360707, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 1340.93, 'p95_latency_ms': 6415.52}, 'impression': {'total_turns': 32, 'completed_turns': 32, 'failed_turns': 0, 'silent_turns': 0, 'model_calls': 74, 'prompt_tokens': 347389, 'completion_tokens': 23462, 'total_tokens': 370851, 'avg_tokens': 11589.09, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.0, 'event_hit_rate': 0.5, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 64, 'context_shadow_total_blocks': 346, 'context_shadow_estimated_tokens': 159518, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 11562.52, 'p95_latency_ms': 19214.95}, 'main_chat': {'total_turns': 49, 'completed_turns': 49, 'failed_turns': 0, 'silent_turns': 0, 'model_calls': 98, 'prompt_tokens': 415889, 'completion_tokens': 4587, 'total_tokens': 420476, 'avg_tokens': 8581.14, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.9388, 'event_hit_rate': 0.0465, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 48, 'context_shadow_total_blocks': 672, 'context_shadow_estimated_tokens': 128647, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 3164.57, 'p95_latency_ms': 3949.75}}
- experiment_arm_metrics: {'combined': {'total_turns': 233, 'completed_turns': 84, 'failed_turns': 0, 'silent_turns': 149, 'model_calls': 310, 'prompt_tokens': 1559680, 'completion_tokens': 33190, 'total_tokens': 1592870, 'avg_tokens': 6836.35, 'parse_success_rate': 0.9957, 'repeat_rate': 0.0, 'memory_hit_rate': 0.1845, 'event_hit_rate': 0.0687, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 259, 'context_shadow_total_blocks': 2528, 'context_shadow_estimated_tokens': 591950, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 1666.53, 'p95_latency_ms': 12268.65}, 'default': {'total_turns': 19, 'completed_turns': 12, 'failed_turns': 0, 'silent_turns': 7, 'model_calls': 33, 'prompt_tokens': 154656, 'completion_tokens': 5494, 'total_tokens': 160150, 'avg_tokens': 8428.95, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.1579, 'event_hit_rate': None, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 24, 'context_shadow_total_blocks': 191, 'context_shadow_estimated_tokens': 56922, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 3123.52, 'p95_latency_ms': 13373.21}}
- experiment_arm_surface_metrics: {'combined': {'auto_chat': {'total_turns': 164, 'completed_turns': 15, 'failed_turns': 0, 'silent_turns': 149, 'model_calls': 164, 'prompt_tokens': 914214, 'completion_tokens': 10165, 'total_tokens': 924379, 'avg_tokens': 5636.46, 'parse_success_rate': 0.9939, 'repeat_rate': 0.0, 'memory_hit_rate': 0.0, 'event_hit_rate': 0.0061, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 164, 'context_shadow_total_blocks': 1640, 'context_shadow_estimated_tokens': 347058, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 1352.92, 'p95_latency_ms': 6522.47}, 'impression': {'total_turns': 26, 'completed_turns': 26, 'failed_turns': 0, 'silent_turns': 0, 'model_calls': 60, 'prompt_tokens': 279994, 'completion_tokens': 19005, 'total_tokens': 298999, 'avg_tokens': 11499.96, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.0, 'event_hit_rate': 0.5, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 52, 'context_shadow_total_blocks': 286, 'context_shadow_estimated_tokens': 129665, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 11891.44, 'p95_latency_ms': 19218.19}, 'main_chat': {'total_turns': 43, 'completed_turns': 43, 'failed_turns': 0, 'silent_turns': 0, 'model_calls': 86, 'prompt_tokens': 365472, 'completion_tokens': 4020, 'total_tokens': 369492, 'avg_tokens': 8592.84, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 1.0, 'event_hit_rate': 0.0465, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 43, 'context_shadow_total_blocks': 602, 'context_shadow_estimated_tokens': 115227, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 3177.15, 'p95_latency_ms': 3917.94}}, 'default': {'auto_chat': {'total_turns': 7, 'completed_turns': 0, 'failed_turns': 0, 'silent_turns': 7, 'model_calls': 7, 'prompt_tokens': 36844, 'completion_tokens': 470, 'total_tokens': 37314, 'avg_tokens': 5330.57, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.0, 'event_hit_rate': None, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 7, 'context_shadow_total_blocks': 61, 'context_shadow_estimated_tokens': 13649, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 1241.85, 'p95_latency_ms': 1888.68}, 'impression': {'total_turns': 6, 'completed_turns': 6, 'failed_turns': 0, 'silent_turns': 0, 'model_calls': 14, 'prompt_tokens': 67395, 'completion_tokens': 4457, 'total_tokens': 71852, 'avg_tokens': 11975.33, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.0, 'event_hit_rate': None, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 12, 'context_shadow_total_blocks': 60, 'context_shadow_estimated_tokens': 29853, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 10263.41, 'p95_latency_ms': 15103.56}, 'main_chat': {'total_turns': 6, 'completed_turns': 6, 'failed_turns': 0, 'silent_turns': 0, 'model_calls': 12, 'prompt_tokens': 50417, 'completion_tokens': 567, 'total_tokens': 50984, 'avg_tokens': 8497.33, 'parse_success_rate': 1.0, 'repeat_rate': 0.0, 'memory_hit_rate': 0.5, 'event_hit_rate': None, 'fallback_rate': 0.0, 'search_requests': 0, 'search_success_rate': None, 'retries': 0, 'context_shadow_reports': 5, 'context_shadow_total_blocks': 70, 'context_shadow_estimated_tokens': 13420, 'context_shadow_omitted_sources': {}, 'p50_latency_ms': 3124.97, 'p95_latency_ms': 4644.89}}}

## 解释边界

- 原作台词用于核对事实、关系和情绪方向，不是唯一正确答案。
- 字面信号覆盖率仅用于诊断，不能替代角色裁判分数。
- AI 裁判应尽量使用与生成模型不同的模型，并抽样人工复核。

## 复现方式

- 校验评测集：`python tools/conversation_ai/baseline/evaluate.py --validate-only`
- 读取回放结果：`python tools/conversation_ai/baseline/evaluate.py --responses path/to/responses.jsonl`
- 汇总在线 trace：设置 `AKITO_CONVERSATION_TRACE_PATH` 后运行 `python tools/conversation_ai/baseline/evaluate.py --traces path/to/traces.jsonl`
- 启用结构化 AI 裁判：追加 `--judge --judge-model <model>`，并配置 `DEEPSEEK_API_KEY`
- 回放结果每行至少包含 `{"id":"casual-001","response":"..."}`；可选附带 `judge` 字段
