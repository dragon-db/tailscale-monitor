from __future__ import annotations

import re

import httpx

from ..models import MetricsCounters, MetricsDelta, NodeState


METRICS_URL = "http://100.100.100.100/metrics"
BYTES_RE = re.compile(
    r'tailscaled_outbound_bytes_total\{[^}]*path="(?P<path>[^"]+)"[^}]*\}\s+(?P<value>[0-9]+(?:\.[0-9]+)?)'
)

DIRECT_PATHS = {"direct_ipv4", "direct_ipv6"}
RELAY_PATHS = {"peer_relay_ipv4", "peer_relay_ipv6"}


async def fetch_metrics(timeout_seconds: int = 5) -> tuple[MetricsCounters | None, str | None]:
    try:
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(METRICS_URL)
            response.raise_for_status()
            return parse_metrics(response.text), None
    except Exception as exc:
        return None, str(exc)


def parse_metrics(payload: str) -> MetricsCounters:
    counters = MetricsCounters()

    for match in BYTES_RE.finditer(payload):
        path = match.group("path")
        value = int(float(match.group("value")))

        if path in DIRECT_PATHS:
            counters.direct += value
        elif path in RELAY_PATHS:
            counters.relay += value
        elif path == "derp":
            counters.derp += value

    return counters


def compute_delta(previous: MetricsCounters | None, current: MetricsCounters) -> MetricsDelta:
    if previous is None:
        return MetricsDelta(direct=0, relay=0, derp=0)

    def _delta(new_value: int, old_value: int) -> int:
        if new_value < old_value:
            return new_value
        return new_value - old_value

    return MetricsDelta(
        direct=_delta(current.direct, previous.direct),
        relay=_delta(current.relay, previous.relay),
        derp=_delta(current.derp, previous.derp),
    )


def dominant_state(delta: MetricsDelta) -> NodeState | None:
    buckets = {
        NodeState.DIRECT: delta.direct,
        NodeState.PEER_RELAY: delta.relay,
        NodeState.DERP: delta.derp,
    }
    state, value = max(buckets.items(), key=lambda item: item[1])
    if value <= 0:
        return None
    return state
