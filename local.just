# UI Code Quality - Check variants
ui-type-check:
    cd ui && npm run type-check

ui-lint-check:
    cd ui && npm run lint-check

ui-format-check:
    cd ui && npm run format-check

# UI Code Quality - Fix variants
ui-lint:
    cd ui && npm run lint

ui-format:
    cd ui && npm run format

# UI dependencies & build
ui-install:
    cd ui && npm install

ui-build:
    cd ui && npm run build

# Dev servers
dev-api:
    uv run snore serve --reload

dev-ui:
    cd ui && npm run dev

# Build frontend + backend package
build-all: ui-build build

# Screenshots
ui-playwright-install:
    cd ui && npx playwright install chromium

screenshot:
    just ui-build
    cd ui && npx playwright test screenshot.spec.ts

screenshot-post PR_NUMBER:
    bash scripts/post-screenshots.sh {{PR_NUMBER}}

screenshot-pr PR_NUMBER: screenshot
    just screenshot-post {{PR_NUMBER}}

# Generated API types
ui-generate-types:
    uv run python scripts/export_openapi.py ui/openapi.json
    cd ui && npm run generate:types
