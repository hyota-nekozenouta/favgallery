# Stage 1: Install dependencies using uv
FROM python:3.12-slim AS builder
WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy only dependency manifests first — improves layer caching
COPY pyproject.toml uv.lock ./

# Install all production packages into the system Python
# pywebview is excluded (desktop = optional extra, not available on Linux headless)
RUN uv pip install --system --no-cache \
        "fastapi>=0.136.1" \
        "gallery-dl>=1.32.1" \
        "httpx>=0.28.1" \
        "imagehash>=4.3.2" \
        "pillow>=12.2.0" \
        "uvicorn>=0.46.0"

# Stage 2: Lean production image
FROM python:3.12-slim AS runner
WORKDIR /app

# Install ffmpeg via apt — replaces the Windows-only ffmpeg/bin/ffmpeg.exe bundle
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd -r -u 1001 appuser

# Copy installed packages and binaries from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY src/ ./src/

# Persistent volume for the media library — mount at /data in Railway
RUN mkdir -p /data/library && chown -R appuser:appuser /data

USER appuser

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Point the app at the persistent volume by default
ENV ARCHIVE_LIBRARY_ROOT=/data/library

# PORT is injected by Railway at runtime; default 8000 for local docker run
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/api/library')" || exit 1

CMD sh -c "uvicorn xlikes_viewer.server:app --host 0.0.0.0 --port ${PORT:-8000}"
