#!/bin/sh
set -eu

if [ -z "${HYCANVAS_API_KEY:-}" ] || [ -z "${HYCANVAS_WORKSPACE_ID:-}" ]; then
  echo "HYCANVAS_API_KEY and HYCANVAS_WORKSPACE_ID are required" >&2
  exit 1
fi

case "$HYCANVAS_API_KEY" in
  hyk_*) ;;
  *)
    echo "HYCANVAS_API_KEY must start with hyk_" >&2
    exit 1
    ;;
esac

api_key_hash=$(printf %s "$HYCANVAS_API_KEY" | sha256sum | cut -d ' ' -f 1)
api_key_prefix=$(printf %s "$HYCANVAS_API_KEY" | cut -c 1-12)

psql "$DATABASE_URL" \
  -v ON_ERROR_STOP=1 \
  -v workspace_id="$HYCANVAS_WORKSPACE_ID" \
  -v api_key_hash="$api_key_hash" \
  -v api_key_prefix="$api_key_prefix" <<'SQL'
INSERT INTO users (
  id, email, email_verified, name, password_hash, locale, theme,
  mfa_enabled, created_at, updated_at
) VALUES (
  '00000000-0000-4000-8000-000000001001',
  'contentswarm-integration@local.invalid',
  true,
  'ContentSwarm Integration',
  NULL,
  'zh-CN',
  'system',
  false,
  now(),
  now()
) ON CONFLICT (id) DO UPDATE SET
  name = EXCLUDED.name,
  updated_at = now();

INSERT INTO workspaces (id, kind, name, slug, owner_id, created_at, updated_at)
VALUES (
  :'workspace_id'::uuid,
  'TEAM',
  'ContentSwarm Workspace',
  'contentswarm-managed',
  '00000000-0000-4000-8000-000000001001',
  now(),
  now()
) ON CONFLICT (id) DO UPDATE SET
  name = EXCLUDED.name,
  owner_id = EXCLUDED.owner_id,
  updated_at = now();

INSERT INTO workspace_members (
  id, workspace_id, user_id, role, status, joined_at, created_at, updated_at
) VALUES (
  '00000000-0000-4000-8000-000000001002',
  :'workspace_id'::uuid,
  '00000000-0000-4000-8000-000000001001',
  'OWNER',
  'ACTIVE',
  now(),
  now(),
  now()
) ON CONFLICT (workspace_id, user_id) DO UPDATE SET
  role = 'OWNER',
  status = 'ACTIVE',
  updated_at = now();

INSERT INTO api_keys (
  id, workspace_id, user_id, label, prefix, key_hash, scopes, created_at
) VALUES (
  '00000000-0000-4000-8000-000000001003',
  :'workspace_id'::uuid,
  '00000000-0000-4000-8000-000000001001',
  'ContentSwarm managed integration',
  :'api_key_prefix',
  :'api_key_hash',
  ARRAY['generate', 'read', 'export'],
  now()
) ON CONFLICT (id) DO UPDATE SET
  workspace_id = EXCLUDED.workspace_id,
  user_id = EXCLUDED.user_id,
  label = EXCLUDED.label,
  prefix = EXCLUDED.prefix,
  key_hash = EXCLUDED.key_hash,
  scopes = EXCLUDED.scopes,
  revoked_at = NULL;
SQL

echo "HyCanvas ContentSwarm integration is ready."
