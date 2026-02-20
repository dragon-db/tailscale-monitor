from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import CheckResult, NodeConfig, NodeRuntimeState, NodeState, TransitionEvent


def _dt_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


class Storage:
    def __init__(self, db_path: str | Path = "data/monitor.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(row["name"]) == column for row in rows)

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        ddl_type: str,
    ) -> None:
        if self._column_exists(conn, table, column):
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS nodes (
            ip TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            tags TEXT NOT NULL,
            added_at TEXT NOT NULL,
            last_seen_at TEXT
        );

        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_ip TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            state TEXT NOT NULL,
            confidence TEXT NOT NULL,
            approach1_state TEXT,
            approach2_state TEXT,
            ping_state TEXT,
            ping_min_ms REAL,
            ping_avg_ms REAL,
            ping_max_ms REAL,
            ping_packet_loss_pct REAL,
            derp_region TEXT,
            cur_addr_endpoint TEXT,
            peer_relay_endpoint TEXT,
            relay_hint TEXT,
            bytes_direct_delta INTEGER NOT NULL,
            bytes_relay_delta INTEGER NOT NULL,
            bytes_derp_delta INTEGER NOT NULL,
            raw_status_json TEXT,
            FOREIGN KEY(node_ip) REFERENCES nodes(ip)
        );

        CREATE TABLE IF NOT EXISTS transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_ip TEXT NOT NULL,
            transitioned_at TEXT NOT NULL,
            previous_state TEXT NOT NULL,
            current_state TEXT NOT NULL,
            duration_previous_seconds INTEGER,
            notified INTEGER NOT NULL,
            notification_channels TEXT NOT NULL,
            transition_reason TEXT NOT NULL,
            FOREIGN KEY(node_ip) REFERENCES nodes(ip)
        );

        CREATE INDEX IF NOT EXISTS idx_checks_node_checked_at
            ON checks(node_ip, checked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_transitions_node_time
            ON transitions(node_ip, transitioned_at DESC);
        CREATE INDEX IF NOT EXISTS idx_transitions_time
            ON transitions(transitioned_at DESC);
        """
        with self._connect() as conn:
            conn.executescript(schema)
            self._ensure_column(conn, "checks", "cur_addr_endpoint", "TEXT")
            self._ensure_column(conn, "checks", "relay_hint", "TEXT")
            conn.commit()

    def upsert_nodes(self, nodes: list[NodeConfig]) -> None:
        now = _to_iso(_dt_now())
        with self._write_lock, self._connect() as conn:
            for node in nodes:
                conn.execute(
                    """
                    INSERT INTO nodes (ip, label, tags, added_at, last_seen_at)
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(ip) DO UPDATE SET
                        label = excluded.label,
                        tags = excluded.tags
                    """,
                    (node.ip, node.label, json.dumps(node.tags), now),
                )
            conn.commit()

    def update_node_last_seen(self, ip: str, last_seen: datetime) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("UPDATE nodes SET last_seen_at = ? WHERE ip = ?", (_to_iso(last_seen), ip))
            conn.commit()

    def insert_check(self, check: CheckResult) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO checks (
                    node_ip, checked_at, state, confidence, approach1_state, approach2_state,
                    ping_state, ping_min_ms, ping_avg_ms, ping_max_ms, ping_packet_loss_pct,
                    derp_region, cur_addr_endpoint, peer_relay_endpoint, relay_hint,
                    bytes_direct_delta, bytes_relay_delta, bytes_derp_delta, raw_status_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check.node_ip,
                    _to_iso(check.checked_at),
                    check.state.value,
                    check.confidence.value,
                    check.approach1_state.value if check.approach1_state else None,
                    check.approach2_state.value if check.approach2_state else None,
                    check.ping_state.value if check.ping_state else None,
                    check.ping_min_ms,
                    check.ping_avg_ms,
                    check.ping_max_ms,
                    check.ping_packet_loss_pct,
                    check.derp_region,
                    check.cur_addr_endpoint,
                    check.peer_relay_endpoint,
                    check.relay_hint,
                    check.bytes_direct_delta,
                    check.bytes_relay_delta,
                    check.bytes_derp_delta,
                    check.raw_status_json,
                ),
            )
            conn.commit()

    def insert_transition(self, event: TransitionEvent) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO transitions (
                    node_ip, transitioned_at, previous_state, current_state,
                    duration_previous_seconds, notified, notification_channels, transition_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.node_ip,
                    _to_iso(event.transitioned_at),
                    event.previous_state.value,
                    event.current_state.value,
                    event.duration_previous_seconds,
                    1 if event.notified else 0,
                    json.dumps(event.notification_channels),
                    event.transition_reason,
                ),
            )
            conn.commit()

    def load_runtime_states(self, nodes: list[NodeConfig]) -> dict[str, NodeRuntimeState]:
        runtime: dict[str, NodeRuntimeState] = {}
        with self._connect() as conn:
            for node in nodes:
                runtime_state = NodeRuntimeState()

                transition_row = conn.execute(
                    """
                    SELECT current_state, transitioned_at
                    FROM transitions
                    WHERE node_ip = ?
                    ORDER BY transitioned_at DESC
                    LIMIT 1
                    """,
                    (node.ip,),
                ).fetchone()

                if transition_row:
                    try:
                        runtime_state.last_state = NodeState(transition_row["current_state"])
                    except Exception:
                        runtime_state.last_state = None
                    runtime_state.last_state_since = _from_iso(transition_row["transitioned_at"])

                check_row = conn.execute(
                    """
                    SELECT state, checked_at, derp_region
                    FROM checks
                    WHERE node_ip = ?
                    ORDER BY checked_at DESC
                    LIMIT 1
                    """,
                    (node.ip,),
                ).fetchone()

                if check_row:
                    if runtime_state.last_state is None:
                        try:
                            runtime_state.last_state = NodeState(check_row["state"])
                        except Exception:
                            runtime_state.last_state = None
                        runtime_state.last_state_since = _from_iso(check_row["checked_at"])
                    runtime_state.last_derp_region = check_row["derp_region"]

                runtime[node.ip] = runtime_state

        return runtime

    def cleanup_old_checks(self, retention_days: int) -> int:
        cutoff = _dt_now() - timedelta(days=retention_days)
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM checks WHERE checked_at < ?", (_to_iso(cutoff),))
            conn.commit()
            return cur.rowcount

    def get_current_state_all_nodes(self, node_ips: list[str] | None = None) -> list[dict]:
        if node_ips is not None and len(node_ips) == 0:
            return []

        ip_filter = ""
        params: list[object] = []
        if node_ips is not None:
            placeholders = ", ".join("?" for _ in node_ips)
            ip_filter = f"WHERE n.ip IN ({placeholders})"
            params.extend(node_ips)

        query = """
        SELECT
            n.ip,
            n.label,
            n.tags,
            c.state,
            c.confidence,
            c.checked_at,
            c.derp_region,
            c.cur_addr_endpoint,
            c.peer_relay_endpoint,
            c.relay_hint,
            c.ping_avg_ms
        FROM nodes n
        LEFT JOIN checks c ON c.id = (
            SELECT c2.id
            FROM checks c2
            WHERE c2.node_ip = n.ip
            ORDER BY c2.checked_at DESC
            LIMIT 1
        )
        {ip_filter}
        ORDER BY n.label COLLATE NOCASE ASC
        """

        rows: list[dict] = []
        with self._connect() as conn:
            rendered_query = query.format(ip_filter=ip_filter)
            for row in conn.execute(rendered_query, params).fetchall():
                uptime = self.get_uptime_stats(row["ip"], days=7)
                rows.append(
                    {
                        "ip": row["ip"],
                        "label": row["label"],
                        "tags": json.loads(row["tags"] or "[]"),
                        "current_state": row["state"] or NodeState.UNKNOWN.value,
                        "confidence": row["confidence"] or "low",
                        "last_checked": row["checked_at"],
                        "derp_region": row["derp_region"],
                        "cur_addr_endpoint": row["cur_addr_endpoint"],
                        "peer_relay_endpoint": row["peer_relay_endpoint"],
                        "relay_hint": row["relay_hint"],
                        "ping_avg_ms": row["ping_avg_ms"],
                        "uptime_7d_pct": uptime["uptime_pct"],
                    }
                )

        return rows

    def get_node_history(self, ip: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 1000))
        query = """
        SELECT
            id, node_ip, checked_at, state, confidence, approach1_state, approach2_state,
            ping_state, ping_min_ms, ping_avg_ms, ping_max_ms, ping_packet_loss_pct,
            derp_region, cur_addr_endpoint, peer_relay_endpoint, relay_hint,
            bytes_direct_delta, bytes_relay_delta, bytes_derp_delta
        FROM checks
        WHERE node_ip = ?
        ORDER BY checked_at DESC
        LIMIT ?
        """

        with self._connect() as conn:
            rows = conn.execute(query, (ip, limit)).fetchall()
            return [dict(row) for row in rows]

    def get_recent_transitions(self, limit: int = 50, node_ips: list[str] | None = None) -> list[dict]:
        limit = max(1, min(limit, 500))
        if node_ips is not None and len(node_ips) == 0:
            return []

        ip_filter = ""
        params: list[object] = []
        if node_ips is not None:
            placeholders = ", ".join("?" for _ in node_ips)
            ip_filter = f"WHERE t.node_ip IN ({placeholders})"
            params.extend(node_ips)

        query = """
        SELECT
            t.id,
            t.node_ip,
            n.label,
            t.transitioned_at,
            t.previous_state,
            t.current_state,
            t.duration_previous_seconds,
            t.notified,
            t.notification_channels,
            t.transition_reason
        FROM transitions t
        LEFT JOIN nodes n ON n.ip = t.node_ip
        {ip_filter}
        ORDER BY t.transitioned_at DESC
        LIMIT ?
        """
        params.append(limit)

        with self._connect() as conn:
            rendered_query = query.format(ip_filter=ip_filter)
            rows = conn.execute(rendered_query, params).fetchall()
            result: list[dict] = []
            for row in rows:
                item = dict(row)
                item["notified"] = bool(item["notified"])
                item["notification_channels"] = json.loads(item["notification_channels"] or "[]")
                result.append(item)
            return result

    def get_uptime_stats(self, ip: str, days: int = 7) -> dict:
        cutoff = _dt_now() - timedelta(days=days)
        query = """
        SELECT state, COUNT(*) AS count
        FROM checks
        WHERE node_ip = ? AND checked_at >= ?
        GROUP BY state
        """

        with self._connect() as conn:
            rows = conn.execute(query, (ip, _to_iso(cutoff))).fetchall()

        totals: dict[str, int] = {}
        total_count = 0
        for row in rows:
            count = int(row["count"])
            state = str(row["state"])
            totals[state] = count
            total_count += count

        if total_count == 0:
            return {"uptime_pct": None, "state_pct": {}}

        online_count = (
            totals.get(NodeState.DIRECT.value, 0)
            + totals.get(NodeState.PEER_RELAY.value, 0)
            + totals.get(NodeState.DERP.value, 0)
            + totals.get(NodeState.INACTIVE.value, 0)
        )
        uptime_pct = round((online_count / total_count) * 100.0, 2)

        state_pct = {
            state: round((count / total_count) * 100.0, 2)
            for state, count in totals.items()
        }
        return {"uptime_pct": uptime_pct, "state_pct": state_pct}

    def get_stats_summary(self, node_ips: list[str] | None = None) -> dict:
        nodes = self.get_current_state_all_nodes(node_ips=node_ips)
        total_nodes = len(nodes)
        offline = 0
        derp = 0
        direct = 0
        inactive = 0
        online = 0
        last_check_time: str | None = None

        for node in nodes:
            state = node.get("current_state")
            checked_at = node.get("last_checked")
            if checked_at and (last_check_time is None or checked_at > last_check_time):
                last_check_time = checked_at

            if state == NodeState.OFFLINE.value:
                offline += 1
            if state == NodeState.DERP.value:
                derp += 1
            if state == NodeState.DIRECT.value:
                direct += 1
            if state == NodeState.INACTIVE.value:
                inactive += 1
            if state in {
                NodeState.DIRECT.value,
                NodeState.DERP.value,
                NodeState.PEER_RELAY.value,
                NodeState.INACTIVE.value,
            }:
                online += 1

        return {
            "total_nodes": total_nodes,
            "nodes_online": online,
            "nodes_offline": offline,
            "nodes_on_derp": derp,
            "nodes_on_direct": direct,
            "nodes_inactive": inactive,
            "last_check_time": last_check_time,
        }
