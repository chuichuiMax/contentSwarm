#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE=""
HEAD_REF="HEAD"
PATHS_FILE=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      BASE="${2:-}"
      shift 2
      ;;
    --head)
      HEAD_REF="${2:-}"
      shift 2
      ;;
    --paths-file)
      PATHS_FILE="${2:-}"
      shift 2
      ;;
    *)
      fail "未知参数: $1"
      ;;
  esac
done

declare -a changed_paths=()
if [[ -n "$PATHS_FILE" ]]; then
  [[ -f "$PATHS_FILE" ]] || fail "路径清单不存在: $PATHS_FILE"
  while IFS= read -r path || [[ -n "$path" ]]; do
    [[ -n "$path" ]] && changed_paths+=("$path")
  done < "$PATHS_FILE"
else
  [[ -n "$BASE" ]] || fail "必须提供 --base"
  git -C "$ROOT" rev-parse --verify "$BASE^{commit}" >/dev/null
  git -C "$ROOT" rev-parse --verify "$HEAD_REF^{commit}" >/dev/null
  git -C "$ROOT" merge-base --is-ancestor "$BASE" "$HEAD_REF" \
    || fail "发布基线不是目标提交的祖先，请先确认生产版本"
  while IFS= read -r path; do
    [[ -n "$path" ]] && changed_paths+=("$path")
  done < <(git -C "$ROOT" diff --name-only "$BASE..$HEAD_REF")
fi

api=0
web=0
hycanvas=0
declare -a blocked=()

for path in "${changed_paths[@]}"; do
  case "$path" in
    backend/pyproject.toml|backend/uv.lock|backend/.python-version|backend/alembic/*|backend/migrations/*|backend/package/yuxi/storage/postgres/migrations/*)
      blocked+=("$path")
      ;;
    web/package.json|web/pnpm-lock.yaml|web/package-lock.json|web/yarn.lock)
      blocked+=("$path")
      ;;
    apps/hycanvas/go.mod|apps/hycanvas/go.sum|apps/hycanvas/package.json|apps/hycanvas/package-lock.json|apps/hycanvas/*/package.json|apps/hycanvas/*/package-lock.json)
      blocked+=("$path")
      ;;
    docker/incremental/*)
      ;;
    docker/*|docker-compose*.yml|.github/workflows/*|scripts/deploy-prod-server.sh|scripts/push-and-deploy.sh)
      blocked+=("$path")
      ;;
    backend/package/*|backend/server/*)
      api=1
      ;;
    web/*)
      web=1
      ;;
    apps/hycanvas/*)
      hycanvas=1
      ;;
    backend/test/*|docs/*|README*|LICENSE*|.gitignore|AGENTS.md|ARCHITECTURE.md|scripts/*)
      ;;
    *)
      blocked+=("$path")
      ;;
  esac
done

components=""
(( api == 1 )) && components="api"
if (( web == 1 )); then
  components="${components:+$components,}web"
fi
if (( hycanvas == 1 )); then
  components="${components:+$components,}hycanvas"
fi

if (( ${#blocked[@]} > 0 )); then
  echo "release_mode=full_required"
else
  echo "release_mode=incremental"
fi
echo "components=$components"
echo "changed_count=${#changed_paths[@]}"
echo "blocked_count=${#blocked[@]}"
if (( ${#blocked[@]} > 0 )); then
  for path in "${blocked[@]}"; do
    echo "blocked_path=$path"
  done
fi
