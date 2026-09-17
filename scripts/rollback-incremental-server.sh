#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
RELEASE_ROOT="${RELEASE_ROOT:-$DEPLOY_DIR/.deploy/incremental}"
STATE_FILE="${1:-$RELEASE_ROOT/current-state}"
WEB_HOST_PORT="${WEB_HOST_PORT:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

state_value() {
  sed -n "s/^$1=//p" "$STATE_FILE" | tail -n 1
}

[[ -f "$STATE_FILE" ]] || fail "没有可回滚的增量发布记录: $STATE_FILE"
components="$(state_value components)"
api_ref="$(state_value previous_api_ref)"
web_ref="$(state_value previous_web_ref)"
previous_git_sha="$(state_value previous_git_sha)"
[[ -n "$api_ref" && -n "$web_ref" ]] || fail "回滚记录缺少镜像引用"
[[ "$previous_git_sha" =~ ^[0-9a-f]{40}$ ]] || fail "回滚记录缺少基线 Git SHA"

cd "$DEPLOY_DIR"
WEB_HOST_PORT="${WEB_HOST_PORT:-$(sed -n 's/^WEB_HOST_PORT=//p' "$ENV_FILE" | tail -n 1)}"
WEB_HOST_PORT="${WEB_HOST_PORT:-8090}"
compose_with_refs() {
  YUXI_API_REF="$api_ref" YUXI_WEB_REF="$web_ref" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

if [[ ",$components," == *",api,"* ]]; then
  compose_with_refs stop -t 30 worker
  compose_with_refs up -d --no-deps --force-recreate --no-build --pull never api xhs-browser-gateway
fi
if [[ ",$components," == *",web,"* ]]; then
  compose_with_refs up -d --no-deps --force-recreate --no-build --pull never web
fi
if [[ ",$components," == *",api,"* ]]; then
  compose_with_refs up -d --no-deps --force-recreate --no-build --pull never worker
fi

for _ in $(seq 1 45); do
  if curl -sf --max-time 5 "http://127.0.0.1:${WEB_HOST_PORT}/api/system/health" >/dev/null \
    && { [[ ",$components," != *",api,"* ]] || docker exec xhs-browser-gateway curl -sf --max-time 5 http://127.0.0.1:5051/health >/dev/null; } \
    && { [[ ",$components," != *",api,"* ]] || [[ "$(docker inspect worker-prod --format '{{.State.Running}}')" == "true" ]]; }; then
    printf '%s\n' "$previous_git_sha" > "$RELEASE_ROOT/current-git-sha"
    rm -f "$RELEASE_ROOT/current-state"
    echo ">>> 已回滚最近一次 API/Web 增量发布"
    exit 0
  fi
  sleep 2
done
fail "回滚后的服务健康检查失败"
