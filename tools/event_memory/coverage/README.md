# 剧情覆盖维护

本目录只保存可提交的剧情来源元数据和评测草稿，不保存剧情原文、缓存或用户 trace。

维护命令统一使用 Python 3.10；覆盖台账只统计已知来源，不代表全游戏剧情覆盖率。

- `catalog.json`：已抓取、已发布和人工加入待办的已知来源台账。
- `eval_drafts.json`：人工批准前的事件召回评测草稿。
- `core.py`：状态同步、分类确认、评测草稿审批和覆盖报告。
- `cli.py`：无网页环境下的同步、汇总和待办 URL 入口。

常用命令：

```text
python tools/event_memory/coverage/cli.py sync
python tools/event_memory/coverage/cli.py summary
python tools/event_memory/coverage/cli.py add https://pjsk.moe/zh-cn/story/event/140/8/ --priority high
```

覆盖率分母仅为已发布的已知来源，不能用于宣称全游戏剧情覆盖率。`rejected` 只作历史归档，默认隐藏且不参与分类、评测或进度统计。`participant_scope` 指原剧情场景角色范围，不是目标片段提取范围；目标片段仍固定为彰人/冬弥。`source_speakers` 是整页动作中的说话人，`target_speakers` 是实际进入目标片段的说话人，二者不能互换。AI 分类只写入 `suggested_classification`；评测生成只写入 `eval_drafts.json`。分类和评测问题都必须人工确认，评测批准后才会写入 `retrieval/eval_set.json`。

时间阶段按共同经历实际发生的时期判断，不按页面标题判断；如果剧情正在回忆过去，则按被回忆的经历定位。RUSH BEATS 相关阶段依次区分“目标确立”“日本筹备”“已在美国的赛前筹备”“比赛进行中”和“赛后”，其中美国场景不自动等同于比赛已经开始。

召回评测按已发布的共同经历事件（必要时再拆到多个证据单元）覆盖，不要求原剧情中的每一句对白都单独建用例；同一事件用多种自然问法、相邻干扰和事实错误负例验证。
