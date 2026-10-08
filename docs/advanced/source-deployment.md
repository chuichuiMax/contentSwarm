# 源码挂载部署

本文面向维护独立 Linux 服务器的运维人员。该模式把“稳定运行环境”和“业务代码”分开：服务器预先保存不含业务源码的基础镜像，业务代码由 Git 拉取并以只读卷挂载到容器。普通功能更新不构建业务镜像，也不再通过 U 盘传递代码。

> 这不是热重载开发模式。生产进程不使用 `--reload`；部署脚本会停止直接读取源码的服务，更新到一个完整 Git 提交，再整体重建业务容器。

## 适用范围与发布边界

普通发布包含三类内容：

- API、Worker、XHS Gateway 的 Python 源码和 Sandbox Provisioner 源码：从 Git 工作区只读挂载。
- Web 静态文件和 HyCanvas 单文件程序：由固定 Builder 镜像构建到版本化发布目录，运行容器只读挂载 `current` 指针。
- PostgreSQL、MinIO、Neo4j、Milvus、Redis 和 HyCanvas 数据：全部保存在 Git 仓库外的 `DATA_ROOT`，更新代码不会覆盖。

`backend/uv.lock`、`backend/package/uv.lock` 或 Sandbox `requirements.txt` 变化不属于普通源码发布。它们必须先触发对应的基础镜像升级，否则启动前的摘要校验会拒绝运行，避免“代码已更新但容器依赖仍是旧版本”。

Web 同时只读挂载仓库中的 `docker/nginx/nginx.conf` 和 `default.conf`。代理配置更新后必须按源码部署流程重建 Web 容器；只切换 `web-dist` 不会让 Nginx 自动加载新配置。宿主机 TLS 入口需要单独执行 `nginx -t` 和配置重载。案例分享的根路径、`/boyun` 前缀、转发头和公网图片验收要求见[生产部署指南](./deployment.md)，分享链路验收是现有服务健康检查之后的必要发布检查。

## 基础镜像一次性准备

建议由能访问镜像仓库的构建环境构建、扫描并推送带固定版本号或 digest 的镜像。镜像内只有解释器、系统包、第三方依赖和构建工具，不包含 `server`、`package/yuxi`、Web 或 HyCanvas 业务源码。

```bash
docker build -f docker/runtime/api-base.Dockerfile \
  -t registry.example.com/contentswarm/api-runtime:1.0.0 .
docker build -f docker/runtime/sandbox-base.Dockerfile \
  -t registry.example.com/contentswarm/sandbox-runtime:1.0.0 .
docker build -f docker/runtime/web-builder-base.Dockerfile \
  -t registry.example.com/contentswarm/web-builder:1.0.0 .
docker build -f docker/runtime/hycanvas-builder-base.Dockerfile \
  --build-arg WEB_BUILDER_BASE=registry.example.com/contentswarm/web-builder:1.0.0 \
  -t registry.example.com/contentswarm/release-builder:1.0.0 .
docker build -f docker/runtime/hycanvas-runtime-base.Dockerfile \
  -t registry.example.com/contentswarm/hycanvas-runtime:1.0.0 .
```

将这些镜像推送到内网 Registry。隔离网络也可以一次性通过可信介质执行 `docker save` / `docker load`，但之后的业务代码只通过 Git 更新。生产配置中的镜像引用不得使用 `latest`。

## 服务器目录与权限

示例目录如下，目录可以调整，但必须使用绝对路径，并且发布、数据、缓存和配置目录不能放在 Git 工作区中。

```text
/srv/contentswarm/
├── repo/                 # Git 工作区，只保存代码
├── config/
│   ├── .env.source       # Compose 路径、镜像与必填密钥
│   └── .env.config       # 完整业务配置与模型供应商密钥
├── releases/             # <git-sha>/、current、previous
├── data/                 # 数据库、对象存储和业务持久化数据
└── cache/                # pnpm、npm、Go 构建缓存
```

```bash
sudo install -d -m 0750 -o contentswarm -g contentswarm \
  /srv/contentswarm/config \
  /srv/contentswarm/releases \
  /srv/contentswarm/data \
  /srv/contentswarm/cache
```

部署账户需要访问 Git、运行 Docker、读配置目录，并写入 `releases`、`data`、`cache`。只有 Sandbox Provisioner 会按既有架构访问 Docker Socket；不要额外把 Socket 挂载给 Web、API 或数据库服务。

## 配置文件

克隆代码后，把模板复制到仓库外并修改：

```bash
cp /srv/contentswarm/repo/.env.source.template /srv/contentswarm/config/.env.source
cp /srv/contentswarm/repo/.env.template /srv/contentswarm/config/.env.config
chmod 0600 /srv/contentswarm/config/.env.source /srv/contentswarm/config/.env.config
```

`.env.source` 至少要完成这些工作：

- 五个根目录全部写成服务器绝对路径。
- 六个业务基础镜像变量使用已拉取的固定版本或 digest。
- `APP_NETWORK_NAME` 与 `SANDBOX_DOCKER_NETWORK` 保持一致。
- 填写 JWT、数据库、MinIO、HyCanvas、XHS Gateway 等随机生产密钥；`HYCANVAS_API_KEY` 必须以 `hyk_` 开头。
- 填写 `CONTENTSWARM_PUBLIC_URL`、`HYCANVAS_PUBLIC_URL` 和端口。

`.env.config` 保存模型供应商、远程素材等完整业务配置。它由 API 和可选解析服务读取，不进入 Git。不要把真实密钥复制回 `.env.source.template` 或提交到仓库。

以下变量供部署脚本使用，建议写入仅部署账户可读的 shell 环境文件，执行发布前导出：

```bash
export SOURCE_DEPLOY_REPO=/srv/contentswarm/repo
export SOURCE_DEPLOY_RELEASE_ROOT=/srv/contentswarm/releases
export SOURCE_DEPLOY_ENV_FILE=/srv/contentswarm/config/.env.source
export SOURCE_DEPLOY_COMPOSE_FILE=/srv/contentswarm/repo/docker-compose.source.yml
export SOURCE_DEPLOY_BRANCH=main
```

## 首次部署

1. 安装 Git、Docker Engine 和 Docker Compose v2，确认 `docker compose version` 正常。
2. 使用部署账户克隆配置分支：

```bash
git clone --branch main --single-branch <gitlab-repository-url> /srv/contentswarm/repo
cd /srv/contentswarm/repo
git status --short
```

3. 登录 Registry 并拉取基础镜像。`release` profile 必须包含 Builder：

```bash
docker compose \
  --env-file /srv/contentswarm/config/.env.source \
  -f /srv/contentswarm/repo/docker-compose.source.yml \
  --profile release pull
```

4. 执行无副作用预检。它会验证 Git、绝对路径、Compose 配置、本地基础镜像和禁用 `latest`，不会停止、拉取、构建或启动服务：

```bash
bash scripts/source-deploy/deploy-source.sh --validate-only
```

5. 首次发布。脚本内部使用 `git pull --ff-only`，运行锁摘要检查和 Builder，然后启动服务并等待健康：

```bash
bash scripts/source-deploy/deploy-source.sh
```

6. 检查状态和发布记录：

```bash
docker compose \
  --env-file /srv/contentswarm/config/.env.source \
  -f docker-compose.source.yml ps -a
cat .deploy/source-deploy/current-state.env
readlink /srv/contentswarm/releases/current
```

MinerU 和 PaddleX 默认不启动。服务器已准备 GPU、驱动和固定镜像后，可额外使用 `--profile all` 管理这两个服务。

## 日常更新

通常不要由运维手工改工作区。确认 `git status --short` 为空后执行：

```bash
cd /srv/contentswarm/repo
bash scripts/source-deploy/deploy-source.sh --validate-only
bash scripts/source-deploy/deploy-source.sh
```

脚本顺序固定为：预检 → 记录旧状态 → 停止 API/Worker/Gateway/Sandbox → 切回配置分支 → `git pull --ff-only` → 校验依赖锁 → 构建并校验版本化 Web/HyCanvas 产物 → 以 `--no-recreate` 保持持久化服务 → 分阶段重建业务服务 → 健康检查 → 原子写入状态。部署输出会记录旧/新 Git SHA、发布产物摘要、健康检查结果和最终容器状态，可直接归档到运维日志。

如果运维已经明确执行过 `git pull --ff-only` 并核对了目标提交，可避免重复拉取：

```bash
bash scripts/source-deploy/deploy-source.sh --no-pull
```

`--no-pull` 仍会执行全部锁校验、构建、重建和健康检查，不能绕过依赖契约。

## 基础镜像升级

出现以下变化时先发布新基础镜像，再部署源码：

| 变化 | 必须更新的镜像 |
| --- | --- |
| `backend/uv.lock` 或 `backend/package/uv.lock` | API Runtime |
| `docker/sandbox_provisioner/requirements.txt` | Sandbox Runtime |
| Node、pnpm、Go 版本或 Builder 系统依赖 | Web Builder 和 Release Builder |
| HyCanvas 的 ffmpeg、字体或系统运行库 | HyCanvas Runtime |

推荐流程：

1. 在 CI 构建新版本镜像并执行部署单元测试及镜像边界测试。
2. 推送固定版本，服务器执行 `docker pull`。
3. 更新 `/srv/contentswarm/config/.env.source` 中相应镜像引用。
4. 执行 `--validate-only`，确认全部镜像本地可用。
5. 执行正常部署。禁止临时在运行容器内 `pip install` 或 `npm install`。

业务代码没有改变依赖锁时，不需要升级基础镜像。

## 构建缓存与发布产物

Release Builder 只读挂载 `SOURCE_ROOT`，可写挂载 `RELEASE_ROOT` 和 `CACHE_ROOT`。pnpm、npm、Go module 与 Go build 缓存在 `/srv/contentswarm/cache`，可以加快后续构建，但不是运行数据；清理缓存只会使下一次构建变慢。

每个成功版本位于 `/srv/contentswarm/releases/<git-sha>`，包含 `web-dist`、`hycanvas` 和 `manifest.env`。`current` 与 `previous` 是相对符号链接，并通过原子重命名切换。不要手工修改版本目录内容，也不要在服务运行时把 `current` 改成普通目录。

## 日志和状态检查

先定义公共 Compose 参数，减少手工输入错误：

```bash
cd /srv/contentswarm/repo
COMPOSE="docker compose --env-file /srv/contentswarm/config/.env.source -f docker-compose.source.yml"
$COMPOSE ps -a
$COMPOSE logs --tail 200 api
$COMPOSE logs --tail 200 worker
$COMPOSE logs --tail 200 xhs-browser-gateway
$COMPOSE logs --tail 200 sandbox-provisioner
$COMPOSE --profile release logs --tail 200 release-builder
$COMPOSE logs --tail 200 web
$COMPOSE logs --tail 200 hycanvas-app hycanvas-init
```

Builder 与 `hycanvas-init` 均由 `docker compose run --rm` 执行，容器会在成功或失败后删除；完整构建输出保留在部署命令终端或外层日志系统中。运行日志不要写进 Git 工作区。

## 数据备份

代码回滚不会回滚数据库结构或业务数据。包含迁移的发布必须先完成备份和向后兼容评审。建议停止 API、Worker 和会写数据的外部入口后取得同一时间点备份：

- PostgreSQL：分别对主库和 HyCanvas 库执行 `pg_dump`，并定期验证恢复。
- MinIO：备份 `${DATA_ROOT}/milvus/minio` 与配置，或使用 `mc mirror` 复制到独立存储。
- Neo4j：使用对应版本的 `neo4j-admin database dump`；不要只在数据库写入中复制 data 目录。
- Milvus：将 Milvus、etcd、MinIO 三部分作为一个一致性边界备份，优先使用官方备份工具或停写快照。
- HyCanvas：同时备份 `hycanvas-db` 的 PostgreSQL 数据和 `${DATA_ROOT}/hycanvas` 文件存储。
- Yuxi 业务文件与 Redis：按恢复要求备份 `${DATA_ROOT}/yuxi`；Redis 若承载需要恢复的队列状态，同时保存其 AOF。

备份应写到 `DATA_ROOT` 之外，并定期在隔离环境演练恢复。部署脚本不会删除失败版本目录，也不会自动处理数据库降级。

## 自动回滚与显式回滚

Git 拉取、锁摘要校验、Builder 或健康检查失败后，只要部署已进入变更阶段，脚本都会：

1. 切回部署前的精确 Git SHA。
2. 把 `current` 恢复到旧发布产物。
3. 保持 PostgreSQL、Redis、MinIO、etcd、Milvus、Neo4j 和 HyCanvas PostgreSQL 容器不重建，分阶段重建旧业务服务并再次检查健康。
4. 保留失败版本和非零退出码，防止外层系统把回滚成功误报成发布成功。

显式回滚默认使用 `.deploy/source-deploy/current-state.env` 中的上一 SHA：

```bash
bash scripts/source-deploy/rollback-source.sh
```

也可以指定仍存在完整 `manifest.env` 的历史提交：

```bash
bash scripts/source-deploy/rollback-source.sh <full-git-sha>
```

回滚后工作区处于 detached HEAD 是预期行为。下一次正式发布会显式切回 `SOURCE_DEPLOY_BRANCH`，再执行快进拉取。不要用 `git reset --hard` 处理运维修改；先确认并人工保存需要保留的内容。

## 常见错误

### 工作区存在未提交的已跟踪文件

脚本会在停止服务前拒绝部署。使用 `git status` 查明来源；把合法变更提交并推送到 GitLab，或人工移出错误修改。不要用部署脚本覆盖服务器上的临时改动。

### lock mismatch

源码锁文件与基础镜像内摘要不同。先按“基础镜像升级”发布并拉取新 API/Sandbox Runtime，再更新 `.env.source` 镜像引用。不要删除摘要文件或绕过校验。

### Builder 失败

旧 `current` 不会被切换。查看部署终端输出，检查 Git 工作区是否干净、Registry/依赖源是否可达、磁盘空间和缓存目录权限。修复后重跑；不要手工把不完整的 `.tmp` 指向 `current`。

### 缺少 current 或 manifest

首次部署必须成功运行 Release Builder。历史回滚要求目标目录同时包含 `manifest.env`、`web-dist/index.html` 和可执行的 `hycanvas`。若这些文件缺失，应重新检出对应提交并通过正式 Builder 生成，不能拼接不同提交的产物。

### 健康检查失败

查看 API、Gateway、Worker、Web、HyCanvas 及其依赖日志。自动回滚成功时部署命令仍返回非零；确认旧版本健康后再排障。若提示“自动回滚不完整”，停止继续发布，保留现场并按状态文件、Git SHA 和 `current` 指针逐项恢复。

### Compose 解析或镜像检查失败

先运行 `--validate-only`。检查绝对路径、必填密钥、Registry 登录、固定镜像名称，以及 `APP_NETWORK_NAME` 与 `SANDBOX_DOCKER_NETWORK` 是否一致。只有预检通过后才能执行正式发布。
