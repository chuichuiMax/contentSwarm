#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
OVERRIDE_FILE="${OVERRIDE_FILE:-docker-compose.hycanvas-fast.yml}"
RELEASE_ROOT="${RELEASE_ROOT:-$DEPLOY_DIR/docker/volumes/hycanvas-release}"

cd "$DEPLOY_DIR"
PREVIOUS_VERSION="$(tr -d '[:space:]' < "$RELEASE_ROOT/previous-version")"
[[ -x "$RELEASE_ROOT/releases/$PREVIOUS_VERSION/hycanvas" ]] \
  || { echo "ERROR: 找不到可回滚版本: $PREVIOUS_VERSION" >&2; exit 1; }
CURRENT_CONTAINER="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q hycanvas-app)"
CURRENT_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$CURRENT_CONTAINER")"
ln -sfn "releases/$PREVIOUS_VERSION" "$RELEASE_ROOT/current.next"
mv -Tf "$RELEASE_ROOT/current.next" "$RELEASE_ROOT/current"
HYCANVAS_REF="$CURRENT_IMAGE" docker compose --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE" \
  up -d --no-deps --force-recreate --no-build --pull never hycanvas-app
for _ in $(seq 1 30); do
  CURRENT_CONTAINER="$(HYCANVAS_REF="$CURRENT_IMAGE" docker compose --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE" ps -q hycanvas-app)"
  if docker exec "$CURRENT_CONTAINER" curl -fsS --max-time 3 http://127.0.0.1:8005/healthz >/dev/null 2>&1; then
    printf '%s\n' "$PREVIOUS_VERSION" > "$RELEASE_ROOT/current-version"
    echo ">>> HyCanvas 已回滚到: $PREVIOUS_VERSION"
    exit 0
  fi
  sleep 2
done
echo "ERROR: HyCanvas 回滚后健康检查失败" >&2
exit 1
