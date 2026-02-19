#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
RUN_DIR="$PROJECT_ROOT/.run"
PID_FILE="$RUN_DIR/tailscale-monitor.pid"
LOG_FILE="$RUN_DIR/tailscale-monitor.log"
REQ_FILE="$PROJECT_ROOT/requirements.txt"
REQ_HASH_FILE="$VENV_DIR/.requirements.sha256"
VENV_PYTHON=""

mkdir -p "$RUN_DIR"

log() {
  printf '[tailscale-monitor] %s\n' "$*"
}

find_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    echo "python3.12"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  log "ERROR: Python interpreter not found. Install Python 3.12+."
  exit 1
}

is_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    rm -f "$PID_FILE"
  fi
  return 1
}

requirements_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$REQ_FILE" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ_FILE" | awk '{print $1}'
    return
  fi

  "$PYTHON_BIN" - <<'PY'
import hashlib
from pathlib import Path
payload = Path("requirements.txt").read_bytes()
print(hashlib.sha256(payload).hexdigest())
PY
}

resolve_venv_paths() {
  local py_unix="$VENV_DIR/bin/python"
  local py_win="$VENV_DIR/Scripts/python.exe"
  local act_unix="$VENV_DIR/bin/activate"
  local act_win="$VENV_DIR/Scripts/activate"

  if [[ -x "$py_unix" && -f "$act_unix" ]]; then
    VENV_PYTHON="$py_unix"
    # shellcheck disable=SC1091
    source "$act_unix"
    return 0
  fi

  if [[ -x "$py_win" && -f "$act_win" ]]; then
    VENV_PYTHON="$py_win"
    # shellcheck disable=SC1091
    source "$act_win"
    return 0
  fi

  return 1
}

create_venv() {
  log "Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
}

ensure_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    create_venv
  fi

  if ! resolve_venv_paths; then
    log "Detected invalid or partial venv at $VENV_DIR; recreating it"
    rm -rf "$VENV_DIR"
    create_venv
    if ! resolve_venv_paths; then
      log "ERROR: Unable to activate venv after recreation."
      exit 1
    fi
  fi
}

install_deps_if_needed() {
  if [[ ! -f "$REQ_FILE" ]]; then
    log "ERROR: requirements.txt not found at $REQ_FILE"
    exit 1
  fi

  local current_hash existing_hash
  current_hash="$(requirements_hash)"
  existing_hash="$(cat "$REQ_HASH_FILE" 2>/dev/null || true)"

  if [[ "$current_hash" != "$existing_hash" ]]; then
    log "Installing dependencies from requirements.txt"
    "$VENV_PYTHON" -m pip install --upgrade pip
    "$VENV_PYTHON" -m pip install -r "$REQ_FILE"
    printf '%s\n' "$current_hash" > "$REQ_HASH_FILE"
  else
    log "Dependencies are up to date"
  fi
}

ensure_defaults() {
  if [[ ! -f "$PROJECT_ROOT/config.yaml" && -f "$PROJECT_ROOT/config.yaml.example" ]]; then
    cp "$PROJECT_ROOT/config.yaml.example" "$PROJECT_ROOT/config.yaml"
    log "Created config.yaml from config.yaml.example"
  fi
  if [[ ! -f "$PROJECT_ROOT/.env" && -f "$PROJECT_ROOT/.env.example" ]]; then
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    log "Created .env from .env.example"
  fi
}

start_app() {
  if is_running; then
    log "Already running (pid $(cat "$PID_FILE"))"
    exit 0
  fi

  cd "$PROJECT_ROOT"
  ensure_venv
  install_deps_if_needed
  ensure_defaults

  log "Starting application"
  nohup "$VENV_PYTHON" -m app.main >> "$LOG_FILE" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" > "$PID_FILE"
  sleep 1

  if kill -0 "$pid" >/dev/null 2>&1; then
    log "Started (pid $pid). Logs: $LOG_FILE"
  else
    log "ERROR: Failed to start. Check logs: $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi
}

stop_app() {
  if ! is_running; then
    log "Not running"
    exit 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  log "Stopping process $pid"
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      log "Stopped"
      exit 0
    fi
    sleep 0.5
  done

  log "Force killing process $pid"
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
  log "Stopped"
}

status_app() {
  if is_running; then
    log "Running (pid $(cat "$PID_FILE"))"
  else
    log "Stopped"
  fi
}

logs_app() {
  mkdir -p "$RUN_DIR"
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

restart_app() {
  stop_app || true
  start_app
}

usage() {
  cat <<'EOF'
Usage: run.sh <command>

Commands:
  start     Create/reuse venv, install deps if needed, and start app
  stop      Stop running app
  restart   Restart app
  status    Show app status
  logs      Tail application logs
EOF
}

PYTHON_BIN="$(find_python)"

case "${1:-start}" in
  start) start_app ;;
  stop) stop_app ;;
  restart) restart_app ;;
  status) status_app ;;
  logs) logs_app ;;
  *) usage; exit 1 ;;
esac
