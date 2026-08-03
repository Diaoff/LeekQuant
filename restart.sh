#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/.logs"
PID_DIR="$ROOT_DIR/.pids"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-4}"
BACKTEST_CELERY_CONCURRENCY="${BACKTEST_CELERY_CONCURRENCY:-1}"
REALTIME_RISK_GUARD_INTERVAL="${REALTIME_RISK_GUARD_INTERVAL:-15}"

WITH_CELERY=0
SKIP_BACKEND=0
SKIP_FRONTEND=0

usage() {
  cat <<'EOF'
Usage: ./restart.sh [options]

Options:
  --with-celery      Also restart celery worker and beat.
  --skip-backend     Do not start FastAPI.
  --skip-frontend    Do not start Vite.
  -h, --help         Show this help.

Environment:
  BACKEND_HOST       Backend bind host, default 127.0.0.1.
  BACKEND_PORT       Backend port, default 8000.
  FRONTEND_HOST      Frontend bind host, default 127.0.0.1.
  FRONTEND_PORT      Frontend port, default 5173.
  CELERY_CONCURRENCY Celery worker concurrency, default 4.
  BACKTEST_CELERY_CONCURRENCY Dedicated backtest worker concurrency, default 1.
  REALTIME_RISK_GUARD_INTERVAL Realtime risk polling interval seconds, default 15.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-celery)
      WITH_CELERY=1
      ;;
    --skip-backend)
      SKIP_BACKEND=1
      ;;
    --skip-frontend)
      SKIP_FRONTEND=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

cd "$ROOT_DIR"
mkdir -p "$LOG_DIR" "$PID_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-4}"
BACKTEST_CELERY_CONCURRENCY="${BACKTEST_CELERY_CONCURRENCY:-1}"
# Prefetch must be >= concurrency, otherwise the worker pulls only one task
# at a time (worker_prefetch_multiplier is 1 globally for safety) and serializes
# all backtests regardless of BACKTEST_CELERY_CONCURRENCY. Default = concurrency + 1.
BACKTEST_PREFETCH_MULTIPLIER="${BACKTEST_PREFETCH_MULTIPLIER:-$((BACKTEST_CELERY_CONCURRENCY + 1))}"
REALTIME_RISK_GUARD_INTERVAL="${REALTIME_RISK_GUARD_INTERVAL:-15}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://$BACKEND_HOST:$BACKEND_PORT}"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
ALEMBIC_BIN="${ALEMBIC_BIN:-$ROOT_DIR/.venv/bin/alembic}"
UVICORN_BIN="${UVICORN_BIN:-$ROOT_DIR/.venv/bin/uvicorn}"
CELERY_BIN="${CELERY_BIN:-$ROOT_DIR/.venv/bin/celery}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_file() {
  if [[ ! -x "$1" ]]; then
    echo "Missing executable: $1" >&2
    exit 1
  fi
}

wait_http() {
  local name="$1"
  local url="$2"
  local log_file="$3"
  local attempts="${4:-30}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep 1
  done

  echo "$name failed to become ready: $url" >&2
  if [[ -f "$log_file" ]]; then
    echo "Last $name log lines:" >&2
    tail -80 "$log_file" >&2 || true
  fi
  exit 1
}

wait_backtest_worker() {
  local attempts="${1:-30}"

  for _ in $(seq 1 "$attempts"); do
    if (
      cd "$ROOT_DIR/backend"
      "$PYTHON_BIN" -c 'from app.api.backtests import _backtest_worker_available; raise SystemExit(0 if _backtest_worker_available() else 1)'
    ) >/dev/null 2>&1; then
      echo "Backtest worker is ready"
      return 0
    fi
    sleep 1
  done

  echo "Backtest worker failed to become ready" >&2
  if [[ -f "$LOG_DIR/celery-backtest-worker.log" ]]; then
    tail -80 "$LOG_DIR/celery-backtest-worker.log" >&2 || true
  fi
  exit 1
}

start_detached() {
  local log_file="$1"
  shift

  "$PYTHON_BIN" -c '
import subprocess
import sys

log_path = sys.argv[1]
cmd = sys.argv[2:]
log = open(log_path, "ab", buffering=0)
process = subprocess.Popen(
    cmd,
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
    close_fds=True,
    start_new_session=True,
)
print(process.pid)
' "$log_file" "$@"
}

kill_pid_file() {
  local name="$1"
  local file="$PID_DIR/$name.pid"

  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "Stopping $name pid=$pid"
      kill "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$file"
  fi
}

kill_celery_processes() {
  local name="$1"
  local role="$2"

  if ! command -v ps >/dev/null 2>&1 || ! command -v awk >/dev/null 2>&1; then
    return 0
  fi

  local app_path="app.tasks.celery_app:celery_app"
  local pids
  pids="$(ps -eo pid=,command= | awk -v app="$app_path" -v role="$role" 'index($0, app) && index($0, role) {print $1}' || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  echo "Stopping existing celery $name process(es): $pids"
  for pid in $pids; do
    kill "$pid" >/dev/null 2>&1 || true
  done

  sleep 1
  pids="$(ps -eo pid=,command= | awk -v app="$app_path" -v role="$role" 'index($0, app) && index($0, role) {print $1}' || true)"
  if [[ -n "$pids" ]]; then
    echo "Force stopping existing celery $name process(es): $pids"
    for pid in $pids; do
      kill -9 "$pid" >/dev/null 2>&1 || true
    done
  fi
}

kill_port() {
  local port="$1"

  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi

  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  echo "Stopping process(es) listening on port $port: $pids"
  for pid in $pids; do
    kill "$pid" >/dev/null 2>&1 || true
  done

  sleep 1
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Force stopping process(es) on port $port: $pids"
    for pid in $pids; do
      kill -9 "$pid" >/dev/null 2>&1 || true
    done
  fi
}

start_backend() {
  require_file "$PYTHON_BIN"
  require_file "$ALEMBIC_BIN"
  require_file "$UVICORN_BIN"

  echo "Running Alembic migrations"
  (
    cd "$ROOT_DIR/backend"
    "$ALEMBIC_BIN" upgrade head
  )

  echo "Starting backend on http://$BACKEND_HOST:$BACKEND_PORT"
  (
    cd "$ROOT_DIR/backend"
    start_detached "$LOG_DIR/backend.log" "$UVICORN_BIN" app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" \
      > "$PID_DIR/backend.pid"
  )
}

start_frontend() {
  require_file "$PYTHON_BIN"
  require_cmd npm

  echo "Starting frontend on http://$FRONTEND_HOST:$FRONTEND_PORT"
  (
    cd "$ROOT_DIR/frontend"
    export VITE_API_BASE_URL
    start_detached "$LOG_DIR/frontend.log" npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" \
      > "$PID_DIR/frontend.pid"
  )
}

start_celery() {
  require_file "$PYTHON_BIN"
  require_file "$CELERY_BIN"

  echo "Starting celery worker with concurrency=$CELERY_CONCURRENCY"
  (
    cd "$ROOT_DIR/backend"
    start_detached "$LOG_DIR/celery-worker.log" "$CELERY_BIN" -A app.tasks.celery_app:celery_app worker --loglevel=INFO --concurrency "$CELERY_CONCURRENCY" -Q default,data,factor,trading \
      > "$PID_DIR/celery-worker.pid"
  )

  echo "Starting dedicated backtest worker with concurrency=$BACKTEST_CELERY_CONCURRENCY prefetch=$BACKTEST_PREFETCH_MULTIPLIER"
  (
    cd "$ROOT_DIR/backend"
    start_detached "$LOG_DIR/celery-backtest-worker.log" "$CELERY_BIN" -A app.tasks.celery_app:celery_app worker --loglevel=INFO --concurrency "$BACKTEST_CELERY_CONCURRENCY" --prefetch-multiplier "$BACKTEST_PREFETCH_MULTIPLIER" -Q backtest --hostname "backtest@%h" \
      > "$PID_DIR/celery-backtest-worker.pid"
  )

  echo "Starting celery beat"
  (
    cd "$ROOT_DIR/backend"
    start_detached "$LOG_DIR/celery-beat.log" "$CELERY_BIN" -A app.tasks.celery_app:celery_app beat --loglevel=INFO \
      > "$PID_DIR/celery-beat.pid"
  )

  echo "Starting realtime risk guard with interval=${REALTIME_RISK_GUARD_INTERVAL}s"
  (
    cd "$ROOT_DIR/backend"
    start_detached "$LOG_DIR/realtime-risk-guard.log" "$PYTHON_BIN" -m app.realtime.risk_guard --mode snapshot --refresh-interval "$REALTIME_RISK_GUARD_INTERVAL" \
      > "$PID_DIR/realtime-risk-guard.pid"
  )
}

if [[ "$SKIP_BACKEND" -eq 0 ]]; then
  kill_pid_file backend
  kill_port "$BACKEND_PORT"
fi

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  kill_pid_file frontend
  kill_port "$FRONTEND_PORT"
fi

if [[ "$WITH_CELERY" -eq 1 ]]; then
  kill_pid_file celery-worker
  kill_pid_file celery-backtest-worker
  kill_pid_file celery-beat
  kill_pid_file realtime-risk-guard
  kill_celery_processes worker " worker"
  kill_celery_processes beat " beat"
fi

if [[ "$SKIP_BACKEND" -eq 0 ]]; then
  start_backend
fi

if [[ "$WITH_CELERY" -eq 1 ]]; then
  start_celery
  wait_backtest_worker
fi

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  start_frontend
fi

if [[ "$SKIP_BACKEND" -eq 0 ]]; then
  wait_http "Backend" "http://$BACKEND_HOST:$BACKEND_PORT/health" "$LOG_DIR/backend.log"
fi

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  wait_http "Frontend" "http://$FRONTEND_HOST:$FRONTEND_PORT/" "$LOG_DIR/frontend.log"
fi

echo
echo "Restart complete."
if [[ "$SKIP_BACKEND" -eq 0 ]]; then
  echo "Backend:  http://$BACKEND_HOST:$BACKEND_PORT"
fi
if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  echo "Frontend: http://$FRONTEND_HOST:$FRONTEND_PORT"
fi
echo "Logs:     $LOG_DIR"
