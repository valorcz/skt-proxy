FROM python:3.12-slim

# Copy the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1

# Copy dependency management files first to leverage Docker cache
COPY pyproject.toml uv.lock* ./

# Sync dependencies into a virtual environment (excluding dev dependencies)
RUN uv sync --no-dev --no-install-project

# Copy the rest of the application code
COPY . .

# Run sync again to install the project itself (if defined in pyproject.toml)
RUN uv sync --no-dev

# Put the virtual environment on the PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5000

# Start Gunicorn (single worker, multiple threads for SQLite/memory safety)
CMD ["gunicorn", "--workers=1", "--threads=4", "--bind=0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "app:app"]
