# Blueprint First 默认工作流部署

目标：将此前仅保存在本地数据库的默认入口切换纳入代码，让同事部署后使用相同配置。

更新代码后执行：

```bash
git pull --ff-only
docker compose up -d --build
```

API 和 Worker 启动时通过现有种子初始化流程发布 `content-workflow-blueprint-first-v2`。新环境的六个行业模板默认绑定此版本；已有环境中由系统创建、仍绑定 `content-workflow-enterprise-v3.7` 的 V3 行业模板自动升级。开发环境已启动时，代码热重载也会执行初始化。

切换只影响新任务。已有任务的版本和快照不迁移；管理员创建的模板及绑定其他工作流版本的模板保留原配置。目标工作流定义与代码不一致或已下架时会停止切换，需检查 API/Worker 的初始化错误日志，不覆盖管理员修改。

该变更不包含业务知识库、模型密钥或用户数据。装修任务仍需已索引的“标题词库”和“正文词库”，Agent 的模型、知识库配置仍需在对应环境配置。

验收清单：

- [x] 新模板默认绑定 Blueprint First v2。
- [x] 旧系统默认入口升级，重复初始化结果一致。
- [x] 自定义绑定和管理员模板不被覆盖。
- [x] 修改过的目标定义阻止自动切换。
- [x] 历史任务版本和快照不变。
- [x] 容器内单元、迁移及入口端到端测试通过。

可运行 `backend/test/unit/content/test_blueprint_first_seed.py`、`backend/test/integration/test_blueprint_first_seed_migration.py` 和 `backend/test/e2e/test_blueprint_first_default.py` 验证；最后一项检查部署的六个默认入口和真实页面，适用于未自定义入口的环境。
