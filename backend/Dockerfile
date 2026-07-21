ARG BASE_IMAGE=python:3.12-slim-bookworm
ARG RUNTIME_IMAGE=python:3.12-slim-bookworm

# ---- Stage 1: builder ----
# Installs build deps, creates a venv at /opt/venv with all requirements.
# Heavy layer (build-essential, dev headers) stays here and is discarded.
FROM ${BASE_IMAGE} AS builder

ARG BUILD_REGION=cn

WORKDIR /build

RUN set -eux; \
    APT_TIMEOUT='Acquire::http::Timeout=35'; \
    if [ "$BUILD_REGION" = "cn" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
      sed -i -E \
        's|https?://deb.debian.org|https://mirrors.aliyun.com|g; s|https?://security.debian.org|https://mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources; \
      apt-get -o "$APT_TIMEOUT" -o Acquire::https::Timeout=35 update \
        || { cp /tmp/debian.sources.bak /etc/apt/sources.list.d/debian.sources; apt-get -o "$APT_TIMEOUT" update; }; \
    else \
      apt-get -o "$APT_TIMEOUT" update; \
    fi; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      build-essential \
      python3-dev \
      libffi-dev \
      libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

RUN set -eux; \
    if [ "$BUILD_REGION" = "cn" ]; then \
      pip install --no-cache-dir --prefer-binary --timeout 180 \
        -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt \
      || pip install --no-cache-dir --prefer-binary --timeout 180 -r requirements.txt; \
    else \
      pip install --no-cache-dir --prefer-binary --timeout 180 -r requirements.txt; \
    fi

# ---- Stage 2: runtime ----
# Slim image with only runtime deps + venv. Runs as non-root user `app`.
FROM ${RUNTIME_IMAGE}

ARG RUNTIME_REGION=cn

WORKDIR /app

RUN set -eux; \
    APT_TIMEOUT='Acquire::http::Timeout=35'; \
    if [ "$RUNTIME_REGION" = "cn" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
      sed -i -E \
        's|https?://deb.debian.org|https://mirrors.aliyun.com|g; s|https?://security.debian.org|https://mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources; \
      apt-get -o "$APT_TIMEOUT" -o Acquire::https::Timeout=35 update \
        || { cp /tmp/debian.sources.bak /etc/apt/sources.list.d/debian.sources; apt-get -o "$APT_TIMEOUT" update; }; \
    else \
      apt-get -o "$APT_TIMEOUT" update; \
    fi; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with a stable UID/GID (1000:1000).
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app \
       --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app \
    && chown -R app:app /app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Copy app source
COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
