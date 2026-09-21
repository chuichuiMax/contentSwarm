#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "$*" >&2
  exit 1
}

require_absolute_path() {
  local name=$1
  local value=$2
  [[ "$value" == /* ]] || fail "$name 必须是绝对路径: $value"
}

tree_digest() {
  local directory=$1
  (
    cd "$directory"
    while IFS= read -r -d '' file; do
      sha256sum "$file"
    done < <(find . -type f -print0 | LC_ALL=C sort -z)
  ) | sha256sum | awk '{print $1}'
}

manifest_value() {
  local key=$1
  local manifest=$2
  sed -n "s/^${key}=//p" "$manifest"
}

validate_release() {
  local directory=$1
  local expected_sha=$2
  local manifest="$directory/manifest.env"

  [[ -f "$directory/web-dist/index.html" ]] || return 1
  [[ -x "$directory/hycanvas" ]] || return 1
  [[ -f "$manifest" ]] || return 1
  [[ "$(manifest_value FORMAT_VERSION "$manifest")" == "1" ]] || return 1
  [[ "$(manifest_value GIT_SHA "$manifest")" == "$expected_sha" ]] || return 1
  [[ "$(manifest_value WEB_SHA256 "$manifest")" == "$(tree_digest "$directory/web-dist")" ]] || return 1
  [[ "$(manifest_value HYCANVAS_SHA256 "$manifest")" == "$(sha256sum "$directory/hycanvas" | awk '{print $1}')" ]]
}

switch_release() {
  local git_sha=$1
  local current_link="$release_root/current"
  local previous_link="$release_root/previous"
  local current_next="$release_root/current.next"
  local previous_next="$release_root/previous.next"
  local old_target=""

  [[ ! -e "$current_link" || -L "$current_link" ]] || fail "current 必须是符号链接"
  [[ ! -e "$previous_link" || -L "$previous_link" ]] || fail "previous 必须是符号链接"

  if [[ -L "$current_link" ]]; then
    old_target=$(readlink "$current_link")
    [[ "$old_target" =~ ^[0-9a-f]{40,64}$ ]] || fail "current 指向非法发布版本: $old_target"
    validate_release "$release_root/$old_target" "$old_target" \
      || fail "当前发布目录不完整: $old_target"
  fi

  rm -f -- "$current_next" "$previous_next"
  if [[ -n "$old_target" && "$old_target" != "$git_sha" ]]; then
    ln -s "$old_target" "$previous_next"
    mv -Tf -- "$previous_next" "$previous_link"
  fi

  ln -s "$git_sha" "$current_next"
  mv -Tf -- "$current_next" "$current_link"
}

SOURCE_ROOT=${SOURCE_ROOT:-}
RELEASE_ROOT=${RELEASE_ROOT:-}
CACHE_ROOT=${CACHE_ROOT:-}
WORK_ROOT=${WORK_ROOT:-}
VITE_BASE_PATH=${VITE_BASE_PATH:-/}
CONTENTSWARM_PUBLIC_URL=${CONTENTSWARM_PUBLIC_URL:-}

[[ -n "$SOURCE_ROOT" ]] || fail "缺少 SOURCE_ROOT"
[[ -n "$RELEASE_ROOT" ]] || fail "缺少 RELEASE_ROOT"
[[ -n "$CACHE_ROOT" ]] || fail "缺少 CACHE_ROOT"
[[ -n "$WORK_ROOT" ]] || fail "缺少 WORK_ROOT"

require_absolute_path SOURCE_ROOT "$SOURCE_ROOT"
require_absolute_path RELEASE_ROOT "$RELEASE_ROOT"
require_absolute_path CACHE_ROOT "$CACHE_ROOT"
require_absolute_path WORK_ROOT "$WORK_ROOT"

[[ -d "$SOURCE_ROOT" ]] || fail "源码目录不存在: $SOURCE_ROOT"
source_root=$(realpath "$SOURCE_ROOT")
git_root=$(git -C "$source_root" rev-parse --show-toplevel 2>/dev/null) \
  || fail "SOURCE_ROOT 不是 Git 工作区"
git_root=$(realpath "$git_root")
[[ "$git_root" == "$source_root" ]] || fail "SOURCE_ROOT 必须指向 Git 工作区根目录"

if [[ -n "$(git -C "$source_root" status --porcelain --untracked-files=no)" ]]; then
  fail "工作区存在未提交的已跟踪文件"
fi

git_sha=$(git -C "$source_root" rev-parse HEAD)
[[ "$git_sha" =~ ^[0-9a-f]{40,64}$ ]] || fail "Git SHA 格式非法: $git_sha"

mkdir -p "$RELEASE_ROOT" "$CACHE_ROOT" "$WORK_ROOT"
release_root=$(realpath "$RELEASE_ROOT")
cache_root=$(realpath "$CACHE_ROOT")
work_root=$(realpath "$WORK_ROOT")

case "$release_root" in
  "$source_root"|"$source_root"/*)
    fail "RELEASE_ROOT 不能位于 Git 工作区内"
    ;;
esac

for command_name in git pnpm npm go sha256sum; do
  command -v "$command_name" >/dev/null || fail "缺少构建命令: $command_name"
done

version_dir="$release_root/$git_sha"
if [[ -e "$version_dir" ]]; then
  validate_release "$version_dir" "$git_sha" || fail "已存在的发布目录不完整: $version_dir"
  switch_release "$git_sha"
  echo "复用已完成发布: $git_sha"
  exit 0
fi

stage_dir="$release_root/.${git_sha}.tmp"
build_dir="$work_root/release-$git_sha"
stage_created=0
build_created=0

cleanup() {
  if [[ "$stage_created" == 1 && "$stage_dir" == "$release_root"/.*.tmp ]]; then
    rm -rf -- "$stage_dir"
  fi
  if [[ "$build_created" == 1 && "$build_dir" == "$work_root"/release-* ]]; then
    rm -rf -- "$build_dir"
  fi
  rm -f -- "$release_root/current.next" "$release_root/previous.next"
}
trap cleanup EXIT

[[ ! -e "$stage_dir" ]] || fail "存在未清理的发布暂存目录: $stage_dir"
[[ ! -e "$build_dir" ]] || fail "存在未清理的构建目录: $build_dir"

mkdir "$stage_dir" "$build_dir"
stage_created=1
build_created=1

web_work="$build_dir/web"
hycanvas_work="$build_dir/hycanvas"
mkdir "$web_work" "$hycanvas_work"
cp -a "$source_root/web/." "$web_work/"
cp -a "$source_root/apps/hycanvas/." "$hycanvas_work/"

mkdir -p "$cache_root/pnpm" "$cache_root/npm" "$cache_root/go-mod" "$cache_root/go-build"

(
  cd "$web_work"
  pnpm install --frozen-lockfile --store-dir "$cache_root/pnpm"
  VITE_BASE_PATH="$VITE_BASE_PATH" pnpm run build
)
[[ -f "$web_work/dist/index.html" ]] || fail "Web 构建未生成 dist/index.html"
mkdir "$stage_dir/web-dist"
cp -a "$web_work/dist/." "$stage_dir/web-dist/"

(
  cd "$hycanvas_work"
  npm ci --cache "$cache_root/npm" --no-audit --no-fund
  npm run build:packages
  deployment_id=$(printf '%s' "$git_sha" | tr -c '[:alnum:]_-' '-')
  HYCANVAS_AUTH_MODE=standalone \
    CONTENTSWARM_URL="$CONTENTSWARM_PUBLIC_URL" \
    NEXT_PUBLIC_HYCANVAS_AUTH_MODE=standalone \
    NEXT_PUBLIC_CONTENTSWARM_URL="$CONTENTSWARM_PUBLIC_URL" \
    HYCANVAS_DEPLOYMENT_ID="$deployment_id" \
    npm run build:dist -w frontend
)
[[ -f "$hycanvas_work/frontend/out/index.html" ]] || fail "HyCanvas 前端未生成 frontend/out/index.html"

rm -rf -- "$hycanvas_work/backend/internal/webui/public"
mkdir -p "$hycanvas_work/backend/internal/webui/public"
cp -a "$hycanvas_work/frontend/out/." "$hycanvas_work/backend/internal/webui/public/"

(
  cd "$hycanvas_work/backend"
  CGO_ENABLED=0 GOMODCACHE="$cache_root/go-mod" GOCACHE="$cache_root/go-build" \
    go build -tags embed -trimpath \
      -ldflags "-s -w -X main.version=$git_sha" \
      -o "$stage_dir/hycanvas" ./cmd/api
)
chmod 0755 "$stage_dir/hycanvas"

web_sha=$(tree_digest "$stage_dir/web-dist")
hycanvas_sha=$(sha256sum "$stage_dir/hycanvas" | awk '{print $1}')
cat > "$stage_dir/manifest.env" <<EOF
FORMAT_VERSION=1
GIT_SHA=$git_sha
WEB_SHA256=$web_sha
HYCANVAS_SHA256=$hycanvas_sha
BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

validate_release "$stage_dir" "$git_sha" || fail "发布产物完整性校验失败"
mv -T -- "$stage_dir" "$version_dir"
stage_created=0

switch_release "$git_sha"
echo "发布产物构建完成: $git_sha"
