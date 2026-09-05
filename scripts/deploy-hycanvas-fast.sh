#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CURRENT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
VERSION="${1:-$(git -C "$ROOT" rev-parse --short=8 HEAD)}"
HOST="${DEPLOY_HOST:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
HYCANVAS_DIR="$ROOT/apps/hycanvas"
PACKAGE_MARKER="$(git -C "$ROOT" rev-parse --absolute-git-dir)/hycanvas-fast-packages-sha"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ -n "$HOST" ]] || fail "必须设置 DEPLOY_HOST，例如 DEPLOY_HOST=server-47"
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || fail "版本号格式无效: $VERSION"
[[ "$DEPLOY_DIR" =~ ^/[0-9A-Za-z._/-]+$ ]] || fail "DEPLOY_DIR 必须是安全的绝对路径"
for command_name in docker npm rsync gzip scp ssh shasum; do
  command -v "$command_name" >/dev/null 2>&1 || fail "缺少本地命令: $command_name"
done
[[ -d "$HYCANVAS_DIR/frontend/node_modules" ]] \
  || fail "HyCanvas 前端依赖尚未安装，请先在 apps/hycanvas 执行 npm install"

BUILD_DIR="$(mktemp -d "$HYCANVAS_DIR/.fast-release.XXXXXX")"
cleanup() {
  [[ "$BUILD_DIR" == "$HYCANVAS_DIR"/.fast-release.* ]] || return
  rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

echo ">>> 构建 HyCanvas 前端与内部包"
cd "$HYCANVAS_DIR"
LAST_PACKAGE_SHA="$(cat "$PACKAGE_MARKER" 2>/dev/null || true)"
if [[ -n "$LAST_PACKAGE_SHA" ]] \
  && git -C "$ROOT" cat-file -e "$LAST_PACKAGE_SHA^{commit}" 2>/dev/null \
  && git -C "$ROOT" diff --quiet "$LAST_PACKAGE_SHA..$CURRENT_SHA" -- apps/hycanvas/packages apps/hycanvas/package.json apps/hycanvas/package-lock.json \
  && [[ -z "$(git -C "$ROOT" status --porcelain -- apps/hycanvas/packages apps/hycanvas/package.json apps/hycanvas/package-lock.json)" ]]; then
  echo ">>> 内部包自上次成功发布后未变化，复用已有构建结果"
else
  npm run build:packages
fi
HYCANVAS_AUTH_MODE=contentswarm \
CONTENTSWARM_URL="${CONTENTSWARM_PUBLIC_URL:-https://bydf.openswarm.run/boyun}" \
NEXT_PUBLIC_HYCANVAS_AUTH_MODE=contentswarm \
NEXT_PUBLIC_CONTENTSWARM_URL="${CONTENTSWARM_PUBLIC_URL:-https://bydf.openswarm.run/boyun}" \
HYCANVAS_DEPLOYMENT_ID="$(printf '%s' "$VERSION" | tr -c '[:alnum:]_-' '-')" \
  npm run build:dist -w frontend
rsync -a --delete frontend/out/ backend/internal/webui/public/

echo ">>> 编译 Linux AMD64 单文件"
docker run --rm \
  -v "$HYCANVAS_DIR/backend:/src" \
  -v "$BUILD_DIR:/out" \
  -v hycanvas-go-mod:/go/pkg/mod \
  -v hycanvas-go-build:/root/.cache/go-build \
  -w /src \
  -e GOOS=linux -e GOARCH=amd64 -e CGO_ENABLED=0 \
  golang:1.25-alpine \
  /usr/local/go/bin/go build -tags embed -trimpath \
    -ldflags "-s -w -X main.version=$VERSION" \
    -o /out/hycanvas ./cmd/api
[[ -x "$BUILD_DIR/hycanvas" ]] || fail "HyCanvas 单文件编译失败"
gzip -1 -c "$BUILD_DIR/hycanvas" > "$BUILD_DIR/hycanvas.gz"
SHA256="$(shasum -a 256 "$BUILD_DIR/hycanvas" | awk '{print $1}')"

echo ">>> 上传单文件与快速发布描述文件"
ssh "$HOST" "mkdir -p '$DEPLOY_DIR/scripts' /tmp/hycanvas-fast-release"
scp "$ROOT/docker-compose.hycanvas-fast.yml" "${HOST}:$DEPLOY_DIR/docker-compose.hycanvas-fast.yml"
scp "$ROOT/scripts/deploy-hycanvas-fast-server.sh" "$ROOT/scripts/rollback-hycanvas-fast.sh" "${HOST}:$DEPLOY_DIR/scripts/"
scp "$BUILD_DIR/hycanvas.gz" "${HOST}:/tmp/hycanvas-fast-release/hycanvas-$VERSION.gz"
ssh "$HOST" "chmod +x '$DEPLOY_DIR/scripts/deploy-hycanvas-fast-server.sh' '$DEPLOY_DIR/scripts/rollback-hycanvas-fast.sh' && DEPLOY_DIR='$DEPLOY_DIR' bash '$DEPLOY_DIR/scripts/deploy-hycanvas-fast-server.sh' '$VERSION' '/tmp/hycanvas-fast-release/hycanvas-$VERSION.gz' '$SHA256'"

printf '%s\n' "$CURRENT_SHA" > "$PACKAGE_MARKER"
echo ">>> 发布成功: $VERSION"
