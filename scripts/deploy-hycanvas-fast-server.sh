#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
ARCHIVE="${2:-}"
EXPECTED_SHA256="${3:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
OVERRIDE_FILE="${OVERRIDE_FILE:-docker-compose.hycanvas-fast.yml}"
RELEASE_ROOT="${RELEASE_ROOT:-$DEPLOY_DIR/docker/volumes/hycanvas-release}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-30}"
HEALTH_INTERVAL_SECONDS="${HEALTH_INTERVAL_SECONDS:-2}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ -n "$VERSION" && -n "$ARCHIVE" && -n "$EXPECTED_SHA256" ]] \
  || fail "用法: $0 <版本号> <hycanvas.gz> <sha256>"
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || fail "版本号格式无效: $VERSION"
[[ "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "SHA256 格式无效"
[[ "$DEPLOY_DIR" =~ ^/[0-9A-Za-z._/-]+$ ]] || fail "DEPLOY_DIR 必须是安全的绝对路径"
[[ "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "HEALTH_ATTEMPTS 必须是正整数"
[[ "$HEALTH_INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || fail "HEALTH_INTERVAL_SECONDS 必须是非负整数"
[[ -f "$ARCHIVE" ]] || fail "发布包不存在: $ARCHIVE"
command -v docker >/dev/null 2>&1 || fail "服务器尚未安装 Docker"
command -v gzip >/dev/null 2>&1 || fail "服务器尚未安装 gzip"
command -v sha256sum >/dev/null 2>&1 || fail "服务器尚未安装 sha256sum"

cd "$DEPLOY_DIR"
[[ -f "$ENV_FILE" ]] || fail "缺少生产环境文件: $ENV_FILE"
[[ -f "$COMPOSE_FILE" ]] || fail "缺少生产 Compose 文件: $COMPOSE_FILE"
[[ -f "$OVERRIDE_FILE" ]] || fail "缺少快速发布 Compose 文件: $OVERRIDE_FILE"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE" config --quiet

compose() {
  HYCANVAS_REF="$CURRENT_IMAGE" docker compose --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE" "$@"
}

wait_for_health() {
  local container_id
  for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
    container_id="$(compose ps -q hycanvas-app 2>/dev/null || true)"
    if [[ -n "$container_id" ]] \
      && docker exec "$container_id" curl -fsS --max-time 3 http://127.0.0.1:8005/healthz >/dev/null 2>&1; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
  done
  return 1
}

mkdir -p "$RELEASE_ROOT/releases"
CURRENT_CONTAINER="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q hycanvas-app)"
[[ -n "$CURRENT_CONTAINER" ]] || fail "hycanvas-app 当前未运行，快速发布不能作为首次部署方式"
CURRENT_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$CURRENT_CONTAINER")"
[[ -n "$CURRENT_IMAGE" ]] || fail "无法读取当前 HyCanvas 基础镜像"

CURRENT_ENTRYPOINT="$(docker inspect --format '{{index .Config.Entrypoint 0}}' "$CURRENT_CONTAINER" 2>/dev/null || true)"
PREVIOUS_VERSION=""
if [[ "$CURRENT_ENTRYPOINT" == "/app/release/hycanvas" && -L "$RELEASE_ROOT/current" ]]; then
  PREVIOUS_VERSION="$(basename "$(readlink "$RELEASE_ROOT/current")")"
else
  PREVIOUS_VERSION="baseline-$(date -u +%Y%m%d%H%M%S)"
  mkdir -p "$RELEASE_ROOT/releases/$PREVIOUS_VERSION"
  docker cp "$CURRENT_CONTAINER:/app/hycanvas" "$RELEASE_ROOT/releases/$PREVIOUS_VERSION/hycanvas"
  chmod 0755 "$RELEASE_ROOT/releases/$PREVIOUS_VERSION/hycanvas"
fi

TARGET_DIR="$RELEASE_ROOT/releases/$VERSION"
mkdir -p "$TARGET_DIR"
gzip -dc "$ARCHIVE" > "$TARGET_DIR/hycanvas.tmp"
ACTUAL_SHA256="$(sha256sum "$TARGET_DIR/hycanvas.tmp" | awk '{print $1}')"
[[ "$ACTUAL_SHA256" == "$EXPECTED_SHA256" ]] || fail "发布程序 SHA256 校验失败"
chmod 0755 "$TARGET_DIR/hycanvas.tmp"
mv -f "$TARGET_DIR/hycanvas.tmp" "$TARGET_DIR/hycanvas"
printf '%s\n' "$EXPECTED_SHA256" > "$TARGET_DIR/hycanvas.sha256"

switch_release() {
  local version="$1"
  ln -sfn "releases/$version" "$RELEASE_ROOT/current.next"
  mv -Tf "$RELEASE_ROOT/current.next" "$RELEASE_ROOT/current"
}

switch_release "$VERSION"
echo ">>> 切换 HyCanvas 单文件版本: $PREVIOUS_VERSION -> $VERSION"
if ! compose up -d --no-deps --force-recreate --no-build --pull never hycanvas-app \
  || ! wait_for_health; then
  echo ">>> 新版本健康检查失败，自动回滚到: $PREVIOUS_VERSION" >&2
  switch_release "$PREVIOUS_VERSION"
  compose up -d --no-deps --force-recreate --no-build --pull never hycanvas-app
  if wait_for_health; then
    echo ">>> 已自动回滚到: $PREVIOUS_VERSION" >&2
  else
    echo ">>> 回滚后健康检查仍失败，需要人工介入" >&2
    compose ps >&2 || true
    docker logs "$(compose ps -q hycanvas-app)" --tail 100 >&2 || true
  fi
  exit 1
fi

printf '%s\n' "$PREVIOUS_VERSION" > "$RELEASE_ROOT/previous-version"
printf '%s\n' "$VERSION" > "$RELEASE_ROOT/current-version"
compose ps hycanvas-app
echo ">>> HyCanvas 快速发布完成: $VERSION"
echo "回滚命令: cd '$DEPLOY_DIR' && bash scripts/rollback-hycanvas-fast.sh"
