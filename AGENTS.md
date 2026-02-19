# AGENTS.md

This file is the persistent context handoff for coding agents working on `tailscale-monitor`.

## Must-Follow Rule

- Update this `AGENTS.md` whenever a major change is made.
- A major change includes: architecture/module changes, API changes, config contract changes, runtime/deploy workflow changes, schema changes, or notifier behavior changes.
- For each major change, append a short entry in `## Major Change Log` with date, what changed, and impacted files.

## Project Snapshot

- Stack: Python 3.12+, FastAPI, SQLite, httpx, PyYAML.
- Runtime mode (V1): local/venv (no Docker deliverables yet).
- Purpose: monitor configured Tailscale peers and detect transitions across `DIRECT`, `PEER_RELAY`, `DERP`, `OFFLINE`, `UNKNOWN`.

## Current Architecture

- API/UI:
  - `app/api.py` exposes `/api/*`, `/health`, and serves `app/web/*`.
  - FastAPI docs available at `/docs`.
- App bootstrap:
  - `app/main.py` loads config, initializes storage, notifier manager, monitor service, and scheduler.
- Monitor engine:
  - `app/monitor.py` runs status-based detection, optional ping on DERP, confidence resolution, transition logic, notification dispatch.
  - Metrics are currently backseated and do not decide per-node routing state.
- Scheduler:
  - `app/scheduler.py` runs per-node loops with interval overrides and manual trigger dedupe.
- Detectors:
  - `app/detectors/status.py` parses `tailscale status --json` with routing priority:
    `PeerRelay` -> `CurAddr` -> `DERP-suspect` when both are empty.
  - DERP is confirmed by `tailscale ping` output (`via DERP (...)`), not by `Relay` field alone.
  - `app/detectors/metrics.py` remains available but is not used for active route classification.
  - `app/detectors/ping.py` parses `tailscale ping` output.
- Commands:
  - `app/commands.py` wraps subprocess calls and uses `asyncio.to_thread` from async workflows.
- Storage:
  - `app/storage.py` uses SQLite WAL and manages `nodes`, `checks`, `transitions` + query helpers.
- Notifiers:
  - `app/notifiers/manager.py` dispatches Discord and/or Ntfy asynchronously.

## Config Contract

- File: `config.yaml` (copy from `config.yaml.example`).
- Secrets: `.env` (copy from `.env.example`).
- Important defaults:
  - `check_interval_seconds`: 300
  - `ping_on_derp_suspect`: true
  - `ping_count`: 3
  - `ping_timeout_seconds`: 15
  - `offline_threshold_minutes`: 5
  - `web_ui_port`: 8080
  - `tailscale_socket`: `/var/run/tailscale/tailscaled.sock`

## Operational Workflow

- One-stop management script: `run.sh`
  - `start`: ensure/reuse venv, install deps, start app in background
  - `stop`: stop running app process using pid file
  - `restart`, `status`, `logs`
- PID file: `.run/tailscale-monitor.pid`
- Log file: `.run/tailscale-monitor.log`

## Data Model Summary

- `nodes`: configured monitored peers
- `checks`: per-check observations (metrics fields currently persisted as zero/N/A)
- `transitions`: state changes and notification metadata

## Known Decisions (Locked)

- FastAPI only (no Flask).
- Missing peer in status is `OFFLINE`.
- No API auth in V1.
- Manual trigger dedupe policy: skip duplicate while check in-flight.
- No automated tests in V1; manual validation checklist in `README.md`.

## Major Change Log

- 2026-02-19: Initial implementation baseline documented in AGENTS context. Added one-stop runtime script (`scripts/manage.sh`) and updated run workflow docs. Impacted: `AGENTS.md`, `scripts/manage.sh`, `README.md`.
- 2026-02-19: Repository bootstrapping and first publish workflow. Added `.gitignore`, initialized git history, and pushed initial codebase to GitHub remote `dragon-db/tailscale-monitor`. Impacted: `.gitignore`, `AGENTS.md`.
- 2026-02-19: Reworked start/stop script UX and reliability. Added root `run.sh`, changed docs to root command usage, retained `scripts/manage.sh` as wrapper, and fixed venv handling to recreate invalid partial `.venv` directories (prevents missing `bin/activate` failures). Impacted: `run.sh`, `scripts/manage.sh`, `README.md`, `AGENTS.md`.
- 2026-02-19: Removed legacy wrapper script per early-stage simplification decision. `run.sh` is now the only lifecycle script. Impacted: `scripts/manage.sh` (deleted), `README.md`, `AGENTS.md`.
- 2026-02-19: Fixed runtime/API correctness issues: migrated app lifecycle to FastAPI lifespan (removed deprecated `on_event` usage), filtered `/api/nodes` `/api/stats` `/api/transitions` to configured node IPs only (prevents stale DB nodes from appearing), and relaxed status offline classification to avoid false OFFLINE when `Online=true` but `LastSeen` is stale. Added CIDR-safe peer IP matching and reduced `httpx/httpcore` log noise. Impacted: `app/api.py`, `app/main.py`, `app/storage.py`, `app/detectors/status.py`, `app/logging.py`, `AGENTS.md`.
- 2026-02-19: Major detection logic update. Routing state now prioritizes `PeerRelay`, then `CurAddr`, then `Relay` from `status --json`; metrics are backseated and no longer influence per-node route classification/confidence. Added stale+inactive offline fallback and updated notifier language/docs accordingly. Impacted: `app/detectors/status.py`, `app/monitor.py`, `app/notifiers/manager.py`, `README.md`, `AGENTS.md`.
- 2026-02-19: Refined routing semantics to treat `Relay` as non-authoritative hint. Current path logic is: `PeerRelay` => HIGH SPEED RELAY, `CurAddr` => DIRECT, both empty => DERP suspected and confirmed by `tailscale ping` (`via DERP (...)`). Updated UI labels and docs for this behavior. Impacted: `app/detectors/status.py`, `app/monitor.py`, `app/web/app.js`, `README.md`, `AGENTS.md`, `monitor_architecture.md`.
- 2026-02-19: Fixed critical runtime crash in monitor check loop (`NameError: runtime`) by restoring per-node runtime binding in `run_check`. Improved DERP confirmation behavior by parsing ping output even when command exits non-zero and mapping DERP-suspected + `direct connection not established` to DERP. Impacted: `app/monitor.py`, `app/commands.py`, `app/detectors/ping.py`, `AGENTS.md`.
