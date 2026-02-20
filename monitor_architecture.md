## Project Overview

Build a Python 3.11+ application called `tailscale-monitor`. It monitors multiple Tailscale nodes for connection type changes (Direct, Peer Relay, DERP) and offline state, sends rich notifications, and serves a simple web dashboard. It runs as a Docker container using `network_mode: host` with the host's tailscale socket bind-mounted.

### Implementation Update (2026-02-20)

The active implementation now uses this routing decision order from
`tailscale --socket ... status --json`:

1. peer missing or `Online == false` -> `OFFLINE`
2. `Online == true` and `Active == false` -> `INACTIVE`
3. active peers:
   - `CurAddr != ""` -> `DIRECT`
   - else `PeerRelay != ""` -> `PEER_RELAY` (UI label: **SPEED RELAY**)
   - else DERP-suspect; confirm using `tailscale ping`
4. ping contains `via DERP (...)` -> `DERP` (UI label: **RELAY (DERP)**)

Metrics endpoint (`http://100.100.100.100/metrics`) is intentionally backseated
for route classification because it is host-level aggregate telemetry, not
authoritative per-peer path state.

---

### Section 1 — Project Structure & Configuration

```
Create a Python project with this file layout:

tailscale-monitor/
├── app/
│   ├── __init__.py
│   ├── main.py            # Entry point, main loop
│   ├── config.py          # Config loading from .env / config.yaml
│   ├── monitor.py         # Per-node monitoring logic
│   ├── detectors/
│   │   ├── metrics.py     # Approach 1: parse 100.100.100.100/metrics
│   │   ├── status.py      # Approach 2: parse tailscale status --json
│   │   └── ping.py        # Approach 3: tailscale ping (conditional)
│   ├── notifiers/
│   │   ├── discord.py     # Discord webhook sender
│   │   └── ntfy.py        # Ntfy sender
│   ├── storage.py         # SQLite read/write
│   ├── api.py             # Flask/FastAPI web server for the UI
│   └── web/
│       ├── index.html     # Single-page dashboard
│       ├── style.css      # (empty if using Tailwind CDN)
│       └── app.js         # Fetches /api/nodes, /api/history, etc.
├── data/                  # Mounted volume — SQLite lives here
├── .env.example
├── config.yaml.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt

CONFIGURATION SYSTEM:

Use a config.yaml file (not just .env) for node definitions, because multiple
nodes need structured config that doesn't fit cleanly in env vars. Load
sensitive values (webhook URLs, tokens) from .env.

config.yaml structure:
  settings:
    check_interval_seconds: 300
    ping_on_derp_suspect: true
    ping_count: 3
    notification_cooldown_seconds: 0
    data_retention_days: 30
    log_level: INFO
    web_ui_port: 8080

  nodes:
    - ip: "100.x.x.x"
      label: "my-vps-1"
      tags: ["production", "vps"]
      check_interval_seconds: 60   # optional per-node override
    - ip: "100.x.x.x"
      label: "home-server"
      tags: ["homelab"]

.env file (secrets only):
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  NTFY_URL=https://ntfy.sh
  NTFY_TOPIC=tailscale-alerts
  NTFY_TOKEN=tk_xxxx   # optional

The app should merge config.yaml settings with .env secrets at startup.
Validate on startup: warn if no nodes configured, warn if no notifier configured.
```

---

### Section 2 — Multi-Node Monitor Engine

```
Implement the core monitoring loop with these requirements:

MULTI-NODE ARCHITECTURE:

- On startup, create one monitor context per node defined in config.yaml.
- Use threading or asyncio to run all node checks concurrently.
  Each node has its own independent check interval (defaults to global setting).
- Each node maintains its own state independently in memory and in SQLite.

CURRENT IMPLEMENTATION OVERRIDE (V1):

- Per-peer route classification is driven by `tailscale status --json` fields.
- `/metrics` is not used to decide a node's DIRECT/DERP/PEER_RELAY state.
- Ping is used as optional DERP confirmation only.
- Effective path logic in production:
  - peer missing or `Online == false` => `OFFLINE`
  - `Online == true` and `Active == false` => `INACTIVE`
  - active peer with `CurAddr != ""` => `DIRECT`
  - active peer with empty `CurAddr` and `PeerRelay != ""` => `PEER_RELAY` (SPEED RELAY)
  - active peer with both empty => DERP suspected; confirm with ping
  - ping output `via DERP (...)` => `DERP` (RELAY (DERP))
- `Relay` by itself is treated as a hint (home DERP/signaling), not active-path proof.
- If this override conflicts with older details below, this override wins.

PER-NODE CHECK SEQUENCE (run every check_interval_seconds):

STEP 1 — Fetch tailscale status (Approach 2, always runs):
  Run: tailscale status --json
  (Use subprocess with the host socket: tailscale --socket /var/run/tailscale/tailscaled.sock status --json)
  Parse the Peers section to find the peer matching this node's IP.
  Extract: online (bool), CurAddr, Relay, PeerRelay fields.
  
  Determine raw status:
  - Peer not found in output at all → UNKNOWN (daemon issue)
  - online=false OR LastSeen > 5 minutes ago → OFFLINE
  - CurAddr has IP:port, no Relay → DIRECT
  - PeerRelay populated → PEER_RELAY
  - Relay populated (DERP region code) → DERP

STEP 2 — Fetch local metrics (Approach 1, runs alongside Step 1):
  GET http://100.100.100.100/metrics
  Parse tailscaled_outbound_bytes_total for path labels:
    direct_ipv4 / direct_ipv6 → DIRECT traffic
    peer_relay_ipv4 / peer_relay_ipv6 → PEER_RELAY traffic
    derp → DERP traffic
  Calculate delta from previous poll values.
  Store raw counter values for next comparison.
  Handle counter resets gracefully (new value < old = treat as reset, use new value).
  
  Note: Metrics are LOCAL to the machine running the monitor — they show the
  monitor machine's own traffic paths, not the target node's. Use them as a
  supporting signal, not the primary truth for per-node routing.

STEP 3 — Determine final state:
  Primary truth: tailscale status --json (Step 1) per node.
  Supporting signal: metrics delta (Step 2) for overall traffic.
  
  Confidence:
  - HIGH: Status shows DIRECT + metrics show direct_bytes_delta > 0
  - HIGH: Status shows DERP + metrics show derp_bytes_delta > 0  
  - MEDIUM: Status and metrics disagree → trust status, note discrepancy
  - MEDIUM: Status says DIRECT/PEER_RELAY but metrics show 0 traffic (idle node)
  - LOW: Status JSON failed to parse

STEP 4 — Ping confirmation (conditional):
  Only run IF determined type is DERP AND ping_on_derp_suspect=true AND node is ONLINE.
  Run: tailscale ping --socket /var/run/tailscale/tailscaled.sock -c {ping_count} {node_ip}
  Total timeout: 15 seconds max.
  Parse output to extract: connection type per pong, latency per pong, DERP region.
  Summarize: dominant type, avg/min/max latency, packet loss.
  If ping fails entirely → log warning, mark ping_result as "failed".

OFFLINE DETECTION (important):
  An OFFLINE state must be triggered when:
  - The peer's "Online" field in status JSON is false, OR
  - The peer disappears from status JSON entirely (recently removed from tailnet), OR
  - LastSeen field is > offline_threshold_minutes (default: 5) minutes ago.
  
  For offline nodes, skip Steps 3 and 4 entirely.
  
  ONLINE RECOVERY: When a node comes back online from OFFLINE, always send a
  notification regardless of what connection type it recovers on.

STATE TRANSITION LOGIC:
  Keep previous_state per node in memory (also loaded from DB on startup).
  Notify when:
  - OFFLINE → any (node came back online) — HIGH priority
  - any → OFFLINE (node went offline) — HIGH priority
  - DIRECT ↔ DERP — HIGH priority
  - DIRECT ↔ PEER_RELAY — MEDIUM priority
  - PEER_RELAY ↔ DERP — MEDIUM priority
  - Same type, different DERP region — LOW priority
  - No change → no notification (just log)
  
  Apply notification_cooldown_seconds: if the same transition type
  (e.g., DIRECT→DERP) was notified within the cooldown window for this node,
  suppress the duplicate notification but still log it.
```

---

### Section 3 — Notifications

```
Build a notification engine with these two channels:

DISCORD NOTIFIER:

Send to DISCORD_WEBHOOK_URL using a JSON embed payload.

Embed color scheme:
  OFFLINE → Red (#FF0000) / 16711680
  DIRECT → Green (#00C853) / 50259
  PEER_RELAY -> Yellow (#FFD600) / 16766464
  DERP -> Orange (#FF6D00) / 16740608
  INACTIVE -> Blue (#5865F2) / 5793266
  ONLINE RECOVERY → Bright Green (#76FF03) / 7798531

Embed structure:
  title: Choose based on transition:
    - "🔴 Node Offline: {label}"
    - "🟢 Node Back Online: {label}"
    - "🔄 Connection Changed: {label}"
    - "⚠️ DERP Fallback: {label}"
  
  description: One-line human summary of what changed.
  
  fields (always include):
    - Node: {label} ({ip})
    - Previous State: {type} for {human_duration}  [e.g. "2h 14m"]
    - Current State: {type} ({confidence} confidence)
    - Detection: Status JSON: {type} | Metrics: {dominant_path} | Ping: {type or "N/A"}
  
  fields (conditional):
    - Latency (if ping ran): Min {x}ms / Avg {x}ms / Max {x}ms
    - Packet Loss (if ping ran): {x}%
    - DERP Region (if DERP): {region_code} → {region_name}
    - Traffic Delta: Direct {x} bytes | Relay {x} bytes | DERP {x} bytes
  
  footer: "tailscale-monitor • checked {timestamp}"
  timestamp: ISO8601

Send as HTTP POST with Content-Type: application/json.
On HTTP error: log and do not retry (avoid notification storms).

NTFY NOTIFIER:

POST to {NTFY_URL}/{NTFY_TOPIC}

Headers:
  Title: "TS: {label} → {current_state}"
  Priority: urgent (offline), high (DERP), default (peer_relay), low (direct recovery)
  Tags: tailscale, {current_state_lowercase}, {label}
  Authorization: Bearer {NTFY_TOKEN}  (only if token is set)

Body: Plain text multi-line:
  {summary line}
  
  Node: {label} ({ip})
  Previous: {type} for {duration}
  Current: {type} ({confidence})
  
  Detection:
  - Status: {type}
  - Metrics: {dominant_path}
  - Ping: {type or Not run}
  
  [Latency/DERP region/Traffic sections if applicable]

GENERAL NOTIFIER RULES:
  - If both Discord and Ntfy are configured, send to both.
  - If neither is configured, log a warning at startup but continue running.
  - All notifier calls should be non-blocking (use threading or asyncio).
  - Log success/failure for each notification attempt.
```

---

### Section 4 — Storage (SQLite)

```
Use SQLite with WAL mode for all persistence. Database file: data/monitor.db

Tables:

1. nodes
   Stores known monitored nodes (upserted from config on startup).
   Fields: ip (PK), label, tags (JSON), added_at, last_seen_at

2. checks
   One row per monitoring check per node.
   Fields: id, node_ip, checked_at, state (DIRECT/PEER_RELAY/DERP/OFFLINE/UNKNOWN),
           confidence (high/medium/low),
           approach1_state (from metrics), approach2_state (from status JSON),
           ping_state, ping_min_ms, ping_avg_ms, ping_max_ms, ping_packet_loss_pct,
           derp_region, peer_relay_endpoint,
           bytes_direct_delta, bytes_relay_delta, bytes_derp_delta,
           raw_status_json (TEXT, for debugging)

3. transitions
   One row per state change detected per node.
   Fields: id, node_ip, transitioned_at, previous_state, current_state,
           duration_previous_seconds, notified (bool), notification_channels (JSON),
           transition_reason (text description)

STARTUP BEHAVIOR:
  - Create tables if not exist.
  - Load last known state for each configured node from the transitions table
    (most recent row per node_ip) to restore in-memory state after restart.
  - This prevents false "node came back online" notifications after a monitor restart.

DATA RETENTION:
  - On each startup (or daily via a background thread), delete checks rows
    older than data_retention_days.
  - Never delete transitions rows (they are compact and historically useful).

QUERY HELPERS (used by the web API):
  - get_current_state_all_nodes() → latest state per node
  - get_node_history(ip, limit=100) → recent checks for a node
  - get_recent_transitions(limit=50) → latest transitions across all nodes
  - get_uptime_stats(ip, days=7) → % time in each state over N days
```

---

### Section 5 — Web UI & API

```
Serve a simple web dashboard using Flask (or FastAPI).
Bind to 0.0.0.0:{web_ui_port} (default 8080).

API ENDPOINTS:

GET /api/nodes
  Returns current state for all configured nodes.
  Response: list of {ip, label, tags, current_state, confidence, last_checked,
                     derp_region, cur_addr_endpoint, peer_relay_endpoint,
                     relay_hint, ping_avg_ms, uptime_7d_pct}

GET /api/nodes/{ip}/history?limit=100
  Returns recent check history for one node.

GET /api/transitions?limit=50
  Returns recent state transitions across all nodes (the "event log").

GET /api/stats
  Returns summary: total nodes, nodes online, nodes inactive, nodes offline,
                   nodes on DERP, nodes on direct, last_check_time.

POST /api/check/{ip}  (or /api/check/all)
  Triggers an immediate out-of-schedule check for a node (or all nodes).
  Returns 202 Accepted. The check runs async.

POST /api/ping/{ip}
  Runs `tailscale ping -c 5` for one configured node and returns parsed
  route/latency/packet-loss details plus raw output.

GET /health
  Returns 200 OK with {"status": "ok"} — for Docker healthcheck.

SERVE STATIC FILES:
  Serve app/web/ directory at /
  All frontend is a single index.html with vanilla JS making fetch() calls
  to the above API endpoints.

WEB UI DESIGN REQUIREMENTS:

Use Tailwind CSS via CDN (no build step needed).
The UI must be clean, responsive, and work well on mobile too.

LAYOUT:
  Header bar: App name "Tailscale Monitor", last refresh time, manual refresh button.
  
  Summary strip (top): 5 stat cards side by side:
    - Total Nodes
    - Online (green)
    - Inactive (blue)
    - Offline (red)
    - On DERP (orange)
  
  Node Cards grid (main area):
    One card per node. Each card shows:
    - Node label (large) and IP (small, muted)
    - Status badge (colored pill): ONLINE/OFFLINE + connection type
      Color: green=DIRECT, yellow=PEER_RELAY, orange=DERP, red=OFFLINE
    - Last checked time (relative: "2 min ago")
    - Confidence level (small text)
    - DERP region if applicable (e.g., "via nyc")
    - Ping avg latency if available (e.g., "~12ms")
    - Uptime bar: small 7-day uptime % with colored bar
    - Tags as small pills
    - "Check Now" button (calls POST /api/check/{ip})
  
  Event Log table (below cards):
    Columns: Time | Node | Change | Duration in previous state
    Show last 20 transitions, with color-coded Change column.
    New events highlighted briefly on arrival.
  
  Auto-refresh every 30 seconds using setInterval + fetch().
  Show a subtle loading indicator when refreshing.
  No page reload needed — update DOM in place.

VISUAL STATE REFERENCE:
  DIRECT → green text + green dot
  PEER_RELAY -> Yellow (#FFD600) / 16766464
  DERP -> Orange (#FF6D00) / 16740608
  INACTIVE -> Blue (#5865F2) / 5793266
  OFFLINE → red text + red dot + pulsing animation
  UNKNOWN → gray text + gray dot
```

---

### Section 6 — Docker Deployment [NOT REQUIRED FOR V1] [GO WITH PYTHON VENV FOR V1]

```
Create Docker deployment files:

DOCKERFILE:
  Base image: python:3.11-slim < (USE PYTHON 3.12.X)
  
  Install system dependencies:
    - curl (for health check)
    - tailscale (official install script, or copy binary from host via volume)
  
  IMPORTANT: The container does NOT run tailscaled itself.
  It only needs the `tailscale` CLI binary to call:
    tailscale --socket /var/run/tailscale/tailscaled.sock status --json
    tailscale --socket /var/run/tailscale/tailscaled.sock ping ...
  
  Recommended approach: Install tailscale CLI only (no daemon):
    Install the tailscale package via apt, but do NOT start tailscaled.
    Only the CLI binary is used to communicate with the host's tailscaled socket.
  
  Copy app code, install requirements.txt via pip.
  Set working directory to /app.
  Expose port 8080.
  CMD: python -m app.main

DOCKER-COMPOSE.YML:
  Service: tailscale-monitor
  
  Build: from local Dockerfile.
  
  network_mode: host
  (Required! The 100.100.100.100/metrics endpoint is only reachable from host network.)
  
  volumes:
    - ./data:/app/data              # SQLite persistence
    - ./config.yaml:/app/config.yaml:ro  # Node configuration
    - ./.env:/app/.env:ro          # Secrets
    - /var/run/tailscale/tailscaled.sock:/var/run/tailscale/tailscaled.sock:ro
      # Bind-mount host tailscale socket (read-only is fine for status/ping)
  
  restart: unless-stopped
  
  healthcheck:
    test: curl -f http://localhost:8080/health || exit 1
    interval: 30s
    timeout: 5s
    retries: 3
  
  environment:
    - TZ=UTC  # or user's timezone

NOTES FOR THE CODING AGENT:
  - The tailscale socket must be bind-mounted. Without it, `tailscale status`
    will fail or try to connect to a non-existent local daemon.
  - network_mode: host is NOT compatible with named ports in docker-compose
    (ports: directive is ignored). The web UI is accessible at host:8080 directly.
  - On the host, tailscaled must be running and the monitor machine must be
    a member of the same tailnet as the nodes being monitored.
  - Warn the user in startup logs if the socket file is not accessible.

REQUIREMENTS.TXT (minimum):
  requests
  pyyaml
  python-dotenv
  flask  (or fastapi + uvicorn)
  schedule  (for the polling loop, or use threading.Timer)
```

---


