#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$ROOT_DIR/.pids"

WITH_CELERY=0

usage() {
  cat <<'EOF'
Usage: ./stop.sh [options]

Options:
  --with-celery      Also stop celery worker and beat.
  -h, --help         Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-celery)
      WITH_CELERY=1
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

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

echo "Stopping backend..."
kill_pid_file backend
kill_port "$BACKEND_PORT"

echo "Stopping frontend..."
kill_pid_file frontend
kill_port "$FRONTEND_PORT"

if [[ "$WITH_CELERY" -eq 1 ]]; then
  echo "Stopping celery..."
  kill_pid_file celery-worker
  kill_pid_file celery-backtest-worker
  kill_pid_file celery-beat
fi

echo "Done."
