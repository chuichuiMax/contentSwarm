# 生产部署指南

本文档介绍如何在生产环境中部署 Yuxi。

## 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

::: warning 注意事项
1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突
2. 生产服务器只拉取 CI 已验证的版本化镜像，不在服务器构建源码
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭
:::

## 部署步骤

### 1. 准备配置文件

为避免与开发环境冲突，生产环境建议使用 `.env.prod` 文件：

```bash
cp .env.template .env.prod
```

编辑 `.env.prod`，设置强密码和必要的 API 密钥：

- `NEO4J_PASSWORD`：修改默认密码
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：修改默认密钥
- `POSTGRES_PASSWORD`：至少 16 个字符，禁止使用 `postgres`
- `JWT_SECRET_KEY`：至少 32 个字符的持久化随机密钥
- `YUXI_INSTANCE_ID`：至少 12 个字符且部署后保持不变，避免令牌签发方在重启后变化
- `SILICONFLOW_API_KEY` 等模型密钥
- `IMAGE2_BASE_URL` / `IMAGE2_API_KEY` / `IMAGE2_MODEL`：封面生成使用的 image2 中转站地址、密钥和模型名；文生图、图片编辑或异步状态接口不是默认路径时，分别设置 `IMAGE2_SUBMIT_PATH`、`IMAGE2_EDIT_PATH` 与 `IMAGE2_STATUS_PATH`。用户也可在封面生成页右上角保存账号级全局 Base URL、API Key 与模型；账号配置优先，环境变量作为未配置时的回退，API Key 不通过读取接口回显。模板复刻 V2 默认启用；紧急回滚时可将 `CONTENT_COVER_TEMPLATE_REPLICATION_V2=false`，使 Worker 暂时使用旧版最终合成器。
- `XHS_GATEWAY_TOKEN`：至少 32 个字符的随机内部浏览器网关密钥，不能使用开发默认值
- `SANDBOX_IMAGE`：使用固定版本或 digest，禁止使用 `latest`
- `YUXI_API_IMAGE`、`YUXI_WEB_IMAGE`、`YUXI_SANDBOX_PROVISIONER_IMAGE`、`HYCANVAS_IMAGE`：镜像仓库地址
- `PUBLIC_BASE_URL`：运营人员实际访问的 HTTPS 地址，例如 `https://content.example.com/boyun`；生产部署拒绝纯 HTTP，证书必须可被标准客户端验证

部署脚本会在拉取镜像之前校验上述生产凭据和 HTTPS 地址，缺失、仍为公开默认值或长度不足时立即终止，且不会把密钥内容输出到日志。部署完成后除本机 API 与内部浏览器网关外，还会通过 `PUBLIC_BASE_URL` 验证公网 TLS 入口；三者任一失败都会触发既定回滚流程。

首次配置 image2 中转站后，应先在测试环境执行真实四模式及 Worker/MinIO 闭环验收。`IMAGE2_MODEL` 必须是中转站模型列表中的图片模型 ID，不能误填 `high` 等质量参数。该命令会消耗中转站额度，只有显式设置开关才会运行；容器内结果图与不含密钥的任务清单保存到 `/app/saves/image2-live-smoke`，开发 Compose 对应宿主机目录为 `docker/volumes/yuxi/image2-live-smoke`：

```bash
docker compose exec -e RUN_IMAGE2_LIVE_TESTS=1 api \
  pytest test/e2e/test_image2_live.py -m e2e
```

宝塔 Nginx 使用项目内的 `scripts/nginx/yuxi-boyun.conf` 扩展时，应确保该扩展同时包含在 HTTP 与 HTTPS 虚拟主机中。扩展会将 HTTP 请求以 308 跳转到 HTTPS，并设置 HSTS、`nosniff`、同源嵌入与 Referrer Policy；应用发布前应先执行 `nginx -t`，确认通过后再单独 reload Nginx。部署脚本不会擅自修改或重载宿主机 Nginx。

### 2. 发布不可变镜像

在 GitHub Actions 手动运行 `Publish versioned images`，输入发布版本号。流程会先拒绝仓库中已经存在的同名版本，再运行后端测试和前端构建，分别构建 API、Web、Sandbox Provisioner 与 HyCanvas 候选镜像，并对 API 候选镜像执行 Patchright Mock 浏览器 E2E。四类候选镜像全部成功后才统一提升为版本号与 `sha-<Git SHA>` 正式标签，避免后续构建失败留下部分发布；流程不生成 `latest`。完成后下载 `image-digests-<版本号>` 构建产物并归档，其中记录的是正式标签从仓库解析出的最终 digest，作为部署与审计依据。

### 3. 启动服务

推荐使用部署脚本。脚本会检查根目录、部署目录和 Docker 数据目录的磁盘/inode，以及内存、负载、近期内核异常和并行构建/备份/迁移任务，记录上一版本，然后只拉取镜像并以 `--no-build` 启动：

```bash
cd /www/wwwroot/yuxi
RELEASE_MANIFEST=/secure/release/image-digests-0.7.2-contentflow.txt \
  bash scripts/deploy-prod-server.sh 0.7.2-contentflow
```

也可以从受控运维机仅上传部署描述文件并触发同一流程；该脚本不会上传源码或 `.env`：

```bash
DEPLOY_HOST=user@server \
  bash scripts/push-and-deploy.sh 0.7.2-contentflow ./image-digests-0.7.2-contentflow.txt
```

部署脚本会校验清单版本、Git SHA、仓库名称与四个 SHA-256 digest，并将 Compose 镜像引用固定为 `仓库@sha256:...`。成功部署的清单保存在 `.deploy/releases/`，后续回滚继续使用原 digest，不依赖可能漂移的标签。仅对尚无 digest 清单的历史版本，允许运维显式设置 `ALLOW_LEGACY_TAG_ROLLBACK=true` 做一次兼容回滚；该开关禁止用于新版本部署。`push-and-deploy.sh` 会在上传任何文件前，通过 SSH 流式执行一次只读资源预检；描述文件上传后，正式部署脚本还会再次执行同一门禁，避免预检与启动之间的资源状态变化被忽略。

正式部署前可以分两步只读验证：`--validate-only` 仅校验生产配置、凭据、Compose 与 digest 清单；`--preflight-only` 在此基础上继续执行磁盘、inode、内存、负载、Docker 占用、并行任务和近期内核异常检查。两种模式都不会拉取镜像、创建目录、重启或修改服务：

```bash
RELEASE_MANIFEST=/secure/release/image-digests-0.7.2-contentflow.txt \
  bash scripts/deploy-prod-server.sh 0.7.2-contentflow --validate-only
RELEASE_MANIFEST=/secure/release/image-digests-0.7.2-contentflow.txt \
  bash scripts/deploy-prod-server.sh 0.7.2-contentflow --preflight-only
```

如需 `all` Profile，必须提前将 MinerU/PaddleX 也发布为同一固定版本并配置 `MINERU_IMAGE`、`PADDLEX_IMAGE`；服务器仍禁止现场构建。

### 4. 验证部署

- Web 访问：`http://localhost:${WEB_HOST_PORT:-8090}`
- API 健康检查：`curl http://localhost:${WEB_HOST_PORT:-8090}/api/system/health`
- 浏览器网关健康检查：`docker exec xhs-browser-gateway curl -f http://127.0.0.1:5051/health`
- 容器状态：`docker compose --env-file .env.prod -f docker-compose.prod.yml ps`

## 维护与更新

### 发布新版本

```bash
bash scripts/deploy-prod-server.sh <新版本号>
```

新版本健康检查失败时，脚本会回滚到 `.deploy/current-version` 记录的上一版本，并输出精确回滚命令。不要删除当前版本和至少一个已验证可回滚版本的镜像。

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# 小红书浏览器网关日志
docker logs -f xhs-browser-gateway

# Nginx 访问日志
docker logs -f web-prod
```
