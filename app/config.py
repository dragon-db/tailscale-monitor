from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .models import AppConfig, NodeConfig, SecretsConfig, SettingsConfig


logger = logging.getLogger(__name__)


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned or None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_nodes(nodes_raw: list[dict[str, Any]] | None) -> list[NodeConfig]:
    if not nodes_raw:
        return []

    nodes: list[NodeConfig] = []
    seen_ips: set[str] = set()
    for item in nodes_raw:
        ip = str(item.get("ip", "")).strip()
        label = str(item.get("label", ip)).strip() or ip
        if not ip:
            continue
        if ip in seen_ips:
            raise ValueError(f"Duplicate node IP found in config.yaml: {ip}")
        seen_ips.add(ip)
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        interval = item.get("check_interval_seconds")
        interval_value = int(interval) if interval is not None else None
        nodes.append(
            NodeConfig(
                ip=ip,
                label=label,
                tags=[str(tag) for tag in tags],
                check_interval_seconds=interval_value,
            )
        )
    return nodes


def load_config(config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> AppConfig:
    env_file = Path(env_path)
    if env_file.exists():
        # Override inherited process vars so project-local .env is authoritative.
        load_dotenv(dotenv_path=env_file, override=True, encoding="utf-8-sig")
    else:
        logger.warning("Env file not found at %s; relying on process environment only.", env_file)

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_file}. Copy config.yaml.example to config.yaml first."
        )

    raw: dict[str, Any] = {}
    with config_file.open("r", encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle) or {}
        if isinstance(parsed, dict):
            raw = parsed

    settings_raw = raw.get("settings") or {}

    settings = SettingsConfig(
        check_interval_seconds=_as_int(settings_raw.get("check_interval_seconds"), 300),
        ping_on_derp_suspect=_as_bool(settings_raw.get("ping_on_derp_suspect"), True),
        ping_count=_as_int(settings_raw.get("ping_count"), 3),
        ping_timeout_seconds=_as_int(settings_raw.get("ping_timeout_seconds"), 15),
        notification_cooldown_seconds=_as_int(settings_raw.get("notification_cooldown_seconds"), 0),
        data_retention_days=_as_int(settings_raw.get("data_retention_days"), 30),
        log_level=str(settings_raw.get("log_level", "INFO")),
        web_ui_port=_as_int(settings_raw.get("web_ui_port"), 8080),
        offline_threshold_minutes=_as_int(settings_raw.get("offline_threshold_minutes"), 5),
        tailscale_socket=str(
            settings_raw.get("tailscale_socket", "/var/run/tailscale/tailscaled.sock")
        ),
        tailscale_binary=str(settings_raw.get("tailscale_binary", "tailscale")),
    )

    nodes = _normalize_nodes(raw.get("nodes"))

    secrets = SecretsConfig(
        discord_webhook_url=_env_str("DISCORD_WEBHOOK_URL"),
        ntfy_url=_env_str("NTFY_URL"),
        ntfy_topic=_env_str("NTFY_TOPIC"),
        ntfy_token=_env_str("NTFY_TOKEN"),
    )
    logger.info(
        "Secrets resolved: discord=%s ntfy_url=%s ntfy_topic=%s ntfy_token=%s",
        "set" if secrets.discord_webhook_url else "missing",
        "set" if secrets.ntfy_url else "missing",
        "set" if secrets.ntfy_topic else "missing",
        "set" if secrets.ntfy_token else "missing",
    )

    if not nodes:
        logger.warning("No nodes are configured in config.yaml.")

    if not secrets.discord_webhook_url and not (secrets.ntfy_url and secrets.ntfy_topic):
        logger.warning("No notifier configured (Discord/Ntfy missing). Monitoring will continue.")

    if settings.ping_count < 1:
        settings.ping_count = 1

    if settings.check_interval_seconds < 5:
        logger.warning("check_interval_seconds < 5 is too low; forcing to 5 seconds.")
        settings.check_interval_seconds = 5

    if settings.ping_timeout_seconds < 1:
        settings.ping_timeout_seconds = 15

    return AppConfig(settings=settings, nodes=nodes, secrets=secrets)
