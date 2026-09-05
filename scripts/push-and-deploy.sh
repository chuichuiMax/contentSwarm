#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
MANIFEST="${2:-${RELEASE_MANIFEST:-}}"
HOST="${DEPLOY_HOST:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE="${TMPDIR:-/tmp}/yuxi-deployment-descriptors.tar.gz"

[[ -n "$VERSION" ]] || { echo "用法: DEPLOY_HOST=user@server $0 <不可变版本号> <image-digests.txt>" >&2; exit 1; }
[[ -n "$HOST" ]] || { echo "必须显式设置 DEPLOY_HOST" >&2; exit 1; }
[[ "$VERSION" != "latest" ]] || { echo "生产部署禁止使用 latest" >&2; exit 1; }
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || { echo "版本号格式无效: $VERSION" >&2; exit 1; }
[[ "$DEPLOY_DIR" =~ ^/[0-9A-Za-z._/-]+$ ]] || { echo "DEPLOY_DIR 必须是安全的绝对路径" >&2; exit 1; }
[[ -n "$MANIFEST" && -f "$MANIFEST" ]] || { echo "必须提供 CI 生成的 image-digests.txt" >&2; exit 1; }
grep -qx "release_version=$VERSION" "$MANIFEST" || { echo "digest 清单版本与部署版本不一致" >&2; exit 1; }

cd "$ROOT"
tar czf "$ARCHIVE" \
  docker-compose.prod.yml \
  docker/sandbox_provisioner/sandbox.env \
  scripts/deploy-prod-server.sh \
  scripts/server-resource-preflight.sh

echo ">>> 上传任何文件前执行远程只读资源预检"
ssh "$HOST" "DEPLOY_DIR='$DEPLOY_DIR' bash -s" < scripts/server-resource-preflight.sh
echo ">>> 仅上传部署描述文件，不上传源码或 .env"
ssh "$HOST" "mkdir -p '$DEPLOY_DIR/scripts'"
scp "$ARCHIVE" "${HOST}:/tmp/yuxi-deployment-descriptors.tar.gz"
scp "$MANIFEST" "${HOST}:/tmp/yuxi-image-digests-$VERSION.txt"
ssh "$HOST" "tar xzf /tmp/yuxi-deployment-descriptors.tar.gz -C '$DEPLOY_DIR' && chmod +x '$DEPLOY_DIR/scripts/deploy-prod-server.sh' && cd '$DEPLOY_DIR' && RELEASE_MANIFEST='/tmp/yuxi-image-digests-$VERSION.txt' bash scripts/deploy-prod-server.sh '$VERSION'"
