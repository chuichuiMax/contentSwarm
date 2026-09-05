# 平台内置 Skill 固化

## 目标

将当前环境中以远程方式安装的通用 Skill 纳入 ContentSwarm 源码，使新环境从 Git 部署后自动安装并显示为平台内置能力。

## 范围

- 将 18 个现有远程 Skill 的完整目录纳入 `backend/package/yuxi/agents/skills/buildin`。
- 注册为平台内置 Skill，并保留各 Skill 自带的许可证及辅助资源。
- 启动同步时将同名 `upload` 或 `remote` 记录原位升级为 `builtin`，不改变记录 ID 与启用状态。
- 不提交运行时 `docker/volumes/yuxi` 数据目录。

## 验收清单

- [x] 18 个 Skill 均能通过内置 Skill 规范解析。
- [x] 同名远程记录能够升级为内置记录。
- [x] 启动同步后页面中这些 Skill 归入“内置”。
- [x] 后端 Skill 单元测试通过。
- [x] 代码提交并推送到 `contentSwarm/main`。
