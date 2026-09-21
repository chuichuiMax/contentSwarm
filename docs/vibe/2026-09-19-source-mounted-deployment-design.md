# GitLab 源码挂载部署设计

## 1. 背景与目标

当前开发环境已经通过 bind mount 将部分后端和前端源码挂载进容器，但生产环境以版本化业务镜像为发布单元。每次业务功能更新都需要构建、传输并加载 API、Web 或 HyCanvas 镜像，不符合目标服务器通过 GitLab 拉取源码后直接更新的运维方式。

本次改造的目标是建立独立的“源码挂载部署”模式：

- 运维首次准备固定的基础运行镜像，普通业务功能更新不再构建或传输业务 Docker 镜像。
- 业务源码只保存在服务器 Git 工作区，通过只读 volume 挂载给业务容器。
- Python API、Worker、XHS 浏览器网关和 Sandbox Provisioner 直接执行挂载源码。
- Vue Web 和 HyCanvas 使用固定构建器基础镜像从挂载源码生成宿主机发布产物，运行容器只挂载产物。
- 运维日常操作收敛为拉取 GitLab 最新代码并执行固定的 Docker Compose 部署命令或其薄封装脚本。
- 数据、密钥、源码、缓存和发布产物互相隔离，重新克隆代码不得影响持久化数据。
- 新版本校验、构建或健康检查失败时恢复到更新前版本。

## 2. 范围与非目标

### 2.1 本次范围

- 新增独立的源码部署 Compose，不改变现有开发 Compose 的使用方式。
- 保留现有不可变镜像生产部署，作为另一种部署模式和紧急回退路径。
- 抽离 API、Worker、XHS Gateway、Sandbox Provisioner、Web 和 HyCanvas 的业务源码或业务产物。
- 提供基础镜像、依赖契约、发布构建、健康检查、回滚和部署文档。
- 将生产数据目录从 Git 工作区内部路径抽象为外部 `DATA_ROOT`。

### 2.2 非目标

- 不实现 Kubernetes 部署。
- 不追求无损滚动发布；单机源码部署允许业务服务存在短暂维护窗口。
- 不使用 Vite、watchfiles、Air 等开发热重载工具承载生产流量。
- 不在普通部署时动态修改数据库结构回滚；数据库迁移仍按现有应用机制执行，迁移发布前必须备份。
- 不删除现有版本化镜像发布脚本和生产 Compose。

## 3. 前置假设

- 目标服务器运行 Linux，已安装 Docker Engine 24+ 和 Docker Compose 2.20+。
- 服务器能够访问 GitLab；首次依赖安装或依赖变化时能够访问已配置的 Python、Node 和 Go 内部镜像或公共仓库。
- 基础镜像通过镜像仓库或首次离线导入准备，普通功能更新不需要再次传输基础镜像。
- 日常业务提交不随意修改 Python、Node、Go 或系统依赖；依赖变化走显式基础环境升级流程。
- `.env.prod`、数据库文件、对象存储文件和发布产物不提交到 Git。

## 4. 方案选择

### 4.1 采用方案：基础镜像 + 源码挂载 + 宿主机发布产物

Python 服务从只读挂载目录执行源码；Web 和 HyCanvas 由一次性构建服务生成版本化产物，Nginx 和 HyCanvas Runtime 挂载这些产物运行。普通业务发布不会产生新的 Docker 镜像。

这是本次推荐方案，因为它满足运维只拉代码和启动 Compose 的核心诉求，同时避免将开发服务器直接暴露为生产服务。

### 4.2 不采用：直接复用开发 Compose

虽然现有开发 Compose 已包含源码挂载，但 API 使用 reload、Worker 使用 watchfiles、Web 使用 Vite、HyCanvas 使用开发工具链。这种方式启动快但运行边界、性能和故障行为不适合作为正式部署方式。

### 4.3 不采用：每次由 GitLab CI 构建业务镜像

该方案发布可追溯性最好，但仍然以业务镜像为交付单元，不满足本次“普通功能更新不再打包和传输镜像”的要求。

## 5. 服务器目录边界

```text
/srv/contentswarm/
├── repo/                       # GitLab 工作区
├── config/
│   └── .env.prod              # 生产密钥与部署参数
├── releases/
│   ├── <git-sha>/
│   │   ├── web-dist/
│   │   ├── hycanvas
│   │   └── manifest.env
│   ├── current -> <git-sha>
│   └── previous -> <old-sha>
├── data/
│   ├── postgres/
│   ├── redis/
│   ├── minio/
│   ├── etcd/
│   ├── milvus/
│   ├── neo4j/
│   ├── yuxi/
│   ├── models/
│   └── hycanvas/
└── cache/
    ├── pnpm/
    ├── npm/
    ├── go-mod/
    └── go-build/
```

Compose 从外部环境读取以下路径：

```text
SOURCE_ROOT=/srv/contentswarm/repo
CONFIG_ROOT=/srv/contentswarm/config
RELEASE_ROOT=/srv/contentswarm/releases
DATA_ROOT=/srv/contentswarm/data
CACHE_ROOT=/srv/contentswarm/cache
```

所有业务源码挂载均为只读；只有 `DATA_ROOT`、`RELEASE_ROOT` 和 `CACHE_ROOT` 允许写入。

## 6. 基础镜像职责

### 6.1 API Runtime

API 基础镜像包含 Python 3.12、uv、锁定的第三方依赖、Patchright/Chromium、ffmpeg、字体和系统运行库，不包含 `/app/server` 或 `yuxi` 业务包源码。

API、Worker 和 XHS Gateway 共用该基础镜像，通过以下挂载取得源码：

```text
backend/server  -> /app/server:ro
backend/package -> /app/package:ro
backend/scripts -> /app/scripts:ro
```

容器设置 `PYTHONPATH=/app/package` 和 `PYTHONDONTWRITEBYTECODE=1`，并使用无热重载的生产命令。

基础镜像构建时记录 `backend/uv.lock` 及 `backend/package/uv.lock` 的组合摘要。业务容器启动前比较挂载文件摘要；摘要不一致时拒绝启动并明确提示需要更新 API 基础镜像，避免依赖缺失被延迟到运行期。

### 6.2 Sandbox Runtime

Sandbox Provisioner 基础镜像只包含 Python、Uvicorn、Docker SDK 及其第三方依赖。`app.py` 和 `sandbox.env` 从仓库只读挂载。`requirements.txt` 变化时必须更新该基础镜像或显式同步依赖。

### 6.3 Web Builder 与 Runtime

Web Builder 基础镜像包含 Node 24 和固定版本 pnpm，不包含 Web 源码或 `dist`。它从只读挂载的 `web` 目录安装锁定依赖并构建 `dist`，依赖缓存写入 `CACHE_ROOT`。

Web Runtime 直接使用固定版本 Nginx 基础镜像。Nginx 配置从仓库只读挂载，静态文件从 `RELEASE_ROOT/current/web-dist` 只读挂载。

### 6.4 HyCanvas Builder 与 Runtime

HyCanvas Builder 基础镜像包含 Node 24、Go 1.25 和构建工具，不包含 HyCanvas 源码。它从只读挂载的 `apps/hycanvas` 构建内部包、静态前端和 Linux Go 二进制，缓存写入 `CACHE_ROOT`。

HyCanvas Runtime 基础镜像只包含 Debian Runtime、ffmpeg、CA 证书、curl 和 Noto 字体。运行二进制从 `RELEASE_ROOT/current/hycanvas` 只读挂载，用户文件写入 `DATA_ROOT/hycanvas`。

## 7. Compose 服务设计

新增 `docker-compose.source.yml`，包含以下服务组：

- 基础设施：PostgreSQL、Redis、MinIO、etcd、Milvus、Neo4j、HyCanvas PostgreSQL。
- 一次性任务：`release-builder`、`hycanvas-init`。
- Python 业务服务：`api`、`worker`、`xhs-browser-gateway`、`sandbox-provisioner`。
- 产物运行服务：`web`、`hycanvas-app`。
- 可选重服务：MinerU、PaddleX，继续通过 profile 控制。

源码部署 Compose 不声明任何业务服务的 `build:`。普通部署只使用已经准备好的基础镜像。一次性 Builder 负责编译业务产物，但不执行 Docker 镜像构建。

基础设施数据挂载从仓库相对路径改为 `${DATA_ROOT}`。业务服务依赖关系、健康检查、网络别名和现有环境变量语义保持不变。

## 8. 发布构建与原子切换

`release-builder` 完成以下流程：

1. 读取当前 Git SHA，并确认工作区无未提交的已跟踪文件。
2. 创建 `${RELEASE_ROOT}/<git-sha>.tmp`。
3. 构建 Web `dist`。
4. 构建 HyCanvas 单文件二进制。
5. 校验 Web 入口文件存在、HyCanvas 二进制可执行，并写入包含 Git SHA、构建时间和文件摘要的 `manifest.env`。
6. 将临时目录原子重命名为 `${RELEASE_ROOT}/<git-sha>`。
7. 将原 `current` 保存为 `previous`。
8. 原子切换 `current` 到新 Git SHA。

任一步失败时不得更新 `current`，当前 Web 和 HyCanvas 继续使用旧产物。重复构建同一 Git SHA 时，如 manifest 与源码摘要一致可直接复用；不一致则视为异常并拒绝覆盖。

## 9. 日常部署流程

源码直接挂载意味着 Git 工作区更新期间 Python 服务可能观察到文件变化。为避免同一进程混用新旧文件，部署脚本应先停止直接挂载源码的业务服务，但保持数据库、对象存储、Web 和旧 HyCanvas 运行：

1. 校验当前目录、Git 分支、工作区状态、配置文件和 Compose。
2. 记录部署前 Git SHA 和当前发布产物。
3. 停止 API、Worker、XHS Gateway 和 Sandbox Provisioner。
4. 执行 `git pull --ff-only`，禁止隐式 merge。
5. 校验基础镜像依赖摘要。
6. 运行 `release-builder`。
7. 使用 `--no-recreate` 保持全部持久化服务，再以 `--no-deps --force-recreate` 分阶段重新创建 API、Worker、XHS Gateway、Sandbox Provisioner、Web 和 HyCanvas App；`hycanvas-init` 作为一次性任务执行并删除。
8. 等待 API、Gateway、Worker、Web 和 HyCanvas 健康检查。
9. 成功后记录当前 Git SHA；失败则执行回滚。

标准入口为仓库内的薄封装脚本，例如 `scripts/source-deploy/deploy-source.sh`。脚本只编排 Git 和 Docker Compose，不构建业务镜像。运维也可以按文档逐条执行同样的 Compose 命令。

## 10. 回滚与失败处理

部署失败时：

1. Git 工作区切回部署前提交。
2. `current` 恢复到 `previous` 发布产物。
3. 保持全部持久化服务不重建，重新创建直接挂载源码的 Python 服务和产物运行服务。
4. 再次执行健康检查。
5. 保留失败版本目录和日志用于排查，不自动删除。

数据库迁移是独立风险边界。包含数据库结构变化的发布必须先完成数据库备份和迁移兼容性评审；部署脚本不会自动降级数据库结构。

Git 拉取、依赖摘要校验或 Builder 构建失败时，部署脚本必须恢复旧 Git 提交并重新启动旧后端。健康检查失败时执行相同回滚流程。

## 11. 安全和运行约束

- 不将仓库根目录整体挂载进业务容器，只挂载服务必需目录。
- 源码、脚本和 Nginx 配置使用只读挂载。
- `.git`、`.env.prod`、测试目录和本地开发配置不进入业务容器。
- `.env.prod` 位于仓库外，通过 `--env-file` 和 Compose `env_file` 注入。
- Docker Socket 只挂载到确实需要它的 Provisioner/Worker 服务，并保持现有安全边界。
- 生产服务不使用热重载或开发服务器。
- 基础镜像使用固定版本标签或 digest，不使用 `latest`。
- Builder 使用锁文件和 `--frozen-lockfile` 等严格模式，依赖安装失败即终止发布。

## 12. 计划文件变更

新增：

```text
docker-compose.source.yml
docker/runtime/api-base.Dockerfile
docker/runtime/sandbox-base.Dockerfile
docker/runtime/web-builder-base.Dockerfile
docker/runtime/hycanvas-builder-base.Dockerfile
docker/runtime/hycanvas-runtime-base.Dockerfile
scripts/source-deploy/build-release.sh
scripts/source-deploy/check-runtime-lock.sh
scripts/source-deploy/deploy-source.sh
.env.source.template
docs/advanced/source-deployment.md
```

调整：

```text
.gitignore
docs/.vitepress/config.mts
docs/develop-guides/changelog.md
```

现有 `docker-compose.yml`、`docker-compose.prod.yml` 和生产镜像发布脚本原则上保持兼容，不被源码部署方案替换。

## 13. 验收标准

### 13.1 镜像与源码边界

- API 基础镜像不包含 `backend/server` 和 `backend/package/yuxi`。
- Sandbox 基础镜像不包含仓库中的 `app.py`。
- Nginx 基础镜像不包含 Web `dist`。
- HyCanvas Runtime 基础镜像不包含业务源码或预置业务二进制。
- 普通业务发布前后所有基础镜像 ID 保持不变。

### 13.2 功能更新

- 修改一个 Python 接口后，只拉取 Git、运行 Builder/Compose 并重建服务即可生效。
- 修改 Vue 页面后，不构建 Docker 镜像即可生成并切换新静态产物。
- 修改 HyCanvas 后，不构建 Docker 镜像即可生成并切换新二进制。
- API、Worker、Gateway、Sandbox、Web 和 HyCanvas 均运行对应 Git SHA 的代码或产物。

### 13.3 依赖与失败行为

- Python 锁文件与基础镜像摘要不一致时，部署必须在启动前失败并明确报告原因。
- Web 或 HyCanvas 构建失败时，`current` 不得切换。
- 新版本健康检查失败时，Git SHA 和发布产物均恢复到上一版本。
- 部署失败不得重启或清空 PostgreSQL、Redis、MinIO、Milvus、Neo4j 等持久化数据。

### 13.4 数据与配置

- 删除并重新克隆 `repo` 后，所有持久化数据仍存在。
- `.env.prod` 不在 Git 工作区，也不出现在镜像历史中。
- 所有业务源码挂载为只读，数据和缓存目录权限符合对应容器用户要求。

### 13.5 运维流程

- 全新服务器在准备基础镜像、外部配置和数据目录后，可通过 Git clone、Builder 和 Compose 启动完整系统。
- 日常更新不需要 U 盘、`docker save`、`docker load` 或业务镜像构建。
- 部署脚本输出旧/新 Git SHA、构建产物摘要、容器状态和健康检查结果。
- 运维文档包含首次部署、日常更新、日志查看、依赖升级、回滚和数据备份步骤。

## 14. 实施顺序

1. 建立基础镜像和依赖摘要契约，并验证镜像中没有业务源码。
2. 实现版本化 Web/HyCanvas Builder 和原子发布目录。
3. 新增源码部署 Compose，接入只读源码挂载与外部数据目录。
4. 实现部署、健康检查和回滚脚本。
5. 补充 Compose 静态校验、脚本测试和容器级验收测试。
6. 完善部署文档、导航和变更记录。
7. 在全新环境执行从 Git clone 到服务健康的完整演练，再执行 Python、Web、HyCanvas 三类代码更新和失败回滚演练。
