#!/usr/bin/env bash
# 同步 AI 封面 OSS 目录相关改动（含未入库新文件）。默认目标：103.15 当家测试机。
set -euo pipefail

HOST="${1:-root@172.16.103.15}"
BASE="${2:-/srv/contentswarm}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

FILES=(
  backend/package/yuxi/content_cover/ai_cover_prompt_catalog.py
  backend/package/yuxi/content_cover/ai_cover_prompt.py
  backend/package/yuxi/services/ai_cover_prompt_catalog_service.py
  backend/server/routers/content_cover_router.py
  web/src/views/ContentStudioView.vue
  web/src/apis/content_api.js
  web/src/constants/aiCoverPromptCatalog.js
)

for rel in "${FILES[@]}"; do
  src="$ROOT/$rel"
  [[ -f "$src" ]] || { echo "缺少文件: $src" >&2; exit 1; }
  dest_dir="$BASE/$(dirname "$rel")"
  ssh "$HOST" "mkdir -p '$dest_dir'"
  scp "$src" "$HOST:$BASE/$rel"
  echo "OK $rel"
done

echo "已同步。在目标机 .env 追加（参考图在 hongyang02，与 OSS_BUCKET 可不同）："
cat <<'EOF'
AI_COVER_OSS_BUCKET=hongyang02
AI_COVER_OSS_REGION=cn-hangzhou
AI_COVER_OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
AI_COVER_OSS_PUBLIC_BASE_URL=https://hongyang02.oss-cn-hangzhou.aliyuncs.com
EOF
echo "然后: cd $BASE && docker compose --env-file .env up -d --no-deps --force-recreate api web"
echo "若预览 500 且日志 ModuleNotFoundError: alibabacloud_oss_v2，在 api-dev 内执行:"
echo "  docker exec api-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0'"
echo "验证: curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p7/preview.png?content_type_code=CT06'"
echo "公网 124.232.148.28:8080 为 103.15；124.232.148.18 为 103.17。"
