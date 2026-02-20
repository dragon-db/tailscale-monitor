# tailscale-monitor

FastAPI-based monitor for multiple Tailscale nodes.

## Features

- Multi-node monitoring with independent check intervals
- State detection: DIRECT, SPEED RELAY, RELAY (DERP), INACTIVE, OFFLINE
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

## Cross-Verification Commands

Use these commands on the same machine as the monitor:

```bash
# 1) Raw JSON used by the app
tailscale --socket /var/run/tailscale/tailscaled.sock status --json > /tmp/ts-status.json

# 2) Show one peer block by Tailscale IP
IP="100.x.x.x"
jq --arg ip "$IP" '
  .Peer
  | to_entries[]
  | select((.value.TailscaleIPs // []) | map(split("/")[0]) | index($ip))
  | .value
' /tmp/ts-status.json

# 3) Print only routing fields used by detector
jq --arg ip "$IP" -r '
  .Peer
  | to_entries[]
  | select((.value.TailscaleIPs // []) | map(split("/")[0]) | index($ip))
  | "Online=\(.value.Online) Active=\(.value.Active) CurAddr=\(.value.CurAddr) Relay=\(.value.Relay) PeerRelay=\(.value.PeerRelay) LastSeen=\(.value.LastSeen)"
' /tmp/ts-status.json

# 4) Inspect latest app decisions stored in SQLite
sqlite3 data/monitor.db "
select checked_at,state,approach2_state,ping_state,derp_region,cur_addr_endpoint,peer_relay_endpoint,relay_hint
from checks
where node_ip='$IP'
order by checked_at desc
limit 10;
"

# 5) Explicit DERP confirmation probe for one peer
tailscale --socket /var/run/tailscale/tailscaled.sock ping -c 5 "$IP"
```

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

Dashboard: `http://localhost:8080`
Docs: `http://localhost:8080/docs`

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
