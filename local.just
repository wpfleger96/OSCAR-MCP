# UI Code Quality - Check variants

# Run TypeScript type checking
ui-type-check:
    cd ui && pnpm run type-check

# Run UI linter in check mode (no fixes)
ui-lint-check:
    cd ui && pnpm run lint-check

# Run UI formatter in check mode (no changes)
ui-format-check:
    cd ui && pnpm run format-check

# UI Code Quality - Fix variants

# Run UI linter and auto-fix issues
ui-lint:
    cd ui && pnpm run lint

# Run UI formatter and auto-fix style
ui-format:
    cd ui && pnpm run format

# UI dependencies & build

# Install UI dependencies
ui-install:
    cd ui && pnpm install

# Build the UI for production
ui-build:
    cd ui && pnpm run build

# Dev servers

# Start the API dev server with hot-reload
dev-api:
    uv run snore serve --reload

# Start the UI dev server with hot-reload
dev-ui:
    cd ui && pnpm run dev

# Build UI and start the server
serve: ui-build
    uv run snore serve

# Build frontend + backend package
build-all: ui-build build

# Screenshots

# Install Playwright Chromium browser for screenshot tests
ui-playwright-install:
    cd ui && pnpm exec playwright install chromium

# Build UI and capture screenshots with Playwright
screenshot:
    just ui-build
    cd ui && pnpm exec playwright test screenshot.spec.ts

# Post captured screenshots to a GitHub PR
screenshot-post PR_NUMBER:
    bash scripts/post-screenshots.sh {{PR_NUMBER}}

# Build, capture, and post screenshots to a GitHub PR
screenshot-pr PR_NUMBER: screenshot
    just screenshot-post {{PR_NUMBER}}

# Generated API types

# Export OpenAPI schema and regenerate TypeScript types
ui-generate-types:
    uv run python scripts/export_openapi.py ui/openapi.json
    cd ui && pnpm run generate:types
