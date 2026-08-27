# 灰度验收操作模板

## 1. 采集前确认

- 测试群的 `AKITO_EXPERIMENT_GROUPS` 已映射到 `combined`。
- 至少保留一个未映射群作为 `default` 对照；不要把所有群同时切到实验臂。`default` 默认是 M1 shadow、M2 off，不改变回复行为。
- `AKITO_CONVERSATION_TRACE_PATH` 已配置，且运行用户对目标目录有写权限。
- trace 只包含 schema 版本、UTC 记录时间、群组标识、request id、实验臂、表面、状态、耗时、Token 和事件元数据，不应包含用户原文。

## 2. 生成报告

在生产容器中运行：

```bash
docker exec mybot python tools/conversation_ai/rollout/evaluate.py \
  --traces data/conversation_ai/traces/conversation_traces.jsonl \
  --output docs/conversation_ai/rollout/ACCEPTANCE.md \
  --control-arm default \
  --treatment-arm combined \
  --min-turns 30
```

如果要把未通过自动门槛视为命令失败，追加 `--strict`。样本不足时不要据此扩大或回滚。

当前生产灰度群使用 `combined` 时，未映射群会自动作为 `default` 对照；因此默认命令不需要额外改 `--control-arm`。如果 trace 全部来自单个灰度群，报告会显示 `insufficient_data`，这是预期的安全结果。

如果当前只有一个高频群，直接使用单臂观察模式：

```bash
docker exec mybot python tools/conversation_ai/rollout/evaluate.py \
  --traces data/conversation_ai/traces/conversation_traces.jsonl \
  --output docs/conversation_ai/rollout/ACCEPTANCE.md \
  --treatment-arm combined \
  --single-arm \
  --min-turns 30
```

该模式会输出实验臂的绝对稳定性指标，但明确标注“不提供因果比较”；不能据此宣称新版一定优于旧版。

## 3. 建议探针

- 正常聊天：短问候、连续追问、长间隔后恢复话题。
- 已知剧情：露营、生日、练习、RAD BLAST 等共同经历。
- 虚构剧情：养猫、红色跑车、海外演出等不存在经历。
- 模糊问法：那次、后来呢、他当时怎么说。
- 跨表面：自动回复是否该插嘴、群印象分析是否保持中性。

## 4. 放量决策

先看报告自动结论，再完成报告中的人工复核清单。发现错误认领、细节幻觉、答非所问或自动回复越界时，优先把测试群显式切回 `control`，保留 trace 和 request id 供复盘。
