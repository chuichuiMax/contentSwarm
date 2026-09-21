FROM node:24-bookworm

WORKDIR /work

RUN npm install --global pnpm@10.11.0 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git rsync \
    && rm -rf /var/lib/apt/lists/* \
    && npm cache clean --force

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
