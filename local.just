# SNORE-local recipes — not managed by github-config.

# Dev servers

# Start the API dev server with hot-reload
dev-api:
    uv run snore serve --reload

# Start the UI dev server with hot-reload
dev-ui:
    cd ui && pnpm run dev

# Build UI and start the server
serve: web-build
    uv run snore serve

# Build frontend + backend package
build-all: web-build build

# Screenshots

# Install Playwright Chromium browser for screenshot tests
ui-playwright-install:
    cd ui && pnpm exec playwright install chromium

# Build UI and capture the screenshot regression battery (local eyeballing — never post to PRs)
screenshot:
    just web-build
    cd ui && pnpm exec playwright test screenshot.spec.ts

# Generated API types

# Export OpenAPI schema and regenerate TypeScript types
ui-generate-types:
    uv run python scripts/export_openapi.py ui/openapi.json
    cd ui && pnpm run generate:types
