# Settings
set dotenv-load := false

# Default recipe: quick quality check without tests
default: sync type-check lint-check format-check

# Setup & Dependencies
sync:
    uv sync

# Code Quality - Check variants
type-check:
    uv run mypy .

lint-check:
    uvx ruff check .

format-check:
    uvx ruff format . --check

# Code Quality - Fix variants
lint:
    uvx ruff check . --fix

format:
    uvx ruff format .

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

# Composite quality checks
check: sync type-check lint-check format-check ui-type-check ui-lint-check ui-format-check
    @echo "Quick quality checks passed"

check-all: check test
    @echo "All quality checks and tests passed"

pre-commit: sync type-check lint format ui-type-check ui-lint ui-format
    @echo "Pre-commit checks passed"

ci: sync type-check lint-check format-check ui-type-check ui-lint-check ui-format-check test
    @echo "CI checks passed"

# Testing
test:
    uv run pytest

# Generate CLI documentation
docs:
    uv run python scripts/generate_cli_docs.py

# Start the REST API server in development mode
dev-api:
    uv run snore serve --reload

# Start the Vue UI dev server
dev-ui:
    cd ui && npm run dev

# Install UI npm dependencies
ui-install:
    cd ui && npm install

# Build UI for production
ui-build:
    cd ui && npm run build
