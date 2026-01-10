# Stage 1: Build frontend
FROM node:20-slim AS frontend-builder

WORKDIR /build

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# Stage 2: Python runtime
FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev/test deps)
RUN uv sync --frozen --no-dev

# Copy application code
COPY app/ ./app/

# Copy built frontend from first stage
COPY --from=frontend-builder /build/dist ./frontend/dist

# Create data directory for SQLite database
RUN mkdir -p /app/data

EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
