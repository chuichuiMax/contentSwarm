#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/yuxi}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "服务器尚未安装 Docker"
docker compose version >/dev/null 2>&1 || fail "服务器尚未安装 Docker Compose 插件"

echo ">>> 只读资源检查"
uptime
free -h
swapon --show
df -h
df -ih
docker system df
if [[ -d "$DEPLOY_DIR" && -f "$DEPLOY_DIR/$ENV_FILE" && -f "$DEPLOY_DIR/$COMPOSE_FILE" ]]; then
  (
    cd "$DEPLOY_DIR"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
  )
else
  echo ">>> 部署目录尚未就绪，改为显示当前 Docker 容器"
  docker ps
fi

check_filesystem_capacity() {
  local path="$1"
  local label="$2"
  local stats total_kb available_kb used_percent inode_used_percent
  stats="$(df -Pk "$path" | awk 'NR==2 {print $2, $4, $5}')"
  read -r total_kb available_kb used_percent <<<"${stats//%/}"
  (( used_percent < 85 )) || fail "${label}使用率达到 ${used_percent}%"
  (( available_kb >= 10485760 )) || fail "${label}可用空间不足 10 GiB"
  (( available_kb * 100 >= total_kb * 20 )) || fail "${label}可用空间不足 20%"
  inode_used_percent="$(df -Pi "$path" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
  (( inode_used_percent < 85 )) || fail "${label} inode 使用率达到 ${inode_used_percent}%"
}

DEPLOY_CHECK_PATH="$DEPLOY_DIR"
while [[ ! -e "$DEPLOY_CHECK_PATH" && "$DEPLOY_CHECK_PATH" != "/" ]]; do
  DEPLOY_CHECK_PATH="$(dirname "$DEPLOY_CHECK_PATH")"
done
DOCKER_ROOT_DIR="$(docker info --format '{{.DockerRootDir}}')"
check_filesystem_capacity / "根文件系统"
check_filesystem_capacity "$DEPLOY_CHECK_PATH" "部署目录文件系统"
check_filesystem_capacity "$DOCKER_ROOT_DIR" "Docker 数据目录文件系统"

mem_total_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
mem_available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
(( mem_available_kb >= 2097152 )) || fail "MemAvailable 少于 2 GiB"
(( mem_available_kb * 100 >= mem_total_kb * 25 )) || fail "MemAvailable 少于总内存的 25%"

load5="$(awk '{print $2}' /proc/loadavg)"
cpu_count="$(nproc)"
awk -v load="$load5" -v cpus="$cpu_count" 'BEGIN {exit !(load > cpus)}' \
  && fail "最近 5 分钟负载 ${load5} 高于 CPU 核心数 ${cpu_count}" || true

if pgrep -af '[d]ocker build|[d]ocker compose build|[n]pm run build|[p]npm build|[y]arn build|[m]vn package|[g]radle build|[p]g_dump|[m]ongodump|[r]sync.*backup|[d]atabase.*migrat' >/dev/null; then
  fail "检测到其他构建、备份或迁移任务，停止部署"
fi
if command -v journalctl >/dev/null 2>&1; then
  kernel_log="$(journalctl -k --since '-60 min' --no-pager 2>/dev/null || true)"
  if grep -qiE 'oom|out of memory|killed process|panic|i/o error|ext4.*error|xfs.*error' <<<"$kernel_log"; then
    fail "最近 60 分钟内核日志存在 OOM、I/O 或文件系统异常"
  fi
fi

echo ">>> 服务器资源预检通过"
