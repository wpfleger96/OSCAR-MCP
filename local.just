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
