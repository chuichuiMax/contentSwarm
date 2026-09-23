#!/usr/bin/env bash
set -Eeuo pipefail

# 针对当家测试服务器的日常业务代码发布入口。实际部署、健康检查和失败回滚
# 统一复用 deploy-source.sh，避免两套发布流程产生行为差异。
repo=${CONTENTSWARM_TEST_REPO:-/dangjia/project/contentswarm/repo}

export SOURCE_DEPLOY_REPO="$repo"
export SOURCE_DEPLOY_RELEASE_ROOT=${CONTENTSWARM_TEST_RELEASE_ROOT:-/dangjia/project/contentswarm/releases}
export SOURCE_DEPLOY_ENV_FILE=${CONTENTSWARM_TEST_ENV_FILE:-$repo/.env.test}
export SOURCE_DEPLOY_COMPOSE_FILE=${CONTENTSWARM_TEST_COMPOSE_FILE:-$repo/docker-compose-test.yml}
export SOURCE_DEPLOY_BRANCH=${CONTENTSWARM_TEST_BRANCH:-master}
export SOURCE_DEPLOY_STATE_ROOT=${CONTENTSWARM_TEST_STATE_ROOT:-/dangjia/project/contentswarm/.deploy/source-deploy-test}
export SOURCE_DEPLOY_API_HEALTH_URL=${CONTENTSWARM_TEST_API_HEALTH_URL:-http://127.0.0.1:8090/api/system/health}
export SOURCE_DEPLOY_WEB_HEALTH_URL=${CONTENTSWARM_TEST_WEB_HEALTH_URL:-http://127.0.0.1:8090/}
export SOURCE_DEPLOY_HYCANVAS_HEALTH_URL=${CONTENTSWARM_TEST_HYCANVAS_HEALTH_URL:-http://127.0.0.1:8005/healthz}

exec bash "$repo/scripts/source-deploy/deploy-source.sh" "$@"
