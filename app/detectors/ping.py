from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..commands import tailscale_ping
from ..models import NodeState, PingResult


PONG_RE = re.compile(r"via (?P<via>.+?) in (?P<latency>[0-9.]+)ms", re.IGNORECASE)
DERP_RE = re.compile(r"DERP\((?P<region>[^)]+)\)", re.IGNORECASE)


def _state_from_via(via: str) -> tuple[NodeState, str | None]:
    derp_match = DERP_RE.search(via)
    if derp_match:
        return NodeState.DERP, derp_match.group("region")

    lower = via.lower()
    if "peer_relay" in lower or "peer relay" in lower:
        return NodeState.PEER_RELAY, None

    return NodeState.DIRECT, None


def parse_ping_samples(output_text: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for raw_line in output_text.splitlines():
        line = raw_line.strip()
        if "pong" not in line.lower() or "via" not in line.lower():
            continue

        match = PONG_RE.search(line)
        if not match:
            continue

        latency = float(match.group("latency"))
        via = match.group("via").strip()
        state, region = _state_from_via(via)
        samples.append(
            {
                "line": line,
                "via": via,
                "state": state.value,
                "latency_ms": latency,
                "derp_region": region,
            }
        )
    return samples


def summarize_ping_samples(
    samples: list[dict[str, Any]],
    count: int,
    raw_output: str | None,
    error: str | None,
) -> PingResult:
    if not samples:
        return PingResult(
            packet_loss_pct=100.0,
            raw_output=raw_output,
            error=error or "No pong responses parsed from tailscale ping output",
        )

    latencies = [float(sample["latency_ms"]) for sample in samples]
    states = [NodeState(str(sample["state"])) for sample in samples]
    regions = [str(sample["derp_region"]) for sample in samples if sample.get("derp_region")]

    received = len(samples)
    sent = max(count, received)
    packet_loss = max(0.0, ((sent - received) / sent) * 100.0)

    dominant_state = Counter(states).most_common(1)[0][0]

    dominant_region = None
    if dominant_state == NodeState.DERP and regions:
        dominant_region = Counter(regions).most_common(1)[0][0]

    return PingResult(
        state=dominant_state,
        min_ms=min(latencies),
        avg_ms=(sum(latencies) / len(latencies)),
        max_ms=max(latencies),
        packet_loss_pct=packet_loss,
        derp_region=dominant_region,
        raw_output=raw_output,
        error=error,
    )


async def run_ping_check(
    tailscale_binary: str,
    tailscale_socket: str,
    ip: str,
    count: int,
    timeout_seconds: int,
) -> PingResult:
    output, error = await tailscale_ping(
        binary=tailscale_binary,
        socket_path=tailscale_socket,
        ip=ip,
        count=count,
        timeout_seconds=timeout_seconds,
    )
    output_text = output or ""
    samples = parse_ping_samples(output_text)
    return summarize_ping_samples(
        samples=samples,
        count=count,
        raw_output=output_text or None,
        error=error,
    )
