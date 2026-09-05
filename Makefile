
.PHONY: up up-lite down logs lint format seed reset hycanvas-check hycanvas-build

PYTEST_ARGS ?=

up:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	docker compose up -d

down:
	docker compose down

reset:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	docker compose down
	rm -rf docker/volumes
	docker compose up -d
	@echo "Waiting for api to be ready..."
	@until docker compose exec -T api true >/dev/null 2>&1; do sleep 2; done
	$(MAKE) seed

up-lite:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	LITE_MODE=true VITE_USE_RUNS_API=false docker compose up -d postgres redis minio api web

logs:
	@docker logs --tail=50 api-dev
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

seed:
	docker compose exec api uv run python scripts/seed_initial_users.py

######################
# LINTING AND FORMATTING
######################

format:
	cd backend && uv run ruff format package
	cd backend && uv run ruff check package --fix
	cd backend && uv run ruff check --select I package --fix
	cd web && pnpm run format
	cd web && pnpm run lint

hycanvas-check:
	cd apps/hycanvas && npm run lint -w frontend
	cd apps/hycanvas && docker run --rm -v "$$PWD/backend:/src" -v hycanvas-go-mod-cache:/go/pkg/mod -w /src golang:1.25-bookworm /usr/local/go/bin/go test ./...

hycanvas-build:
	cd apps/hycanvas && npm run build:packages
	cd apps/hycanvas && HYCANVAS_AUTH_MODE=contentswarm CONTENTSWARM_URL=http://127.0.0.1:5173 BUILD_DIST=true npm run build:dist -w frontend
