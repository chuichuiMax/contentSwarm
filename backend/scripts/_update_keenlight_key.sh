#!/usr/bin/env bash
set -eu
ENV_FILE="/opt/contentSwarm/.env"
KEY="${1:?usage: _update_keenlight_key.sh <KEENLIGHT_API_KEY>}"
if grep -q "^KEENLIGHT_API_KEY=" "$ENV_FILE"; then
  sed -i "s|^KEENLIGHT_API_KEY=.*|KEENLIGHT_API_KEY=${KEY}|" "$ENV_FILE"
else
  echo "KEENLIGHT_API_KEY=${KEY}" >> "$ENV_FILE"
fi
echo "KEENLIGHT_API_KEY updated (value hidden)"
