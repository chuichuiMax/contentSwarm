#!/usr/bin/env bash
# 上传 *.hi-run.net 证书到生产/测试宿主机并 reload nginx。
set -euo pipefail

HOST="${SSL_DEPLOY_HOST:-172.16.103.17}"
REMOTE_DIR="${SSL_REMOTE_DIR:-/opt/contentSwarm/certs}"
PEM="${SSL_CERT_PEM:-}"
KEY="${SSL_CERT_KEY:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ -n "$PEM" && -f "$PEM" ]] || fail "请设置 SSL_CERT_PEM 为可读 pem 路径"
[[ -n "$KEY" && -f "$KEY" ]] || fail "请设置 SSL_CERT_KEY 为可读 key 路径"

REMOTE="root@${HOST}"
echo "Uploading certs to ${REMOTE}:${REMOTE_DIR} ..."
ssh -o BatchMode=yes "$REMOTE" "mkdir -p '${REMOTE_DIR}' /var/lib/contentswarm/ssl-backup"
scp -o BatchMode=yes "$PEM" "${REMOTE}:${REMOTE_DIR}/hi-run.net.pem"
scp -o BatchMode=yes "$KEY" "${REMOTE}:${REMOTE_DIR}/hi-run.net.key"
ssh -o BatchMode=yes "$REMOTE" bash -s <<EOF
set -euo pipefail
chmod 644 '${REMOTE_DIR}/hi-run.net.pem'
chmod 600 '${REMOTE_DIR}/hi-run.net.key'
if [[ -x /opt/contentSwarm/scripts/ensure-hi-run-ssl-certs.sh ]]; then
  /opt/contentSwarm/scripts/ensure-hi-run-ssl-certs.sh
else
  install -m 644 '${REMOTE_DIR}/hi-run.net.pem' /var/lib/contentswarm/ssl-backup/hi-run.net.pem
  install -m 600 '${REMOTE_DIR}/hi-run.net.key' /var/lib/contentswarm/ssl-backup/hi-run.net.key
fi
nginx -t
systemctl reload nginx
systemctl is-active nginx
EOF
echo "Done. HTTPS certs installed on ${HOST}."
