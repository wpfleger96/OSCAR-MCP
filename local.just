# Local development overrides for SNORE.
# Imported by the main justfile via `import? 'local.just'`.

# Start the API in LOCAL auth mode (single-user, no login, identical to pre-multiuser UX).
dev: web-install web-build
    SNORE_AUTH_MODE=local uv run uvicorn snore.api.app:create_app --factory --reload --reload-dir src --host 127.0.0.1 --port 8000

# Start the API in MULTIUSER auth mode with a throwaway secret and a seeded invite.
# Prints the signup URL to stdout so you can paste it into a browser.
dev-auth: web-install web-build _dev-auth-seed
    SNORE_AUTH_MODE=multiuser \
    SNORE_SESSION_SECRET=dev-throwaway-secret-not-for-production-1234567890ab \
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    SNORE_BIND_HOST=127.0.0.1 \
    SNORE_DEV_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 \
    uv run snore serve --host 127.0.0.1 --port 8000 --reload

# Seed an invite for dev-auth; print the signup URL.
_dev-auth-seed:
    @echo "--- Dev-auth invite ---"
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    uv run snore user invite dev@localhost --role admin

# Build the Docker image locally for smoke-testing.
docker-build:
    docker build -t snore .

# Regenerate ui/src/types/generated.ts from the FastAPI OpenAPI spec.
# Local auth mode: building the app requires no session secret, and all
# routers/schemas are mounted regardless of mode.
ui-generate-types:
    SNORE_AUTH_MODE=local uv run python scripts/export_openapi.py ui/openapi.json
    cd ui && pnpm run generate:types

# Full-stack multiuser smoke test: build the UI, start from a fresh throwaway DB,
# seed an admin invite, and serve everything at http://127.0.0.1:8000.
# Open the printed invite URL to walk the signup → login → import journey.
smoke db="/tmp/snore-smoke.db": web-install web-build
    rm -f {{db}} {{db}}-wal {{db}}-shm
    @echo "--- Smoke invite (fresh DB at {{db}}) ---"
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    uv run snore user invite dev@localhost --role admin --db {{db}}
    SNORE_AUTH_MODE=multiuser \
    SNORE_SESSION_SECRET=dev-throwaway-secret-not-for-production-1234567890ab \
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    SNORE_BIND_HOST=127.0.0.1 \
    uv run snore serve --host 127.0.0.1 --port 8000 --db {{db}}
