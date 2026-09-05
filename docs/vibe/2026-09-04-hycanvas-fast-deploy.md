# HyCanvas 单文件快速发布与自动回滚

## 目标

在不拉取整套生产镜像、不重启 ContentSwarm 其他服务的情况下，将包含 HyCanvas 前端和后端的 Linux AMD64 单文件发布到生产服务器，并在健康检查失败时自动回滚。

## 范围与验收标准

- 本地构建 `build:dist` 前端并嵌入 Go 程序。
- 发布物只有一个压缩后的 `hycanvas` 可执行文件，附带 SHA256 校验值。
- 服务器保留版本目录，通过软链接原子切换当前版本。
- 只重建 `hycanvas-app`，不影响 API、Web、Worker 和数据库。
- 新容器在 60 秒内健康检查失败时，自动恢复上一版本并再次启动。
- 内部包未变化时复用上次成功构建结果，减少重复 TypeScript 编译。
- 保留完整镜像发布链路，用于首次部署、基础镜像升级和整套系统发布。

## 使用方式

```bash
DEPLOY_HOST=server-47 bash scripts/deploy-hycanvas-fast.sh
```

可以显式指定版本：

```bash
DEPLOY_HOST=server-47 bash scripts/deploy-hycanvas-fast.sh 0.7.4-contentflow-abcdef12
```

服务器人工回滚：

```bash
cd /www/wwwroot/yuxi && bash scripts/rollback-hycanvas-fast.sh
```

## Checklist

- [x] 前端生产构建与 Go AMD64 静态编译
- [x] SHA256 完整性校验
- [x] 原子版本切换
- [x] HyCanvas 单服务重建
- [x] 健康检查失败自动回滚
- [x] 人工回滚入口
