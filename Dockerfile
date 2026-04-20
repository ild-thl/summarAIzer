# Multi-stage production Dockerfile for SummarAIzer v2
# Stage 1: Builder - Install dependencies and create virtual environment
# Stage 2: Runtime - Lean production image with only runtime essentials

# ============================================================================
# Stage 1: Builder
# ============================================================================
FROM python:3.11 AS builder

# Set working directory
WORKDIR /app

# Set Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INPUT=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python -m venv /app/.venv

# Upgrade pip and install build tools
RUN . /app/.venv/bin/activate && \
    pip install --upgrade pip setuptools wheel

# Copy requirements and install dependencies
COPY requirements.txt /app/requirements.txt
RUN . /app/.venv/bin/activate && \
    pip install --no-cache-dir -r /app/requirements.txt

# ============================================================================
# Stage 2: Runtime
# ============================================================================
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Install only runtime dependencies (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    ffmpeg \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 app && \
    chown -R app:app /app

# Switch to non-root user
USER app

# Health check - verify API is responding
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:7860/health || exit 1

# Expose application port
EXPOSE 7860

# Production: Run without reload, with configurable worker count
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 7860 --workers ${UVICORN_WORKERS:-1}"]
