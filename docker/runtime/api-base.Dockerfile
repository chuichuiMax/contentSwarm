ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.7.2
ARG NODE_IMAGE=node:24-slim

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node
FROM ${PYTHON_IMAGE} AS system

COPY --from=uv /uv /uvx /bin/
COPY --from=node /usr/local/bin /usr/local/bin
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node /usr/local/include /usr/local/include
COPY --from=node /usr/local/share /usr/local/share

WORKDIR /app

ENV TZ=Asia/Shanghai \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app:/app/package

RUN npm config set registry https://registry.npmmirror.com --global \
    && npm cache clean --force \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && apt-get update \
    && apt-get install -y --no-install-recommends --fix-missing \
        curl \
        ffmpeg \
        git \
        libpq5 \
        fonts-noto-cjk \
        libsm6 \
        libxext6 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

FROM system AS deps

WORKDIR /build/backend

COPY backend/pyproject.toml backend/.python-version backend/uv.lock ./
COPY backend/package/pyproject.toml backend/package/uv.lock ./package/
COPY backend/package ./package
COPY scripts/source-deploy/check-runtime-lock.sh /usr/local/bin/check-runtime-lock

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --group test --no-dev --frozen --no-install-project --no-install-package yuxi \
    && bash /usr/local/bin/check-runtime-lock write /opt/runtime-locks/api.sha256 \
        /build/backend/uv.lock \
        /build/backend/package/uv.lock

FROM system AS runtime

COPY --from=deps /usr/local /usr/local
COPY --from=deps /opt/runtime-locks/api.sha256 /opt/runtime-locks/api.sha256

RUN /usr/local/bin/patchright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PATH=/usr/local/bin:$PATH
