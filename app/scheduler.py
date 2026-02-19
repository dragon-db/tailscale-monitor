from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .models import AppConfig, NodeConfig
from .monitor import MonitorService


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NodeScheduleContext:
    node: NodeConfig
    lock: asyncio.Lock
    trigger_event: asyncio.Event
    task: asyncio.Task | None = None


class MonitorScheduler:
    def __init__(self, config: AppConfig, monitor_service: MonitorService):
        self.config = config
        self.monitor_service = monitor_service
        self._contexts: dict[str, NodeScheduleContext] = {
            node.ip: NodeScheduleContext(
                node=node,
                lock=asyncio.Lock(),
                trigger_event=asyncio.Event(),
            )
            for node in config.nodes
        }
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        for context in self._contexts.values():
            context.task = asyncio.create_task(self._run_node_loop(context))

        logger.info("Scheduler started with %d node loop(s)", len(self._contexts))

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        tasks: list[asyncio.Task] = []
        for context in self._contexts.values():
            if context.task:
                context.task.cancel()
                tasks.append(context.task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Scheduler stopped")

    def has_node(self, ip: str) -> bool:
        return ip in self._contexts

    def trigger_node(self, ip: str) -> dict:
        context = self._contexts.get(ip)
        if context is None:
            return {
                "accepted": False,
                "status": "unknown_node",
                "message": f"Node {ip} is not configured",
            }

        if context.lock.locked():
            return {
                "accepted": True,
                "status": "ignored_in_progress",
                "message": f"Check for {ip} is already in progress; duplicate trigger skipped",
            }

        if context.trigger_event.is_set():
            return {
                "accepted": True,
                "status": "already_pending",
                "message": f"Check for {ip} is already queued",
            }

        context.trigger_event.set()
        return {
            "accepted": True,
            "status": "queued",
            "message": f"Check queued for {ip}",
        }

    def trigger_all(self) -> dict:
        ignored = 0
        queued = []

        for ip in self._contexts:
            result = self.trigger_node(ip)
            if result["status"] in {"ignored_in_progress", "already_pending"}:
                ignored += 1
            if result["status"] == "queued":
                queued.append(ip)

        return {
            "accepted": True,
            "queued_nodes": queued,
            "queued_count": len(queued),
            "ignored_count": ignored,
            "total_nodes": len(self._contexts),
        }

    async def _run_node_loop(self, context: NodeScheduleContext) -> None:
        node = context.node

        try:
            await self._run_check(context, reason="startup")
            while self._running:
                interval = self._interval_for(node)
                deadline = asyncio.get_running_loop().time() + interval

                while self._running:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        await asyncio.wait_for(context.trigger_event.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break

                    context.trigger_event.clear()
                    if context.lock.locked():
                        logger.info("Skipping duplicate manual trigger for %s", node.ip)
                        continue

                    await self._run_check(context, reason="manual")

                await self._run_check(context, reason="scheduled")
        except asyncio.CancelledError:
            logger.debug("Node loop cancelled for %s", node.ip)
            raise
        except Exception:
            logger.exception("Node loop crashed for %s", node.ip)

    async def _run_check(self, context: NodeScheduleContext, reason: str) -> None:
        async with context.lock:
            try:
                await self.monitor_service.run_check(context.node, reason=reason)
            except Exception:
                logger.exception("Check failed for %s", context.node.ip)

    def _interval_for(self, node: NodeConfig) -> int:
        if node.check_interval_seconds and node.check_interval_seconds > 0:
            return node.check_interval_seconds
        return self.config.settings.check_interval_seconds
