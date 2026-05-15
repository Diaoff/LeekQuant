ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG BUILD_REGION=cn

WORKDIR /app

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
    apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN set -eux; \
    if [ "$BUILD_REGION" = "cn" ]; then \
      pip install --no-cache-dir --prefer-binary --timeout 180 \
        -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt \
      || pip install --no-cache-dir --prefer-binary --timeout 180 -r requirements.txt; \
    else \
      pip install --no-cache-dir --prefer-binary --timeout 180 -r requirements.txt; \
    fi

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

