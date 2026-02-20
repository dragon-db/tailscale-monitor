from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .models import AppConfig
from .monitor import MonitorService
from .notifiers.manager import NotifierManager
from .scheduler import MonitorScheduler
from .storage import Storage


def create_app(
    config: AppConfig,
    storage: Storage,
    scheduler: MonitorScheduler,
    notifier: NotifierManager,
    monitor_service: MonitorService,
) -> FastAPI:
    configured_ips = [node.ip for node in config.nodes]
    node_by_ip = {node.ip: node for node in config.nodes}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="tailscale-monitor", lifespan=lifespan)
    web_dir = Path(__file__).resolve().parent / "web"

    app.state.config = config
    app.state.storage = storage
    app.state.scheduler = scheduler
    app.state.notifier = notifier
    app.state.monitor_service = monitor_service

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/nodes")
    async def get_nodes() -> list[dict]:
        return await asyncio.to_thread(storage.get_current_state_all_nodes, configured_ips)

    @app.get("/api/nodes/{ip}/history")
    async def get_node_history(ip: str, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
        if not scheduler.has_node(ip):
            raise HTTPException(status_code=404, detail=f"Node {ip} is not configured")
        return await asyncio.to_thread(storage.get_node_history, ip, limit)

    @app.get("/api/transitions")
    async def get_transitions(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
        return await asyncio.to_thread(storage.get_recent_transitions, limit, configured_ips)

    @app.get("/api/stats")
    async def get_stats() -> dict:
        return await asyncio.to_thread(storage.get_stats_summary, configured_ips)

    @app.post("/api/check/all", status_code=202)
    async def post_check_all() -> dict:
        return scheduler.trigger_all()

    @app.post("/api/check/{ip}", status_code=202)
    async def post_check_node(ip: str) -> dict:
        if not scheduler.has_node(ip):
            raise HTTPException(status_code=404, detail=f"Node {ip} is not configured")
        return scheduler.trigger_node(ip)

    @app.post("/api/ping/{ip}")
    async def post_ping_node(ip: str) -> dict:
        node = node_by_ip.get(ip)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node {ip} is not configured")
        return await monitor_service.run_manual_ping(node=node, count=5)

    @app.post("/api/test/discord")
    async def post_test_discord() -> dict:
        success, error = await notifier.send_discord_test()
        if not success:
            raise HTTPException(status_code=400, detail=error or "Discord test failed")
        return {"ok": True, "message": "Discord test notification sent"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/app.js")
    async def app_js() -> FileResponse:
        return FileResponse(web_dir / "app.js", media_type="application/javascript")

    @app.get("/style.css")
    async def style_css() -> FileResponse:
        return FileResponse(web_dir / "style.css", media_type="text/css")

    return app
