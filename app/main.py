from __future__ import annotations

import logging

import uvicorn

from .api import create_app
from .config import load_config
from .logging import setup_logging
from .monitor import MonitorService
from .notifiers.manager import NotifierManager
from .scheduler import MonitorScheduler
from .storage import Storage


logger = logging.getLogger(__name__)


def build_application():
    config = load_config()
    setup_logging(config.settings.log_level)

    storage = Storage("data/monitor.db")
    storage.initialize()
    storage.upsert_nodes(config.nodes)
    deleted = storage.cleanup_old_checks(config.settings.data_retention_days)
    if deleted:
        logger.info("Data retention cleanup removed %d old check rows", deleted)

    notifier = NotifierManager(config.secrets)
    monitor_service = MonitorService(config=config, storage=storage, notifier=notifier)
    scheduler = MonitorScheduler(config=config, monitor_service=monitor_service)
    app = create_app(config=config, storage=storage, scheduler=scheduler, notifier=notifier)

    return app


app = build_application()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=app.state.config.settings.web_ui_port,
        reload=False,
    )
