#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
GIT_SHA="${2:-}"
ARCHIVE="${3:-}"
EXPECTED_SHA256="${4:-}"
COMPONENTS="${5:-}"
BASE_GIT_SHA="${6:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
RELEASE_ROOT="${RELEASE_ROOT:-$DEPLOY_DIR/.deploy/incremental}"
WEB_HOST_PORT="${WEB_HOST_PORT:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

contains_component() {
  [[ ",$COMPONENTS," == *",$1,"* ]]
}

image_ref() {
  docker inspect "$1" --format '{{.Config.Image}}'
}

[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || fail "版本号格式无效: $VERSION"
[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "Git SHA 格式无效"
[[ "$BASE_GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "基线 Git SHA 格式无效"
[[ "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "SHA256 格式无效"
[[ "$COMPONENTS" =~ ^(api|web)(,(api|web))*$ ]] || fail "服务器增量组件只允许 api、web"
[[ -f "$ARCHIVE" ]] || fail "发布包不存在: $ARCHIVE"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$EXPECTED_SHA256" ]] || fail "发布包 SHA256 校验失败"

cd "$DEPLOY_DIR"
[[ -f "$ENV_FILE" ]] || fail "缺少生产环境文件: $ENV_FILE"
[[ -f "$COMPOSE_FILE" ]] || fail "缺少生产 Compose 文件: $COMPOSE_FILE"
WEB_HOST_PORT="${WEB_HOST_PORT:-$(sed -n 's/^WEB_HOST_PORT=//p' "$ENV_FILE" | tail -n 1)}"
WEB_HOST_PORT="${WEB_HOST_PORT:-8090}"
command -v docker >/dev/null 2>&1 || fail "服务器尚未安装 Docker"
docker compose version >/dev/null 2>&1 || fail "服务器尚未安装 Docker Compose 插件"

mkdir -p "$RELEASE_ROOT/releases"
target_dir="$RELEASE_ROOT/releases/$VERSION"
rm -rf "$target_dir.tmp"
mkdir -p "$target_dir.tmp"
tar xzf "$ARCHIVE" -C "$target_dir.tmp"
rm -rf "$target_dir"
mv "$target_dir.tmp" "$target_dir"

old_api="$(image_ref api-prod)"
old_web="$(image_ref web-prod)"
new_api="$old_api"
new_web="$old_web"
base_api="$old_api"
base_web="$old_web"
if [[ -f "$RELEASE_ROOT/current-state" ]]; then
  recorded_base_api="$(sed -n 's/^base_api_ref=//p' "$RELEASE_ROOT/current-state" | tail -n 1)"
  recorded_base_web="$(sed -n 's/^base_web_ref=//p' "$RELEASE_ROOT/current-state" | tail -n 1)"
  [[ -z "$recorded_base_api" ]] || base_api="$recorded_base_api"
  [[ -z "$recorded_base_web" ]] || base_web="$recorded_base_web"
fi
docker image inspect "$base_api" >/dev/null 2>&1 || fail "API 增量基础镜像不存在: $base_api"
docker image inspect "$base_web" >/dev/null 2>&1 || fail "Web 增量基础镜像不存在: $base_web"

if contains_component api; then
  new_api="contentswarm-api-incremental:$VERSION"
  docker image inspect "$new_api" >/dev/null 2>&1 && fail "API 增量版本已存在: $new_api"
  docker build --pull=false --build-arg "BASE_IMAGE=$base_api" \
    -f "$target_dir/docker/incremental/api.Dockerfile" -t "$new_api" "$target_dir"
fi
if contains_component web; then
  new_web="contentswarm-web-incremental:$VERSION"
  docker image inspect "$new_web" >/dev/null 2>&1 && fail "Web 增量版本已存在: $new_web"
  docker build --pull=false --build-arg "BASE_IMAGE=$base_web" \
    -f "$target_dir/docker/incremental/web.Dockerfile" -t "$new_web" "$target_dir"
fi

compose_with_refs() {
  YUXI_API_REF="$1" YUXI_WEB_REF="$2" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${@:3}"
}

apply_refs() {
  local api_ref="$1"
  local web_ref="$2"
  if contains_component api; then
    compose_with_refs "$api_ref" "$web_ref" stop -t 30 worker
    compose_with_refs "$api_ref" "$web_ref" up -d --no-deps --force-recreate --no-build --pull never api xhs-browser-gateway
  fi
  if contains_component web; then
    compose_with_refs "$api_ref" "$web_ref" up -d --no-deps --force-recreate --no-build --pull never web
  fi
  if contains_component api; then
    compose_with_refs "$api_ref" "$web_ref" up -d --no-deps --force-recreate --no-build --pull never worker
  fi
}

wait_for_health() {
  local attempt
  for attempt in $(seq 1 45); do
    if curl -sf --max-time 5 "http://127.0.0.1:${WEB_HOST_PORT}/api/system/health" >/dev/null \
      && { ! contains_component api || docker exec xhs-browser-gateway curl -sf --max-time 5 http://127.0.0.1:5051/health >/dev/null; } \
      && { ! contains_component api || [[ "$(docker inspect worker-prod --format '{{.State.Running}}')" == "true" ]]; }; then
      return 0
    fi
    sleep 2
  done
  return 1
}

rollback() {
  trap - ERR
  echo ">>> 增量版本健康检查失败，自动恢复旧镜像" >&2
  apply_refs "$old_api" "$old_web" || true
  wait_for_health || true
}
trap rollback ERR
apply_refs "$new_api" "$new_web"
wait_for_health
trap - ERR

state_file="$RELEASE_ROOT/releases/$VERSION.state"
{
  printf 'version=%s\n' "$VERSION"
  printf 'git_sha=%s\n' "$GIT_SHA"
  printf 'previous_git_sha=%s\n' "$BASE_GIT_SHA"
  printf 'components=%s\n' "$COMPONENTS"
  printf 'previous_api_ref=%s\n' "$old_api"
  printf 'previous_web_ref=%s\n' "$old_web"
  printf 'current_api_ref=%s\n' "$new_api"
  printf 'current_web_ref=%s\n' "$new_web"
  printf 'base_api_ref=%s\n' "$base_api"
  printf 'base_web_ref=%s\n' "$base_web"
} > "$state_file"
ln -sfn "releases/$VERSION.state" "$RELEASE_ROOT/current-state.next"
mv -Tf "$RELEASE_ROOT/current-state.next" "$RELEASE_ROOT/current-state"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps api worker xhs-browser-gateway web
echo ">>> API/Web 增量发布完成: $VERSION"
