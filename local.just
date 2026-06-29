# UI Code Quality - Check variants

# Run TypeScript type checking
ui-type-check:
    cd ui && npm run type-check

# Run UI linter in check mode (no fixes)
ui-lint-check:
    cd ui && npm run lint-check

# Run UI formatter in check mode (no changes)
ui-format-check:
    cd ui && npm run format-check

# UI Code Quality - Fix variants

# Run UI linter and auto-fix issues
ui-lint:
    cd ui && npm run lint

# Run UI formatter and auto-fix style
ui-format:
    cd ui && npm run format

# UI dependencies & build

# Install UI npm dependencies
ui-install:
    cd ui && npm install

# Build the UI for production
ui-build:
    cd ui && npm run build

# Dev servers

# Start the API dev server with hot-reload
dev-api:
    uv run snore serve --reload

# Start the UI dev server with hot-reload
dev-ui:
    cd ui && npm run dev

# Build UI and start the server
serve: ui-build
    uv run snore serve

# Build frontend + backend package
build-all: ui-build build

# Screenshots

# Install Playwright Chromium browser for screenshot tests
ui-playwright-install:
    cd ui && npx playwright install chromium

# Build UI and capture screenshots with Playwright
screenshot:
    just ui-build
    cd ui && npx playwright test screenshot.spec.ts

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
    cd ui && npm run generate:types
