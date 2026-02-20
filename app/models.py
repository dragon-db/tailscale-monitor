from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class NodeState(str, Enum):
    DIRECT = "DIRECT"
    PEER_RELAY = "PEER_RELAY"
    DERP = "DERP"
    INACTIVE = "INACTIVE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(slots=True)
class NodeConfig:
    ip: str
    label: str
    tags: list[str] = field(default_factory=list)
    check_interval_seconds: int | None = None


@dataclass(slots=True)
class SettingsConfig:
    check_interval_seconds: int = 300
    ping_on_derp_suspect: bool = True
    ping_count: int = 3
    ping_timeout_seconds: int = 15
    notification_cooldown_seconds: int = 0
    data_retention_days: int = 30
    log_level: str = "INFO"
    web_ui_port: int = 8080
    offline_threshold_minutes: int = 5
    tailscale_socket: str = "/var/run/tailscale/tailscaled.sock"
    tailscale_binary: str = "tailscale"


@dataclass(slots=True)
class SecretsConfig:
    discord_webhook_url: str | None = None
    ntfy_url: str | None = None
    ntfy_topic: str | None = None
    ntfy_token: str | None = None


@dataclass(slots=True)
class AppConfig:
    settings: SettingsConfig
    nodes: list[NodeConfig]
    secrets: SecretsConfig


@dataclass(slots=True)
class MetricsCounters:
    direct: int = 0
    relay: int = 0
    derp: int = 0


@dataclass(slots=True)
class MetricsDelta:
    direct: int = 0
    relay: int = 0
    derp: int = 0


@dataclass(slots=True)
class StatusDetection:
    state: NodeState
    online: bool
    derp_region: str | None = None
    cur_addr_endpoint: str | None = None
    peer_relay_endpoint: str | None = None
    relay_hint: str | None = None
    raw_peer: dict[str, Any] | None = None
    raw_status_json: str | None = None
    error: str | None = None


@dataclass(slots=True)
class PingResult:
    state: NodeState | None = None
    min_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    packet_loss_pct: float | None = None
    derp_region: str | None = None
    raw_output: str | None = None
    error: str | None = None


@dataclass(slots=True)
class CheckResult:
    node_ip: str
    node_label: str
    tags: list[str]
    checked_at: datetime
    state: NodeState
    confidence: Confidence
    approach1_state: NodeState | None
    approach2_state: NodeState | None
    ping_state: NodeState | None
    ping_min_ms: float | None
    ping_avg_ms: float | None
    ping_max_ms: float | None
    ping_packet_loss_pct: float | None
    derp_region: str | None
    cur_addr_endpoint: str | None
    peer_relay_endpoint: str | None
    relay_hint: str | None
    bytes_direct_delta: int
    bytes_relay_delta: int
    bytes_derp_delta: int
    raw_status_json: str | None


@dataclass(slots=True)
class TransitionEvent:
    node_ip: str
    transitioned_at: datetime
    previous_state: NodeState
    current_state: NodeState
    duration_previous_seconds: int | None
    notified: bool
    notification_channels: list[str]
    transition_reason: str


@dataclass(slots=True)
class NodeRuntimeState:
    last_state: NodeState | None = None
    last_state_since: datetime | None = None
    last_derp_region: str | None = None
    last_notified_at: dict[str, datetime] = field(default_factory=dict)
    previous_metrics: MetricsCounters | None = None
