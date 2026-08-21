# Stage 1: Build frontend
FROM node:24-slim AS frontend-builder

ARG COMMIT_HASH=unknown

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci

COPY frontend/ ./
RUN VITE_COMMIT_HASH=${COMMIT_HASH} npm run build


# Stage 2: Python runtime
FROM python:3.14-slim

ARG COMMIT_HASH=unknown

WORKDIR /app

ENV COMMIT_HASH=${COMMIT_HASH}

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev/test deps)
RUN uv sync --frozen --no-dev

# Copy application code (remoteterm/ is the import surface for DB-stored bots)
COPY app/ ./app/
COPY remoteterm/ ./remoteterm/

# Copy license attributions
COPY LICENSES.md ./

# Copy built frontend from first stage
COPY --from=frontend-builder /build/dist ./frontend/dist

# Create data directory for SQLite database
RUN mkdir -p /app/data

RUN apt-get update && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh ./
RUN chmod +x run.sh

EXPOSE 8000

# Run the application (we retain root for max compatibility)
CMD ["./run.sh"]
