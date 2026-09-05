#!/bin/sh
set -e

echo "🚀 Starting HyCanvas development environment..."

# Wait for PostgreSQL. Host/port are parsed from DATABASE_URL
# (postgresql://user:pass@host:port/db); falls back to the compose service name.
wait_for_postgres() {
  DB_HOST=$(echo "$DATABASE_URL" | sed -n 's#.*@\([^:/]*\).*#\1#p')
  DB_PORT=$(echo "$DATABASE_URL" | sed -n 's#.*@[^:/]*:\([0-9]*\).*#\1#p')
  DB_HOST="${DB_HOST:-db}"
  DB_PORT="${DB_PORT:-5432}"
  echo "⏳ Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
  attempt=0
  until nc -z "$DB_HOST" "$DB_PORT" 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
      echo "❌ PostgreSQL did not become ready in time"
      exit 1
    fi
    sleep 2
  done
  echo "✅ PostgreSQL is ready."
}

wait_for_postgres

# Install deps into the named node_modules volume only when package-lock.json
# changes. Recreating the dev container should not reinstall hundreds of packages.
deps_marker="node_modules/.hycanvas-package-lock.sha256"
lock_hash=$(sha256sum package-lock.json | cut -d ' ' -f 1)
node_arch=$(node -p 'process.arch')
lightning_native="lightningcss-linux-${node_arch}-gnu"
oxide_native="@tailwindcss/oxide-linux-${node_arch}-gnu"
deps_ready=false
if [ -d "node_modules/$lightning_native" ] && [ -d "node_modules/$oxide_native" ]; then
  deps_ready=true
fi
if [ ! -f "$deps_marker" ] || [ "$(cat "$deps_marker")" != "$lock_hash" ] || [ "$deps_ready" != true ]; then
  echo ""
  echo "📦 Installing dependencies (lock changed or native modules missing)..."
  npm install --no-audit --no-fund
else
  echo "📦 Dependencies unchanged; using the existing node_modules volume."
fi

# npm may hoist platform-specific optional packages to the workspace root while
# lightningcss and Tailwind resolve their native bindings as sibling folders.
# Link the Linux binding into the frontend workspace so Docker works with a
# package-lock generated on macOS too.
ln -sfn "/app/node_modules/$lightning_native" "frontend/node_modules/$lightning_native"
mkdir -p "frontend/node_modules/@tailwindcss"
ln -sfn "/app/node_modules/$oxide_native" "frontend/node_modules/$oxide_native"
node -e "require('./frontend/node_modules/lightningcss'); require('./frontend/node_modules/@tailwindcss/oxide')"
printf '%s\n' "$lock_hash" > "$deps_marker"

packages_marker="node_modules/.hycanvas-packages.sha256"
packages_hash=$(
  find packages -type f \( -path '*/src/*' -o -name package.json -o -name tsconfig.json \) -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | sha256sum \
    | cut -d ' ' -f 1
)
if [ ! -f "$packages_marker" ] || [ "$(cat "$packages_marker")" != "$packages_hash" ]; then
  echo ""
  echo "🔨 Building @hc/* packages (shared sources changed)..."
  npm run build:packages
  printf '%s\n' "$packages_hash" > "$packages_marker"
else
  echo "🔨 Shared packages unchanged; using existing build output."
fi

# The server also auto-migrates on boot (DB_AUTO_MIGRATE), but run it up front so
# the schema is ready before the frontend starts hitting the API.
echo ""
echo "🗃️  Applying database migrations..."
npm run db:migrate || echo "⚠️  Migration failed or already up to date; continuing"

echo ""
echo "🎯 Starting dev servers - backend on :8005, frontend on :3000..."
# Backend via air (rebuild + restart on .go changes); frontend via next dev. The
# container env (compose `environment:` + the loaded .env) supplies all config to
# both, so neither needs the dotenv wrapper the native `npm run dev` uses.
exec npx concurrently -k -n backend,frontend,generated -c blue,green,magenta \
  "cd backend && air -c .air.toml" \
  "npm run dev -w frontend -- -H 0.0.0.0 -p 3000" \
  "node scripts/watch-dev-builds.mjs"
