# tailscale-monitor

FastAPI-based monitor for multiple Tailscale nodes.

## Features

- Multi-node monitoring with independent check intervals
- State detection: DIRECT, PEER_RELAY, DERP, OFFLINE, UNKNOWN
- SQLite persistence for checks and transitions
- Discord + Ntfy notifications on state transitions
- Web dashboard + JSON API
- Manual out-of-schedule check triggers

## Requirements

- Python 3.12+
- Host with `tailscale` CLI installed
- Access to tailscaled socket (default: `/var/run/tailscale/tailscaled.sock`)

## Quick Start (One-Stop Script)

Use `scripts/manage.sh` for all lifecycle actions (run with Bash).

1. Start app (auto-creates/reuses venv, installs deps, and starts server):
   - `bash scripts/manage.sh start`
2. Stop app:
   - `bash scripts/manage.sh stop`
3. Restart app:
   - `bash scripts/manage.sh restart`
4. Check status:
   - `bash scripts/manage.sh status`
5. Tail logs:
   - `bash scripts/manage.sh logs`

Script behavior:
- If `.venv` does not exist, it is created automatically.
- If `requirements.txt` changed, dependencies are reinstalled.
- If `config.yaml` or `.env` is missing, they are created from example files.
- App runs in background with:
  - PID: `.run/tailscale-monitor.pid`
  - Logs: `.run/tailscale-monitor.log`

Dashboard: `http://localhost:8080`
Docs: `http://localhost:8080/docs`

## Manual Setup (Optional)

1. Create and activate a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy config example:
   - `cp config.yaml.example config.yaml`
4. Copy env example:
   - `cp .env.example .env`
5. Edit `config.yaml` and `.env`.
6. Run:
   - `python -m app.main`

## Manual Validation Checklist

1. Startup validation
   - Empty `nodes` logs warning and API returns empty arrays.
   - No notifier configured logs warning and app still runs.

2. Status mapping
   - Online direct node resolves `DIRECT`.
   - Node with `Relay` resolves `DERP`.
   - Node with `PeerRelay` resolves `PEER_RELAY`.
   - Missing/offline peer resolves `OFFLINE`.
   - Invalid status output resolves `UNKNOWN` with low confidence.

3. Transition behavior
   - `DIRECT -> DERP`, `DERP -> DIRECT`, `OFFLINE -> DIRECT`, `DIRECT -> OFFLINE`
     create transition rows and trigger notifications.
   - Cooldown suppresses duplicate transition notifications.

4. Trigger endpoint
   - `POST /api/check/{ip}` returns `202` and triggers immediate check.
   - Repeat while in-flight returns dedupe response.

5. UI sanity
   - `/` loads cards and event log.
   - Auto-refresh updates every 30 seconds.
   - `Check Now` triggers node check without page reload.
