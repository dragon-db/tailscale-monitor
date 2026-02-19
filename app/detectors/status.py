from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..commands import tailscale_status_json
from ..models import NodeState, StatusDetection


def _parse_last_seen(last_seen_raw: str | None) -> datetime | None:
    if not last_seen_raw:
        return None
    value = last_seen_raw.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _find_peer_for_ip(status_json: dict[str, Any], ip: str) -> dict[str, Any] | None:
    def _matches(candidate: Any, target: str) -> bool:
        if not isinstance(candidate, str):
            return False
        return candidate.split("/", 1)[0] == target

    peers = status_json.get("Peer")
    if not isinstance(peers, dict):
        return None

    for peer in peers.values():
        if not isinstance(peer, dict):
            continue
        ips = peer.get("TailscaleIPs") or []
        if any(_matches(candidate, ip) for candidate in ips):
            return peer
    return None


async def get_node_status(
    tailscale_binary: str,
    tailscale_socket: str,
    ip: str,
    offline_threshold_minutes: int,
) -> StatusDetection:
    payload, error = await tailscale_status_json(
        binary=tailscale_binary,
        socket_path=tailscale_socket,
        timeout_seconds=10,
    )
    if error:
        return StatusDetection(
            state=NodeState.UNKNOWN,
            online=False,
            error=error,
        )

    assert payload is not None
    peer = _find_peer_for_ip(payload, ip)
    raw_status = json.dumps(payload)

    if peer is None:
        return StatusDetection(
            state=NodeState.OFFLINE,
            online=False,
            raw_status_json=raw_status,
            error="Peer not present in tailscale status output",
        )

    online_field = peer.get("Online")
    if online_field is False:
        return StatusDetection(
            state=NodeState.OFFLINE,
            online=False,
            raw_peer=peer,
            raw_status_json=raw_status,
        )

    peer_relay = _non_empty(peer.get("PeerRelay"))
    cur_addr = _non_empty(peer.get("CurAddr"))
    relay = _non_empty(peer.get("Relay"))
    active_field = bool(peer.get("Active", False))
    last_seen = _parse_last_seen(peer.get("LastSeen"))
    stale_grace_minutes = max(10, offline_threshold_minutes)
    if last_seen is not None:
        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_grace_minutes)
        if last_seen < threshold:
            # Online=true and stale LastSeen can happen; only mark OFFLINE when also inactive.
            if online_field is not True and not active_field:
                return StatusDetection(
                    state=NodeState.OFFLINE,
                    online=False,
                    raw_peer=peer,
                    raw_status_json=raw_status,
                    error=(
                        f"Peer stale and inactive: LastSeen older than {stale_grace_minutes} minutes "
                        "with Active=false"
                    ),
                )

    if peer_relay:
        return StatusDetection(
            state=NodeState.PEER_RELAY,
            online=online_field is not False,
            peer_relay_endpoint=str(peer_relay),
            raw_peer=peer,
            raw_status_json=raw_status,
        )

    if cur_addr:
        return StatusDetection(
            state=NodeState.DIRECT,
            online=online_field is not False,
            raw_peer=peer,
            raw_status_json=raw_status,
        )

    if relay:
        # Relay alone is a DERP-suspect signal, not proof of active DERP data path.
        return StatusDetection(
            state=NodeState.UNKNOWN,
            online=online_field is not False,
            derp_region=str(relay),
            raw_peer=peer,
            raw_status_json=raw_status,
            error="Relay present but no CurAddr/PeerRelay; DERP suspected pending ping confirmation",
        )

    return StatusDetection(
        state=NodeState.UNKNOWN,
        online=online_field is not False,
        raw_peer=peer,
        raw_status_json=raw_status,
        error="Could not determine path from peer data",
    )
