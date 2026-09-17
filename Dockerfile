# Stage 1: Build Svelte 5 SPA
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
ENV VITE_OUT_DIR=dist
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim

# Copy the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation for faster startup & set runtime data directory
ENV UV_COMPILE_BYTECODE=1
ENV DATA_DIR="/app/data"

# Ensure data directory exists
RUN mkdir -p /app/data

# 1. Copy ONLY dependency files to leverage Docker layer caching
COPY pyproject.toml uv.lock* ./

# 2. Sync dependencies into virtual environment (excluding dev dependencies)
RUN uv sync --no-dev --no-install-project

# 3. Copy ONLY immutable application source code and documentation
COPY README.md ./
COPY src/ ./src/

# 4. Copy built Svelte SPA from frontend-builder into static/dist
COPY --from=frontend-builder /app/frontend/dist/ ./src/skt_proxy/static/dist/

# 5. Install the project wheel into the virtual environment
RUN uv sync --no-dev

# Add virtual environment binaries to PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5000

# Start Gunicorn (single worker, multiple threads for SQLite/memory safety)
CMD ["gunicorn", "--workers=1", "--threads=4", "--bind=0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "skt_proxy.app:app"]
