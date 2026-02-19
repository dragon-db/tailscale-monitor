from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .detectors.ping import run_ping_check
from .detectors.status import get_node_status
from .models import (
    AppConfig,
    CheckResult,
    Confidence,
    NodeConfig,
    NodeRuntimeState,
    NodeState,
    PingResult,
    TransitionEvent,
)
from .notifiers.manager import NotifierManager
from .storage import Storage


logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(self, config: AppConfig, storage: Storage, notifier: NotifierManager):
        self.config = config
        self.storage = storage
        self.notifier = notifier
        self.runtime = storage.load_runtime_states(config.nodes)
        for node in config.nodes:
            self.runtime.setdefault(node.ip, NodeRuntimeState())

    async def run_check(self, node: NodeConfig, reason: str = "scheduled") -> CheckResult:
        status_result = await get_node_status(
            tailscale_binary=self.config.settings.tailscale_binary,
            tailscale_socket=self.config.settings.tailscale_socket,
            ip=node.ip,
            offline_threshold_minutes=self.config.settings.offline_threshold_minutes,
        )

        final_state = status_result.state
        derp_suspected = (
            status_result.state == NodeState.UNKNOWN
            and status_result.online
            and bool(status_result.derp_region)
        )

        ping_result = PingResult()
        if (
            self.config.settings.ping_on_derp_suspect
            and status_result.online
            and (final_state == NodeState.DERP or derp_suspected)
        ):
            ping_result = await run_ping_check(
                tailscale_binary=self.config.settings.tailscale_binary,
                tailscale_socket=self.config.settings.tailscale_socket,
                ip=node.ip,
                count=self.config.settings.ping_count,
                timeout_seconds=self.config.settings.ping_timeout_seconds,
            )
            if ping_result.error:
                logger.warning("ping check failed for %s: %s", node.ip, ping_result.error)

        if derp_suspected:
            if ping_result.state in {NodeState.DERP, NodeState.DIRECT, NodeState.PEER_RELAY}:
                final_state = ping_result.state
            else:
                final_state = NodeState.UNKNOWN

        confidence = self._resolve_confidence(
            final_state=final_state,
            status_error=status_result.error,
            ping_result=ping_result,
        )

        resolved_derp_region = None
        if final_state == NodeState.DERP:
            resolved_derp_region = status_result.derp_region or ping_result.derp_region

        check = CheckResult(
            node_ip=node.ip,
            node_label=node.label,
            tags=node.tags,
            checked_at=datetime.now(timezone.utc),
            state=final_state,
            confidence=confidence,
            approach1_state=None,
            approach2_state=status_result.state,
            ping_state=ping_result.state,
            ping_min_ms=ping_result.min_ms,
            ping_avg_ms=ping_result.avg_ms,
            ping_max_ms=ping_result.max_ms,
            ping_packet_loss_pct=ping_result.packet_loss_pct,
            derp_region=resolved_derp_region,
            peer_relay_endpoint=status_result.peer_relay_endpoint,
            bytes_direct_delta=0,
            bytes_relay_delta=0,
            bytes_derp_delta=0,
            raw_status_json=status_result.raw_status_json,
        )

        await asyncio.to_thread(self.storage.insert_check, check)
        if check.state != NodeState.OFFLINE:
            await asyncio.to_thread(self.storage.update_node_last_seen, node.ip, check.checked_at)

        await self._handle_transition(node, check, runtime, reason=reason)
        return check

    def _resolve_confidence(
        self,
        final_state: NodeState,
        status_error: str | None,
        ping_result: PingResult,
    ) -> Confidence:
        if status_error and final_state == NodeState.UNKNOWN:
            return Confidence.LOW

        if final_state == NodeState.UNKNOWN:
            return Confidence.LOW

        if status_error and "derp suspected pending ping confirmation" in status_error.lower():
            if ping_result.state is None or ping_result.error:
                return Confidence.LOW

        if status_error and "stale and inactive" in status_error.lower():
            return Confidence.LOW

        if final_state == NodeState.OFFLINE:
            return Confidence.HIGH

        if final_state in {NodeState.DIRECT, NodeState.PEER_RELAY}:
            return Confidence.HIGH

        if final_state == NodeState.DERP and ping_result.error:
            return Confidence.MEDIUM
        if final_state == NodeState.DERP and ping_result.state == NodeState.DERP:
            return Confidence.HIGH
        if final_state == NodeState.DERP and ping_result.state in {NodeState.DIRECT, NodeState.PEER_RELAY}:
            return Confidence.MEDIUM

        return Confidence.MEDIUM

    async def _handle_transition(
        self,
        node: NodeConfig,
        check: CheckResult,
        runtime: NodeRuntimeState,
        reason: str,
    ) -> None:
        previous = runtime.last_state
        current = check.state

        if previous is None:
            runtime.last_state = current
            runtime.last_state_since = check.checked_at
            runtime.last_derp_region = check.derp_region
            return

        region_change = (
            previous == NodeState.DERP
            and current == NodeState.DERP
            and runtime.last_derp_region is not None
            and check.derp_region is not None
            and runtime.last_derp_region != check.derp_region
        )
        state_change = previous != current

        if not state_change and not region_change:
            return

        duration_previous_seconds: int | None = None
        if runtime.last_state_since is not None:
            duration_previous_seconds = int(
                (check.checked_at - runtime.last_state_since).total_seconds()
            )
            if duration_previous_seconds < 0:
                duration_previous_seconds = 0

        transition_reason = self._transition_reason(
            previous=previous,
            current=current,
            state_change=state_change,
            region_change=region_change,
            old_region=runtime.last_derp_region,
            new_region=check.derp_region,
            trigger=reason,
        )

        should_notify = self._is_notifiable(previous, current, region_change)
        cooldown_key = self._cooldown_key(previous, current, region_change)
        suppressed = self._is_in_cooldown(runtime, cooldown_key, check.checked_at)

        event = TransitionEvent(
            node_ip=node.ip,
            transitioned_at=check.checked_at,
            previous_state=previous,
            current_state=current,
            duration_previous_seconds=duration_previous_seconds,
            notified=False,
            notification_channels=[],
            transition_reason=transition_reason,
        )

        if should_notify and not suppressed:
            channels = await self.notifier.send_transition(node, event, check)
            event.notification_channels = channels
            event.notified = bool(channels)
            if event.notified:
                runtime.last_notified_at[cooldown_key] = check.checked_at

        await asyncio.to_thread(self.storage.insert_transition, event)

        if state_change:
            runtime.last_state = current
            runtime.last_state_since = check.checked_at

        runtime.last_derp_region = check.derp_region

    def _is_notifiable(self, previous: NodeState, current: NodeState, region_change: bool) -> bool:
        if region_change:
            return True

        if previous == current:
            return False

        if current == NodeState.OFFLINE:
            return True
        if previous == NodeState.OFFLINE:
            return True

        pair = {previous, current}
        return pair in [
            {NodeState.DIRECT, NodeState.DERP},
            {NodeState.DIRECT, NodeState.PEER_RELAY},
            {NodeState.PEER_RELAY, NodeState.DERP},
        ]

    def _cooldown_key(self, previous: NodeState, current: NodeState, region_change: bool) -> str:
        if region_change:
            return "DERP_REGION_CHANGE"
        return f"{previous.value}->{current.value}"

    def _is_in_cooldown(
        self,
        runtime: NodeRuntimeState,
        cooldown_key: str,
        now: datetime,
    ) -> bool:
        cooldown_seconds = self.config.settings.notification_cooldown_seconds
        if cooldown_seconds <= 0:
            return False

        previous_notified = runtime.last_notified_at.get(cooldown_key)
        if previous_notified is None:
            return False

        elapsed = (now - previous_notified).total_seconds()
        return elapsed < cooldown_seconds

    def _transition_reason(
        self,
        previous: NodeState,
        current: NodeState,
        state_change: bool,
        region_change: bool,
        old_region: str | None,
        new_region: str | None,
        trigger: str,
    ) -> str:
        if state_change:
            return f"{previous.value} -> {current.value} ({trigger} check)"
        if region_change:
            return f"DERP region changed: {old_region} -> {new_region} ({trigger} check)"
        return f"No transition ({trigger} check)"
