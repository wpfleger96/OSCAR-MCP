# Local development overrides for SNORE.
# Imported by the main justfile via `import? 'local.just'`.

# Shared environment for multiuser dev servers: throwaway secret, loopback only.
multiuser_env := "SNORE_AUTH_MODE=multiuser SNORE_SESSION_SECRET=dev-throwaway-secret-not-for-production-1234567890ab SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 SNORE_BIND_HOST=127.0.0.1"
smoke_db := "/tmp/snore-smoke.db"

# Start the API in LOCAL auth mode (single-user, no login, identical to pre-multiuser UX).
dev: web-install web-build
    SNORE_AUTH_MODE=local uv run uvicorn snore.api.app:create_app --factory --reload --reload-dir src --host 127.0.0.1 --port 8000

# Start the API in MULTIUSER auth mode with a throwaway secret and a seeded invite.
# Prints the signup URL to stdout so you can paste it into a browser.
dev-auth: web-install web-build _dev-auth-seed
    {{multiuser_env}} \
    SNORE_DEV_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 \
    uv run snore serve --host 127.0.0.1 --port 8000 --reload

# Seed an invite for dev-auth; print the signup URL.
_dev-auth-seed:
    @echo "--- Dev-auth invite ---"
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    uv run snore user invite dev@localhost --role admin

# Seed fake Google bindings into the dev DB so the /admin/mcp reset flows are
# testable under `just dev-auth` (no real Google OAuth needed). Idempotent.
dev-seed-bindings:
    uv run python scripts/seed_dev_google_bindings.py

# Fully reset local dev state: stop stray dev servers (orphaned uvicorn
# reload workers are the usual "database is locked" culprit), drop the DB,
# and clear staged data. `snore db drop` skips sidecar cleanup when the main
# DB file is already gone, so -wal/-shm are removed explicitly. Logs are
# kept. The next `just dev` recreates the schema via startup migrations and
# re-provisions the local user; re-import your data afterwards.
reset:
    -pkill -f "[u]vicorn snore.api.app" 2>/dev/null
    -pkill -f "[s]nore serve" 2>/dev/null
    -uv run snore db drop --force
    rm -f ~/.snore/snore.db ~/.snore/snore.db-wal ~/.snore/snore.db-shm ~/.snore/vacuum.pending ~/.snore/writers.lock
    rm -rf ~/.snore/raw ~/.snore/spool
    @echo "Local dev state reset — run 'just dev' and re-import your data."

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
# `just smoke 1` requires TOTP enrollment from the first login.
smoke require_totp="0" db=smoke_db: web-install web-build (_smoke-fresh db) (smoke-invite "dev@localhost" "admin" db) (smoke-serve require_totp db)

# Wipe the smoke DB for a fresh run.
_smoke-fresh db=smoke_db:
    rm -f {{db}} {{db}}-wal {{db}}-shm
    @echo "--- Fresh smoke DB at {{db}} ---"

# Restart the smoke server against the EXISTING smoke DB (no wipe, no rebuild).
# `just smoke-serve 1` turns on mandatory TOTP enrollment mid-test.
smoke-serve require_totp="0" db=smoke_db:
    {{multiuser_env}} \
    SNORE_REQUIRE_TOTP={{require_totp}} \
    uv run snore serve --host 127.0.0.1 --port 8000 --db {{db}}

# Seed an extra invite into the smoke DB and print the signup URL.
# Run from a second terminal while the smoke server is up.
smoke-invite email="member@localhost" role="member" db=smoke_db:
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    uv run snore user invite {{email}} --role {{role}} --db {{db}}
