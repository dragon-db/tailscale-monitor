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
    NodeState.INACTIVE: 5793266,
    NodeState.UNKNOWN: 8421504,
}


def _state_label(state: NodeState) -> str:
    mapping = {
        NodeState.DIRECT: "DIRECT",
        NodeState.PEER_RELAY: "SPEED RELAY",
        NodeState.DERP: "RELAY (DERP)",
        NodeState.INACTIVE: "INACTIVE",
        NodeState.OFFLINE: "OFFLINE",
        NodeState.UNKNOWN: "UNKNOWN",
    }
    return mapping.get(state, state.value)


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
    if current == NodeState.INACTIVE and previous != NodeState.INACTIVE:
        return f"Node Inactive: {node_label}"
    if current == NodeState.DERP and previous != NodeState.DERP:
        return f"DERP Relay Fallback: {node_label}"
    return f"Connection Changed: {node_label}"


def _priority(previous: NodeState, current: NodeState) -> str:
    if current == NodeState.OFFLINE:
        return "urgent"
    if current == NodeState.DERP:
        return "high"
    if current == NodeState.INACTIVE:
        return "low"
    if current == NodeState.PEER_RELAY:
        return "default"
    if previous == NodeState.OFFLINE and current == NodeState.DIRECT:
        return "low"
    return "default"


class NotifierManager:
    def __init__(self, secrets: SecretsConfig):
        self._secrets = secrets
        logger.info(
            "Notifier channels configured: discord=%s ntfy=%s",
            "enabled" if self._secrets.discord_webhook_url else "disabled",
            "enabled" if (self._secrets.ntfy_url and self._secrets.ntfy_topic) else "disabled",
        )

    @property
    def has_channels(self) -> bool:
        return bool(self._secrets.discord_webhook_url) or bool(
            self._secrets.ntfy_url and self._secrets.ntfy_topic
        )

    @property
    def has_discord(self) -> bool:
        return bool(self._secrets.discord_webhook_url)

    @property
    def has_ntfy(self) -> bool:
        return bool(self._secrets.ntfy_url and self._secrets.ntfy_topic)

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

    async def send_discord_test(self) -> tuple[bool, str | None]:
        if not self._secrets.discord_webhook_url:
            return False, "DISCORD_WEBHOOK_URL is not configured"

        payload = {
            "content": "tailscale-monitor Discord test notification",
            "embeds": [
                {
                    "title": "Discord Test",
                    "description": "If you can read this, Discord webhook delivery is working.",
                    "color": 51411,
                    "footer": {"text": "tailscale-monitor"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "allowed_mentions": {"parse": []},
        }
        return await send_discord_webhook(self._secrets.discord_webhook_url, payload)

    async def _send_discord(
        self,
        node: NodeConfig,
        transition: TransitionEvent,
        check: CheckResult,
    ) -> tuple[str, bool, str | None]:
        assert self._secrets.discord_webhook_url is not None
        event_title = _title(node.label, transition.previous_state, transition.current_state)
        content = f"{event_title} | {transition.transition_reason}".strip()
        if len(content) > 1900:
            content = f"{content[:1897]}..."

        embed = {
            "title": event_title,
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
                    "value": (
                        f"{_state_label(transition.previous_state)} "
                        f"for {_fmt_duration(transition.duration_previous_seconds)}"
                    ),
                    "inline": True,
                },
                {
                    "name": "Current State",
                    "value": f"{_state_label(transition.current_state)} ({check.confidence.value} confidence)",
                    "inline": True,
                },
                {
                    "name": "Detection",
                    "value": (
                        f"Status JSON: {_state_label(check.approach2_state) if check.approach2_state else 'UNKNOWN'} | "
                        "Metrics: Backseated | "
                        f"Ping: {_state_label(check.ping_state) if check.ping_state else 'N/A'}"
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

        payload = {
            "content": content,
            "embeds": [embed],
            "allowed_mentions": {"parse": []},
        }
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

        title = f"TS: {node.label} -> {_state_label(check.state)}"
        priority = _priority(transition.previous_state, transition.current_state)
        tags = ["tailscale", check.state.value.lower(), node.label.replace(" ", "-").lower()]

        lines = [
            transition.transition_reason,
            "",
            f"Node: {node.label} ({node.ip})",
            (
                f"Previous: {_state_label(transition.previous_state)} "
                f"for {_fmt_duration(transition.duration_previous_seconds)}"
            ),
            f"Current: {_state_label(transition.current_state)} ({check.confidence.value})",
            "",
            "Detection:",
            f"- Status: {_state_label(check.approach2_state) if check.approach2_state else 'UNKNOWN'}",
            "- Metrics: Backseated",
            f"- Ping: {_state_label(check.ping_state) if check.ping_state else 'Not run'}",
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
