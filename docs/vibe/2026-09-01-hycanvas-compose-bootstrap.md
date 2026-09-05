# HyCanvas 根 Compose 自动配置

## 目标

让新开发环境拉取 ContentSwarm 后，通过根目录 `docker compose up -d` 同时启动并配置 HyCanvas，避免出现“HyCanvas 尚未配置”。

## 范围

- 根 Compose 增加 HyCanvas 应用与独立 PostgreSQL。
- 使用幂等初始化任务创建 ContentSwarm 集成用户、工作区、成员关系和 API Key。
- API 与 Worker 自动获得 HyCanvas 内网地址、浏览器地址、API Key 和工作区 ID。
- 本地提供可直接运行的开发默认值，生产部署必须覆盖密钥。

## 验收清单

- [x] `docker compose config` 校验通过。
- [x] HyCanvas 数据库、应用与初始化任务正常完成。
- [x] ContentSwarm 容器能够访问 HyCanvas 模板接口。
- [x] 浏览器可进入托管模式视觉创作页面。
- [x] 相关测试、Lint 与配置检查通过。
- [x] 提交并推送到 `contentSwarm/main`。
