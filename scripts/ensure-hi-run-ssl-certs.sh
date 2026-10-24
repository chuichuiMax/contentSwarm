#!/usr/bin/env bash
# 宿主机 Nginx 启动前：主证书目录有文件则刷新备份；缺失则从 /var/lib 备份恢复。
set -euo pipefail

CERT_DIR="${SSL_CERT_DIR:-/opt/contentSwarm/certs}"
BACKUP_DIR="${SSL_CERT_BACKUP_DIR:-/var/lib/contentswarm/ssl-backup}"
PEM="${CERT_DIR}/hi-run.net.pem"
KEY="${CERT_DIR}/hi-run.net.key"
BACKUP_PEM="${BACKUP_DIR}/hi-run.net.pem"
BACKUP_KEY="${BACKUP_DIR}/hi-run.net.key"

mkdir -p "$CERT_DIR" "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

sync_backup() {
  install -m 644 "$PEM" "$BACKUP_PEM"
  install -m 600 "$KEY" "$BACKUP_KEY"
}

if [[ -s "$PEM" && -s "$KEY" ]]; then
  sync_backup
  exit 0
fi

if [[ -s "$BACKUP_PEM" && -s "$BACKUP_KEY" ]]; then
  install -m 644 "$BACKUP_PEM" "$PEM"
  install -m 600 "$BACKUP_KEY" "$KEY"
  echo "Restored SSL certs from ${BACKUP_DIR} to ${CERT_DIR}"
  exit 0
fi

echo "ERROR: missing hi-run.net.pem / hi-run.net.key in ${CERT_DIR} and ${BACKUP_DIR}" >&2
exit 1
