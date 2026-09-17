#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${DEPLOY_HOST:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
BASE=""
HEAD_REF="HEAD"
VERSION=""
PLAN_ONLY=0

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
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --plan-only)
      PLAN_ONLY=1
      shift
      ;;
    *)
      fail "未知参数: $1"
      ;;
  esac
done

[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || fail "存在未提交的已跟踪文件，请先提交或暂存"
head_sha="$(git -C "$ROOT" rev-parse "$HEAD_REF^{commit}")"
if [[ -z "$BASE" ]]; then
  [[ -n "$HOST" ]] || fail "首次规划必须提供 --base；自动读取服务器基线需要 DEPLOY_HOST"
  BASE="$(ssh "$HOST" "cat '$DEPLOY_DIR/.deploy/incremental/current-git-sha' 2>/dev/null || true")"
  [[ -n "$BASE" ]] || fail "服务器尚无增量发布基线，请用 --base <当前线上Git提交> 初始化"
fi
base_sha="$(git -C "$ROOT" rev-parse "$BASE^{commit}")"
git -C "$ROOT" merge-base --is-ancestor "$base_sha" "$head_sha" \
  || fail "服务器发布基线不是目标提交的祖先，请先确认生产版本"

plan="$("$ROOT/scripts/plan-incremental-release.sh" --base "$base_sha" --head "$head_sha")"
printf '%s\n' "$plan"
mode="$(printf '%s\n' "$plan" | sed -n 's/^release_mode=//p')"
components="$(printf '%s\n' "$plan" | sed -n 's/^components=//p')"
[[ "$mode" == "incremental" ]] || fail "检测到依赖、迁移或运行基础设施变化，请走完整不可变镜像发布"
(( PLAN_ONLY == 0 )) || exit 0
[[ -n "$HOST" ]] || fail "必须设置 DEPLOY_HOST，例如 DEPLOY_HOST=server-47"

if [[ -z "$VERSION" ]]; then
  VERSION="inc-$(git -C "$ROOT" rev-parse --short=8 "$head_sha")-$(date -u +%Y%m%d%H%M%S)"
fi
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || fail "版本号格式无效: $VERSION"

if [[ -z "$components" ]]; then
  ssh "$HOST" "mkdir -p '$DEPLOY_DIR/.deploy/incremental' && printf '%s\\n' '$head_sha' > '$DEPLOY_DIR/.deploy/incremental/current-git-sha'"
  echo ">>> 没有生产运行组件变化，仅更新发布基线"
  exit 0
fi

for command_name in docker rsync tar gzip scp ssh shasum; do
  command -v "$command_name" >/dev/null 2>&1 || fail "缺少本地命令: $command_name"
done

stage="$(mktemp -d "$ROOT/.incremental-release.XXXXXX")"
cleanup() {
  [[ "$stage" == "$ROOT"/.incremental-release.* ]] && rm -rf "$stage"
}
trap cleanup EXIT

mkdir -p "$stage/docker/incremental"
cp "$ROOT/docker/incremental/api.Dockerfile" "$ROOT/docker/incremental/web.Dockerfile" "$stage/docker/incremental/"

if [[ ",$components," == *",api,"* ]]; then
  mkdir -p "$stage/backend"
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$ROOT/backend/package/" "$stage/backend/package/"
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$ROOT/backend/server/" "$stage/backend/server/"
fi

if [[ ",$components," == *",web,"* ]]; then
  docker inspect web-dev >/dev/null 2>&1 || fail "web-dev 未运行，无法在项目容器内构建前端"
  docker exec -e VITE_BASE_PATH=/boyun/ web-dev pnpm run build
  docker cp web-dev:/app/dist "$stage/web-dist"
fi

server_components="$(printf '%s' "$components" | tr ',' '\n' | grep -E '^(api|web)$' | paste -sd, - || true)"
if [[ -n "$server_components" ]]; then
  archive="$stage/incremental-$VERSION.tar.gz"
  archive_items=(docker)
  [[ -d "$stage/backend" ]] && archive_items+=(backend)
  [[ -d "$stage/web-dist" ]] && archive_items+=(web-dist)
  COPYFILE_DISABLE=1 tar czf "$archive" -C "$stage" "${archive_items[@]}"
  archive_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
  ssh "$HOST" "mkdir -p '$DEPLOY_DIR/scripts' /tmp/yuxi-incremental-release"
  scp "$ROOT/scripts/deploy-incremental-server.sh" "$ROOT/scripts/rollback-incremental-server.sh" "${HOST}:$DEPLOY_DIR/scripts/"
  scp "$archive" "${HOST}:/tmp/yuxi-incremental-release/incremental-$VERSION.tar.gz"
  ssh "$HOST" "chmod +x '$DEPLOY_DIR/scripts/deploy-incremental-server.sh' '$DEPLOY_DIR/scripts/rollback-incremental-server.sh' && DEPLOY_DIR='$DEPLOY_DIR' bash '$DEPLOY_DIR/scripts/deploy-incremental-server.sh' '$VERSION' '$head_sha' '/tmp/yuxi-incremental-release/incremental-$VERSION.tar.gz' '$archive_sha' '$server_components' '$base_sha'"
fi

if [[ ",$components," == *",hycanvas,"* ]]; then
  if ! DEPLOY_HOST="$HOST" DEPLOY_DIR="$DEPLOY_DIR" "$ROOT/scripts/deploy-hycanvas-fast.sh" "$VERSION"; then
    if [[ -n "$server_components" ]]; then
      ssh "$HOST" "DEPLOY_DIR='$DEPLOY_DIR' bash '$DEPLOY_DIR/scripts/rollback-incremental-server.sh'"
    fi
    fail "HyCanvas 发布失败，其他增量组件已回滚"
  fi
fi

ssh "$HOST" "mkdir -p '$DEPLOY_DIR/.deploy/incremental' && printf '%s\\n' '$head_sha' > '$DEPLOY_DIR/.deploy/incremental/current-git-sha'"
echo ">>> 增量热发布完成: $VERSION ($components)"
