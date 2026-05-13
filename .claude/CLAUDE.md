# CLAUDE.md — AkitoBot 项目指令

## 上传 / 推送前

在执行任何 `git push` 或涉及上传代码的操作前，**必须先读取 `.claude/VERSIONING.md`**，确认：

- 无 `/data` 目录内容被追踪
- 无 `.env`、`.claude/settings.local.json` 等敏感文件泄露
- `.env.example` 已同步最新可配置项
