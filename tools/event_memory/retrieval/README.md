# EventMemory 检索工具

- `build_index.py`：为编译后的 `akito_event_memories.json` 构建 Git 忽略的事件向量索引。
- `evaluate.py` / `eval_set.json`：验证事件召回、拒绝误认和禁止候选降噪。

部署时先检查数据，再构建索引：

```powershell
python tools/event_memory/retrieval/build_index.py --check
python tools/event_memory/retrieval/build_index.py
```

构建过程只调用 SiliconFlow embedding API，不访问剧情 Wiki。构建成功后不能立即切换 `hybrid`：先用同一评测集完成 Lexical/Hybrid 离线对照，再仅在指定测试群启用 canary，达到命中率、误召回率、延迟和稳定性门槛后才扩大群覆盖。任一阶段失败都保持 `lexical`（必要时关闭 `AKITO_M2_MEMORY_MODE`）并保留旧索引；索引缺失、过期或 rerank 不可用时运行时也会安全降级。

索引发布采用全量成功约束：826 条事件必须全部生成 embedding，任一条失败都会终止构建并保留现有正式索引；运行时同样会拒绝事件下标缺失或数组形状异常的索引。

离线词法评测：

```powershell
python tools/event_memory/retrieval/evaluate.py
```
