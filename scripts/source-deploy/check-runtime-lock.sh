#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: $0 {write|verify} <摘要文件> <lock 文件>..." >&2
  exit 2
}

[[ $# -ge 3 ]] || usage

mode=$1
digest_file=$2
shift 2

calculate_digest() {
  local lock_file

  for lock_file in "$@"; do
    [[ -f "$lock_file" ]] || {
      echo "缺少依赖锁文件: $lock_file" >&2
      return 1
    }
    sed 's/\r$//' "$lock_file" | sha256sum | awk '{print $1}'
  done | sha256sum | awk '{print $1}'
}

case "$mode" in
  write)
    mkdir -p "$(dirname "$digest_file")"
    temporary_file=$(mktemp "${digest_file}.tmp.XXXXXX")
    trap 'rm -f "$temporary_file"' EXIT
    calculate_digest "$@" > "$temporary_file"
    mv -f "$temporary_file" "$digest_file"
    trap - EXIT
    ;;
  verify)
    [[ -f "$digest_file" ]] || {
      echo "缺少基础镜像依赖摘要: $digest_file" >&2
      exit 1
    }
    actual_file=$(mktemp)
    trap 'rm -f "$actual_file"' EXIT
    calculate_digest "$@" > "$actual_file"
    if ! cmp -s "$digest_file" "$actual_file"; then
      echo "基础镜像依赖摘要不匹配，请先更新基础镜像版本" >&2
      exit 1
    fi
    ;;
  *)
    usage
    ;;
esac
