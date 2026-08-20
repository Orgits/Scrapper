# Production Dockerfile for Web Scraper System
# Multi-stage build for smaller final image
# =============================================================================
# Build stage - install dependencies and Playwright
# =============================================================================
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy AS builder

WORKDIR /app

# Install system dependencies needed for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (chromium only for smaller size)
RUN python -m playwright install chromium --with-deps

# =============================================================================
# Production stage - minimal runtime image
# =============================================================================
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy AS production

WORKDIR /app

# Install only runtime dependencies (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder (site-packages and executables)
COPY --from=builder /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy Playwright browsers from builder
COPY --from=builder /ms-playwright /ms-playwright

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Create required directories with proper permissions
RUN mkdir -p \
    /app/data/json \
    /app/data/csv \
    /app/data/checkpoints \
    /app/logs \
    /app/companydata \
    && chown -R appuser:appuser /app

# Copy application code
COPY --chown=appuser:appuser . .

# Copy proxy list if it exists
COPY --chown=appuser:appuser proxyscrape_premium_http_proxies.txt* ./

# Switch to non-root user
USER appuser

# Ensure Python can find packages (installed globally in /usr/local)
ENV PYTHONPATH=/usr/local/lib/python3.10/dist-packages:$PYTHONPATH
ENV PATH=/usr/local/bin:$PATH

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5555/ || exit 1

# Default command runs the Celery worker
CMD ["celery", "-A", "workers.celery_worker", "worker", "--loglevel=info"]


# =============================================================================
# Development stage - with hot reload support
# =============================================================================
FROM production AS development

USER root

# Install development dependencies
RUN pip install --no-cache-dir watchdog

USER appuser

# Override command for development
CMD ["celery", "-A", "workers.celery_worker", "worker", "--loglevel=info", "--concurrency=2"]
