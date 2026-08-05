# Stage 1: build the Vue SPA
FROM node:22-slim AS ui-builder

WORKDIR /app/ui

RUN corepack enable && corepack prepare pnpm@11.17.0 --activate

COPY ui/package.json ui/pnpm-lock.yaml ui/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY ui/ ./
RUN pnpm run build

# Stage 2: Python runtime
FROM python:3.13-slim AS runtime

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Install Python dependencies (without the project itself) for layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install the project (editable; snore binary at /app/.venv/bin/snore).
# README.md is required by hatchling to build the editable install.
COPY src/ ./src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Copy the built SPA.  _resolve_spa_dist() in app.py falls back to
# Path(__file__).parents[3] / "ui" / "dist", which resolves to /app/ui/dist
# for an editable install where __file__ == /app/src/snore/api/app.py.
COPY --from=ui-builder /app/ui/dist ./ui/dist/

# Non-root user with HOME=/data so all state lands under /data/.snore/.
RUN groupadd -g 1000 snore && \
    useradd -u 1000 -g snore -d /data -s /usr/sbin/nologin -M snore && \
    mkdir -p /data && \
    chown snore:snore /data

# /data persists the database, raw backups, and logs across container restarts.
VOLUME /data

EXPOSE 8000

USER snore

# Auth mode and secrets are supplied at runtime via env_file; the image bakes
# no secrets and assumes no auth mode.
CMD ["/app/.venv/bin/snore", "serve", "--host", "0.0.0.0", "--port", "8000"]
