# tailscale-monitor

FastAPI-based monitor for multiple Tailscale nodes.

## Features

- Multi-node monitoring with independent check intervals
- State detection: DIRECT, PEER_RELAY (shown as SPEED RELAY in UI), DERP, INACTIVE, OFFLINE
- SQLite persistence for checks and transitions
- Discord + Ntfy notifications on state transitions
- Web dashboard + JSON API
- Manual out-of-schedule check triggers
- Manual per-node ping test (`POST /api/ping/{ip}` with 5 packets) and UI action
- Discord test trigger from UI (`Test Discord`) and API (`POST /api/test/discord`)

## Requirements

- Python 3.12+
- Host with `tailscale` CLI installed
- Access to tailscaled socket (default: `/var/run/tailscale/tailscaled.sock`)

## Runtime Scope (V1)

- Local/venv runtime only (no Docker support in V1).

## Security Notice (No Built-In Auth)

- V1 has no built-in authentication or authorization.
- Dashboard and API endpoints are unauthenticated by default.
- The app binds on `0.0.0.0:{web_ui_port}` (default `8080`), so do not expose it directly to the public internet.
- If access is needed outside a private/trusted network, place it behind an auth-enabled reverse proxy or gateway and apply firewall/IP allow-list rules.

## Connection Classification Logic

The monitor treats `tailscale status --json` as the primary source of truth for
per-peer routing state.

Decision order:

1. peer missing from JSON or `Online == false` -> `OFFLINE`
2. `Online == true` and `Active == false` -> `INACTIVE`
3. for active peers:
   - `CurAddr != ""` -> `DIRECT`
   - else `PeerRelay != ""` -> `PEER_RELAY` (shown in UI as **SPEED RELAY**)
   - else -> DERP-suspect and confirm with `tailscale ping` (`via DERP (...)` -> `DERP`)

Important:
- `Relay` alone is treated as a DERP hint/suspect and is validated with ping when possible.
- `/metrics` (`http://100.100.100.100/metrics`) is backseated and does not drive
  node routing classification.
- Optional stale fallback: if peer is stale for >= 10 minutes and inactive
  (`Active=false`) while not explicitly `Online=true`, state can be treated as `OFFLINE`.

## Configuration

- Main config: `config.yaml` (copied from `config.yaml.example`)
- Secrets only: `.env` (copied from `.env.example`)
- Required: add at least one node in `config.yaml` under `nodes:`
- Key defaults:
  - `check_interval_seconds: 300`
  - `ping_on_derp_suspect: true`
  - `ping_count: 3`
  - `ping_timeout_seconds: 15`
  - `offline_threshold_minutes: 5`
  - `web_ui_port: 8080`
  - `tailscale_socket: /var/run/tailscale/tailscaled.sock`

## Quick Start (One-Stop Script)

Use root `run.sh` for all lifecycle actions.

1. Start app (auto-creates/reuses venv, installs deps, and starts server):
   - `bash run.sh start`
2. Stop app:
   - `bash run.sh stop`
3. Restart app:
   - `bash run.sh restart`
4. Check status:
   - `bash run.sh status`
5. Tail logs:
   - `bash run.sh logs`

Script behavior:
- If `.venv` does not exist, it is created automatically.
- If `requirements.txt` changed, dependencies are reinstalled.
- If `config.yaml` or `.env` is missing, they are created from example files.
- On each `start`/`restart`, `.run/tailscale-monitor.log` is truncated before launching.
- App runs in background with:
  - PID: `.run/tailscale-monitor.pid`
  - Logs: `.run/tailscale-monitor.log`

Dashboard: `http://localhost:{web_ui_port}` (default `http://localhost:8080`)
API Docs: `http://localhost:{web_ui_port}/docs` (default `http://localhost:8080/docs`)

Notifier startup log:
- On boot, the app logs configured channels with:
  - `Notifier channels configured: discord=enabled|disabled ntfy=enabled|disabled`

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
   - Node with `Online=false` or missing peer resolves `OFFLINE`.
   - Node with `Online=true` and `Active=false` resolves `INACTIVE`.
   - Active node with `CurAddr` resolves `DIRECT`.
   - Active node with empty `CurAddr` and populated `PeerRelay` resolves `PEER_RELAY` (SPEED RELAY).
   - Active node with empty `CurAddr` and empty `PeerRelay` is DERP-suspect and is confirmed via ping output.
   - `via DERP (...)` in ping output confirms `DERP` (shown as RELAY (DERP)).
   - Missing/offline peer resolves `OFFLINE`.
   - Invalid status output resolves `UNKNOWN` with low confidence.

3. Transition behavior
   - `DIRECT -> DERP`, `DERP -> DIRECT`, `OFFLINE -> DIRECT`, `DIRECT -> OFFLINE`
     create transition rows and trigger notifications.
   - `INACTIVE -> OFFLINE` triggers notification; transitions into/out of `INACTIVE` otherwise stay non-notifying.
   - Cooldown suppresses duplicate transition notifications.

4. Trigger endpoint
   - `POST /api/check/{ip}` returns `202` and triggers immediate check.
   - Repeat while in-flight returns dedupe response.

5. UI sanity
   - `/` loads cards and event log.
   - Auto-refresh updates every 30 seconds.
   - `Check Now` triggers node check without page reload.
   - `Ping Test (5)` shows per-pong route/latency summary and raw output panel.
