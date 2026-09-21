FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY docker/sandbox_provisioner/requirements.txt /tmp/sandbox-requirements.txt
COPY scripts/source-deploy/check-runtime-lock.sh /usr/local/bin/check-runtime-lock

RUN pip install --no-cache-dir \
        --index-url https://pypi.org/simple \
        -r /tmp/sandbox-requirements.txt \
    && bash /usr/local/bin/check-runtime-lock write \
        /opt/runtime-locks/sandbox.sha256 \
        /tmp/sandbox-requirements.txt \
    && rm -f /tmp/sandbox-requirements.txt

EXPOSE 8002
