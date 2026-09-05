#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
MODE="${2:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ROLLBACK_DIR="${ROLLBACK_DIR:-.deploy}"
RELEASE_MANIFEST="${RELEASE_MANIFEST:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ -n "$VERSION" ]] || fail "用法: $0 <不可变版本号>"
[[ "$VERSION" != "latest" ]] || fail "生产部署禁止使用 latest"
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || fail "版本号格式无效: $VERSION"
[[ -z "$MODE" || "$MODE" == "--validate-only" || "$MODE" == "--preflight-only" ]] || fail "未知执行模式: $MODE"
command -v docker >/dev/null 2>&1 || fail "服务器尚未安装 Docker"
docker compose version >/dev/null 2>&1 || fail "服务器尚未安装 Docker Compose 插件"

cd "$DEPLOY_DIR"
[[ -f "$ENV_FILE" ]] || fail "缺少生产环境文件: $DEPLOY_DIR/$ENV_FILE"
[[ -f "$COMPOSE_FILE" ]] || fail "缺少生产 Compose 文件: $DEPLOY_DIR/$COMPOSE_FILE"
if [[ -z "$RELEASE_MANIFEST" || ! -f "$RELEASE_MANIFEST" ]]; then
  [[ "${ALLOW_LEGACY_TAG_ROLLBACK:-false}" == "true" ]] || fail "必须提供 CI 生成的 RELEASE_MANIFEST"
fi

env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

require_secret() {
  local name="$1"
  local forbidden_value="$2"
  local min_length="$3"
  local value
  value="$(env_value "$name")"
  [[ -n "$value" ]] || fail "$name 必须在 $ENV_FILE 中显式配置"
  [[ "$value" != "$forbidden_value" ]] || fail "$name 不能使用公开默认值"
  (( ${#value} >= min_length )) || fail "$name 长度不能少于 $min_length 个字符"
}

manifest_value() {
  local manifest="$1"
  local name="$2"
  sed -n "s/^$name=//p" "$manifest" | tail -n 1
}

load_release_manifest() {
  local manifest="$1"
  local expected_version="$2"
  local manifest_version git_sha api_ref web_ref sandbox_ref hycanvas_ref
  local api_image web_image sandbox_image hycanvas_image

  manifest_version="$(manifest_value "$manifest" release_version)"
  git_sha="$(manifest_value "$manifest" git_sha)"
  api_ref="$(manifest_value "$manifest" api)"
  web_ref="$(manifest_value "$manifest" web)"
  sandbox_ref="$(manifest_value "$manifest" sandbox_provisioner)"
  hycanvas_ref="$(manifest_value "$manifest" hycanvas)"
  [[ "$manifest_version" == "$expected_version" ]] || fail "digest 清单版本与部署版本不一致"
  [[ "$git_sha" =~ ^[0-9a-f]{40}$ ]] || fail "digest 清单中的 Git SHA 无效"

  api_image="$(env_value YUXI_API_IMAGE)"
  web_image="$(env_value YUXI_WEB_IMAGE)"
  sandbox_image="$(env_value YUXI_SANDBOX_PROVISIONER_IMAGE)"
  hycanvas_image="$(env_value HYCANVAS_IMAGE)"
  api_image="${api_image:-ghcr.io/shenwei8899-ctrl/contentswarm-yuxi-api}"
  web_image="${web_image:-ghcr.io/shenwei8899-ctrl/contentswarm-yuxi-web}"
  sandbox_image="${sandbox_image:-ghcr.io/shenwei8899-ctrl/contentswarm-yuxi-sandbox-provisioner}"
  hycanvas_image="${hycanvas_image:-ghcr.io/shenwei8899-ctrl/contentswarm-hycanvas}"

  validate_digest_ref "$api_ref" "$api_image" "API"
  validate_digest_ref "$web_ref" "$web_image" "Web"
  validate_digest_ref "$sandbox_ref" "$sandbox_image" "Sandbox Provisioner"
  validate_digest_ref "$hycanvas_ref" "$hycanvas_image" "HyCanvas"
  export YUXI_API_REF="$api_ref"
  export YUXI_WEB_REF="$web_ref"
  export YUXI_SANDBOX_PROVISIONER_REF="$sandbox_ref"
  export HYCANVAS_REF="$hycanvas_ref"
}

validate_digest_ref() {
  local reference="$1"
  local expected_image="$2"
  local label="$3"
  local prefix="${expected_image}@sha256:"
  [[ "$reference" == "$prefix"* ]] || fail "$label 镜像引用与配置的仓库不一致"
  [[ "${reference#${expected_image}@}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "$label 镜像 digest 无效"
}

XHS_TOKEN="$(env_value XHS_GATEWAY_TOKEN)"
SANDBOX_RUNTIME_IMAGE="$(env_value SANDBOX_IMAGE)"
PUBLIC_BASE_URL="$(env_value PUBLIC_BASE_URL)"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
WEB_HOST_PORT="${WEB_HOST_PORT:-$(env_value WEB_HOST_PORT)}"
WEB_HOST_PORT="${WEB_HOST_PORT:-8090}"
require_secret JWT_SECRET_KEY "yuxi_know_secure_key" 32
require_secret YUXI_INSTANCE_ID "" 12
require_secret POSTGRES_PASSWORD "postgres" 16
require_secret NEO4J_PASSWORD "0123456789" 16
require_secret MINIO_ACCESS_KEY "minioadmin" 8
require_secret MINIO_SECRET_KEY "minioadmin" 16
[[ -n "$XHS_TOKEN" && "$XHS_TOKEN" != "local-dev-change-me" && ${#XHS_TOKEN} -ge 32 ]] || fail "XHS_GATEWAY_TOKEN 必须配置为至少 32 个字符的生产随机密钥"
[[ -n "$SANDBOX_RUNTIME_IMAGE" ]] || fail "SANDBOX_IMAGE 必须配置为固定版本或 digest"
[[ "$SANDBOX_RUNTIME_IMAGE" == *@sha256:* || "$SANDBOX_RUNTIME_IMAGE" =~ :[^/:]+$ ]] || fail "SANDBOX_IMAGE 必须显式配置版本标签或 digest"
[[ "$SANDBOX_RUNTIME_IMAGE" != *":latest" ]] || fail "SANDBOX_IMAGE 禁止使用 latest"
[[ "$PUBLIC_BASE_URL" =~ ^https://[^[:space:]]+$ ]] || fail "PUBLIC_BASE_URL 必须配置为生产 HTTPS 地址"
if [[ -n "$RELEASE_MANIFEST" && -f "$RELEASE_MANIFEST" ]]; then
load_release_manifest "$RELEASE_MANIFEST" "$VERSION"
else
  unset YUXI_API_REF YUXI_WEB_REF YUXI_SANDBOX_PROVISIONER_REF HYCANVAS_REF
  echo ">>> 警告：仅为历史版本兼容回滚使用版本标签，禁止用于新版本部署"
fi

export YUXI_VERSION="$VERSION"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
if [[ "$MODE" == "--validate-only" ]]; then
  echo ">>> 配置、凭据与 digest 清单校验通过；未执行资源检查、拉取或启动"
  exit 0
fi

[[ -f scripts/server-resource-preflight.sh ]] || fail "缺少服务器资源预检脚本"
DEPLOY_DIR="$DEPLOY_DIR" ENV_FILE="$ENV_FILE" COMPOSE_FILE="$COMPOSE_FILE" \
  bash scripts/server-resource-preflight.sh

if [[ "$MODE" == "--preflight-only" ]]; then
  echo ">>> 配置、凭据、digest 清单与服务器资源预检通过；未拉取或启动服务"
  exit 0
fi

mkdir -p "$ROLLBACK_DIR"
RELEASE_DIR="$ROLLBACK_DIR/releases"
mkdir -p "$RELEASE_DIR"
CURRENT_VERSION_FILE="$ROLLBACK_DIR/current-version"
PREVIOUS_VERSION=""
if [[ -f "$CURRENT_VERSION_FILE" ]]; then
  PREVIOUS_VERSION="$(tr -d '[:space:]' < "$CURRENT_VERSION_FILE")"
fi
printf '%s\n' "$PREVIOUS_VERSION" > "$ROLLBACK_DIR/previous-version"

echo ">>> 拉取版本化镜像: $VERSION"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull api worker xhs-browser-gateway web sandbox-provisioner hycanvas-app
docker pull "$SANDBOX_RUNTIME_IMAGE"

echo ">>> 启动版本化服务（服务器不构建镜像）"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans --no-build

wait_for_health() {
  for _ in $(seq 1 60); do
    if curl -sf --max-time 10 "http://127.0.0.1:${WEB_HOST_PORT}/api/system/health" >/dev/null \
      && docker exec xhs-browser-gateway curl -sf --max-time 10 http://127.0.0.1:5051/health >/dev/null \
      && curl -sf --max-time 15 "${PUBLIC_BASE_URL}/api/system/health" >/dev/null; then
      return 0
    fi
    sleep 5
  done
  return 1
}

if ! wait_for_health; then
  echo ">>> 新版本健康检查失败"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
  docker logs api-prod --tail 80 || true
  docker logs xhs-browser-gateway --tail 80 || true
  if [[ -n "$PREVIOUS_VERSION" && "$PREVIOUS_VERSION" != "$VERSION" ]]; then
    echo ">>> 自动回滚到已记录版本: $PREVIOUS_VERSION"
    export YUXI_VERSION="$PREVIOUS_VERSION"
    PREVIOUS_MANIFEST="$RELEASE_DIR/$PREVIOUS_VERSION.manifest"
    if [[ -f "$PREVIOUS_MANIFEST" ]]; then
      load_release_manifest "$PREVIOUS_MANIFEST" "$PREVIOUS_VERSION"
    elif [[ "${ALLOW_LEGACY_TAG_ROLLBACK:-false}" == "true" ]]; then
      unset YUXI_API_REF YUXI_WEB_REF YUXI_SANDBOX_PROVISIONER_REF HYCANVAS_REF
      echo ">>> 上一版本没有 digest 清单，按历史不可变版本标签执行兼容回滚"
    else
      echo ">>> 上一版本没有 digest 清单，已拒绝对可漂移标签执行自动回滚" >&2
      echo ">>> 如确认历史标签未被覆盖，可设置 ALLOW_LEGACY_TAG_ROLLBACK=true 后人工回滚" >&2
      exit 1
    fi
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull api worker xhs-browser-gateway web sandbox-provisioner hycanvas-app
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans --no-build
    if wait_for_health; then
      echo ">>> 已成功回滚到版本: $PREVIOUS_VERSION"
    else
      echo ">>> 回滚版本健康检查也失败，需要人工介入" >&2
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
    fi
  fi
  exit 1
fi

if [[ -n "$RELEASE_MANIFEST" && -f "$RELEASE_MANIFEST" ]]; then
  cp "$RELEASE_MANIFEST" "$RELEASE_DIR/$VERSION.manifest"
fi
printf '%s\n' "$VERSION" > "$CURRENT_VERSION_FILE"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
echo ">>> 部署完成: $VERSION"
if [[ -n "$PREVIOUS_VERSION" && "$PREVIOUS_VERSION" != "$VERSION" ]]; then
  if [[ -f "$RELEASE_DIR/$PREVIOUS_VERSION.manifest" ]]; then
    echo "回滚命令: cd '$DEPLOY_DIR' && RELEASE_MANIFEST='$RELEASE_DIR/$PREVIOUS_VERSION.manifest' bash scripts/deploy-prod-server.sh '$PREVIOUS_VERSION'"
  else
    echo "历史版本兼容回滚命令: cd '$DEPLOY_DIR' && ALLOW_LEGACY_TAG_ROLLBACK=true bash scripts/deploy-prod-server.sh '$PREVIOUS_VERSION'"
  fi
fi
