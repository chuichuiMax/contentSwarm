FROM debian:bookworm-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        fonts-noto-cjk \
    && mkdir -p /app/.data/storage \
    && rm -rf /var/lib/apt/lists/*

ENV PORT=8005 \
    DB_AUTO_MIGRATE=true \
    STORAGE_DRIVER=local \
    LOCAL_STORAGE_PATH=/app/.data/storage \
    FONTS_DIR=/usr/share/fonts/opentype/noto

EXPOSE 8005
