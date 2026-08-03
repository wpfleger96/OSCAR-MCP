# Local development overrides for SNORE.
# Imported by the main justfile via `import? 'local.just'`.

# Start the API in LOCAL auth mode (single-user, no login, identical to pre-multiuser UX).
dev:
    SNORE_AUTH_MODE=local uv run uvicorn snore.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000

# Start the API in MULTIUSER auth mode with a throwaway secret and a seeded invite.
# Prints the signup URL to stdout so you can paste it into a browser.
dev-auth: _dev-auth-seed
    SNORE_AUTH_MODE=multiuser \
    SNORE_SESSION_SECRET=dev-throwaway-secret-not-for-production-1234567890ab \
    SNORE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    SNORE_BIND_HOST=127.0.0.1 \
    uv run uvicorn snore.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000

# Seed an invite for dev-auth; print the signup URL.
_dev-auth-seed:
    @echo "--- Dev-auth invite ---"
    uv run snore invite create --email dev@localhost --role admin 2>/dev/null || true
    @echo "Open: http://127.0.0.1:8000/invite (check snore invite list for the token)"
