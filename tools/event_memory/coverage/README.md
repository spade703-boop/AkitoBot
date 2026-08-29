# 剧情覆盖维护

本目录只保存可提交的剧情来源元数据和评测草稿，不保存剧情原文、缓存或用户 trace。

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

覆盖率分母仅为已知来源，不能用于宣称全游戏剧情覆盖率。AI 分类只写入 `suggested_classification`；评测生成只写入 `eval_drafts.json`。分类和评测问题都必须人工确认，评测批准后才会写入 `retrieval/eval_set.json`。
