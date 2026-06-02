# Production Deployment Notes

This project root `docker-compose.yml` is a local development compose file. It binds PostgreSQL, Redis, and the backend to `127.0.0.1` by default, and Redis intentionally has no password in local mode. Do not expose that local Redis service to the public Internet.

## Required Production Boundaries

- Redis must not be reachable from the public Internet. Prefer no host port mapping. If Redis is shared outside the Docker network, enable authentication.
- PostgreSQL must use a strong `POSTGRES_PASSWORD` from the production environment, not `change-me`.
- Backend should listen only on the Docker network or localhost behind a reverse proxy. Public traffic should enter through the proxy.
- `BACKEND_CORS_ORIGINS` must list only the production frontend origins, for example `https://quant.example.com`.
- External access should use HTTPS/TLS at the reverse proxy.
- Keep `ENVIRONMENT=production` for backend, Celery worker, Celery beat, and `realtime_risk_guard`.

## Redis Password Guidance

Local compose currently uses:

```yaml
redis:
  command: ["redis-server", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru"]
  ports:
    - "${REDIS_PORT:-127.0.0.1:6379}:6379"
```

This is acceptable only for local development because the host binding defaults to `127.0.0.1`.

For production, use one of these models:

1. Private Docker network only: remove the Redis `ports` mapping and keep Redis reachable only by backend/Celery services.
2. Authenticated Redis: add `--requirepass`, and set every service `REDIS_URL` to include the password.

Example authenticated Redis override:

```yaml
services:
  redis:
    command:
      - redis-server
      - --maxmemory
      - 256mb
      - --maxmemory-policy
      - allkeys-lru
      - --requirepass
      - ${REDIS_PASSWORD:?set REDIS_PASSWORD}
    ports: []

  backend:
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD:?set REDIS_PASSWORD}@redis:6379/0
      BACKEND_CORS_ORIGINS: ${BACKEND_CORS_ORIGINS:?set BACKEND_CORS_ORIGINS}

  celery_worker:
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD:?set REDIS_PASSWORD}@redis:6379/0

  celery_beat:
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD:?set REDIS_PASSWORD}@redis:6379/0

  realtime_risk_guard:
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD:?set REDIS_PASSWORD}@redis:6379/0
```

## Listener And Proxy Guidance

In production, do not publish database or Redis ports:

```yaml
services:
  postgres:
    ports: []
  redis:
    ports: []
```

Expose the backend only to a reverse proxy. If the proxy runs on the same host, bind backend to localhost:

```env
BACKEND_PORT=127.0.0.1:8000
```

If the proxy is another Compose service, omit backend host port publishing and route through the Docker network.

## CORS

Set CORS to exact production origins:

```env
BACKEND_CORS_ORIGINS=https://quant.example.com
```

Do not use wildcard origins in production. Include both apex and `www` domains only when both are real browser entry points.

## Suggested Production Compose Flow

Keep local defaults in `docker-compose.yml`, and add a production override such as `docker-compose.prod.yml`.

Run config validation before deployment:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config
```

Then deploy:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
