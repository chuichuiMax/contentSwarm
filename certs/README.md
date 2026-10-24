# 生产 HTTPS 证书（不入库）

`ai.hi-run.net` / `hycanvas.hi-run.net` 宿主机 Nginx 使用：

| 文件 | 路径 |
|------|------|
| 证书 | `/opt/contentSwarm/certs/hi-run.net.pem` |
| 私钥 | `/opt/contentSwarm/certs/hi-run.net.key` |

**勿将 `.pem` / `.key` 提交 Git。** 本地保管原件（如 `外网证书/nginx/`）。

## 首次或更换证书

在仓库根目录（Git Bash / WSL）：

```bash
export SSL_CERT_PEM="/path/to/hi-run.net.pem"
export SSL_CERT_KEY="/path/to/hi-run.net.key"
bash scripts/deploy-hi-run-ssl-certs.sh
```

默认目标主机 `172.16.103.17`，目录 `/opt/contentSwarm`。

## 持久化与自愈

- 部署脚本会写入主目录，并同步到 **`/var/lib/contentswarm/ssl-backup/`**（与代码目录分离，避免误删仓库时丢证书）。
- 服务器 `nginx` 启动前执行 `scripts/ensure-hi-run-ssl-certs.sh`：主目录缺失时从备份恢复。

手动在服务器上恢复：

```bash
/opt/contentSwarm/scripts/ensure-hi-run-ssl-certs.sh
nginx -t && systemctl reload nginx
```
