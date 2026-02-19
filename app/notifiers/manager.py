from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..models import CheckResult, NodeConfig, NodeState, SecretsConfig, TransitionEvent
from .discord import send_discord_webhook
from .ntfy import send_ntfy_message


logger = logging.getLogger(__name__)


COLOR_BY_STATE = {
    NodeState.OFFLINE: 16711680,
    NodeState.DIRECT: 51411,
    NodeState.PEER_RELAY: 16766464,
    NodeState.DERP: 16740608,
    NodeState.UNKNOWN: 8421504,
}


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {sec}s"


def _title(node_label: str, previous: NodeState, current: NodeState) -> str:
    if current == NodeState.OFFLINE:
        return f"Node Offline: {node_label}"
    if previous == NodeState.OFFLINE and current != NodeState.OFFLINE:
        return f"Node Back Online: {node_label}"
    if current == NodeState.DERP and previous != NodeState.DERP:
        return f"DERP Fallback: {node_label}"
    return f"Connection Changed: {node_label}"


def _priority(previous: NodeState, current: NodeState) -> str:
    if current == NodeState.OFFLINE:
        return "urgent"
    if current == NodeState.DERP:
        return "high"
    if current == NodeState.PEER_RELAY:
        return "default"
    if previous == NodeState.OFFLINE and current == NodeState.DIRECT:
        return "low"
    return "default"


class NotifierManager:
    def __init__(self, secrets: SecretsConfig):
        self._secrets = secrets

    @property
    def has_channels(self) -> bool:
        return bool(self._secrets.discord_webhook_url) or bool(
            self._secrets.ntfy_url and self._secrets.ntfy_topic
        )

    async def send_transition(
        self,
        node: NodeConfig,
        transition: TransitionEvent,
        check: CheckResult,
    ) -> list[str]:
        if not self.has_channels:
            return []

        channels_sent: list[str] = []
        tasks: list[asyncio.Task[tuple[str, bool, str | None]]] = []

        if self._secrets.discord_webhook_url:
            tasks.append(asyncio.create_task(self._send_discord(node, transition, check)))

        if self._secrets.ntfy_url and self._secrets.ntfy_topic:
            tasks.append(asyncio.create_task(self._send_ntfy(node, transition, check)))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("Notifier task failed: %s", result)
                continue
            channel, success, error = result
            if success:
                channels_sent.append(channel)
                logger.info("Notification sent via %s for %s", channel, node.ip)
            else:
                logger.error("Notification failed via %s for %s: %s", channel, node.ip, error)

        return channels_sent

    async def _send_discord(
        self,
        node: NodeConfig,
        transition: TransitionEvent,
        check: CheckResult,
    ) -> tuple[str, bool, str | None]:
        assert self._secrets.discord_webhook_url is not None
        embed = {
            "title": _title(node.label, transition.previous_state, transition.current_state),
            "description": transition.transition_reason,
            "color": COLOR_BY_STATE.get(check.state, COLOR_BY_STATE[NodeState.UNKNOWN]),
            "fields": [
                {
                    "name": "Node",
                    "value": f"{node.label} ({node.ip})",
                    "inline": False,
                },
                {
                    "name": "Previous State",
                    "value": f"{transition.previous_state.value} for {_fmt_duration(transition.duration_previous_seconds)}",
                    "inline": True,
                },
                {
                    "name": "Current State",
                    "value": f"{transition.current_state.value} ({check.confidence.value} confidence)",
                    "inline": True,
                },
                {
                    "name": "Detection",
                    "value": (
                        f"Status JSON: {check.approach2_state.value if check.approach2_state else 'UNKNOWN'} | "
                        f"Metrics: {check.approach1_state.value if check.approach1_state else 'UNKNOWN'} | "
                        f"Ping: {check.ping_state.value if check.ping_state else 'N/A'}"
                    ),
                    "inline": False,
                },
                {
                    "name": "Traffic Delta",
                    "value": (
                        f"Direct {check.bytes_direct_delta} bytes | "
                        f"Relay {check.bytes_relay_delta} bytes | "
                        f"DERP {check.bytes_derp_delta} bytes"
                    ),
                    "inline": False,
                },
            ],
            "footer": {
                "text": "tailscale-monitor",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if check.ping_avg_ms is not None:
            embed["fields"].append(
                {
                    "name": "Latency",
                    "value": f"Min {check.ping_min_ms:.2f}ms / Avg {check.ping_avg_ms:.2f}ms / Max {check.ping_max_ms:.2f}ms",
                    "inline": False,
                }
            )

        if check.ping_packet_loss_pct is not None:
            embed["fields"].append(
                {
                    "name": "Packet Loss",
                    "value": f"{check.ping_packet_loss_pct:.2f}%",
                    "inline": True,
                }
            )

        if check.derp_region:
            embed["fields"].append(
                {
                    "name": "DERP Region",
                    "value": check.derp_region,
                    "inline": True,
                }
            )

        payload = {"embeds": [embed]}
        success, error = await send_discord_webhook(self._secrets.discord_webhook_url, payload)
        return "discord", success, error

    async def _send_ntfy(
        self,
        node: NodeConfig,
        transition: TransitionEvent,
        check: CheckResult,
    ) -> tuple[str, bool, str | None]:
        assert self._secrets.ntfy_url is not None
        assert self._secrets.ntfy_topic is not None

        title = f"TS: {node.label} -> {check.state.value}"
        priority = _priority(transition.previous_state, transition.current_state)
        tags = ["tailscale", check.state.value.lower(), node.label.replace(" ", "-").lower()]

        lines = [
            transition.transition_reason,
            "",
            f"Node: {node.label} ({node.ip})",
            f"Previous: {transition.previous_state.value} for {_fmt_duration(transition.duration_previous_seconds)}",
            f"Current: {transition.current_state.value} ({check.confidence.value})",
            "",
            "Detection:",
            f"- Status: {check.approach2_state.value if check.approach2_state else 'UNKNOWN'}",
            f"- Metrics: {check.approach1_state.value if check.approach1_state else 'UNKNOWN'}",
            f"- Ping: {check.ping_state.value if check.ping_state else 'Not run'}",
        ]

        if check.ping_avg_ms is not None:
            lines.extend(
                [
                    "",
                    f"Latency: min {check.ping_min_ms:.2f}ms, avg {check.ping_avg_ms:.2f}ms, max {check.ping_max_ms:.2f}ms",
                ]
            )

        if check.derp_region:
            lines.append(f"DERP region: {check.derp_region}")

        lines.append(
            f"Traffic delta: direct {check.bytes_direct_delta}, relay {check.bytes_relay_delta}, derp {check.bytes_derp_delta}"
        )

        success, error = await send_ntfy_message(
            base_url=self._secrets.ntfy_url,
            topic=self._secrets.ntfy_topic,
            title=title,
            priority=priority,
            tags=tags,
            body="\n".join(lines),
            token=self._secrets.ntfy_token,
        )
        return "ntfy", success, error
