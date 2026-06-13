# FavGallery — self-host container image.
#   docker build -t favgallery .
#   docker run -p 8000:8000 -v "$(pwd)/data:/data" favgallery
# Runs zero-config (no R2, no auth) — set X cookies via the in-app ⚙ → 🔑 panel.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# gallery-dl needs ffmpeg for some X video/GIF downloads (matches railpack.json).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install third-party deps first so the layer caches across source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Copy the app and finish the install (the project itself).
COPY . .
RUN uv sync --locked --no-dev

# .venv/bin on PATH + src layout on PYTHONPATH (mirrors the Railway Procfile).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    FAVGALLERY_LIBRARY_ROOT=/data/library

# Library media + SQLite DB + cookies persist here. Mount a host dir or volume.
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "favgallery.server:app", "--host", "0.0.0.0", "--port", "8000"]
