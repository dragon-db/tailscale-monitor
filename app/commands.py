from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any


async def run_command(command: list[str], timeout_seconds: int) -> tuple[int, str, str]:
    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    try:
        result = await asyncio.to_thread(_run)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout_seconds}s"
    except FileNotFoundError as exc:
        return -1, "", str(exc)
    except Exception as exc:
        return -1, "", str(exc)


def build_tailscale_command(binary: str, socket_path: str, args: list[str]) -> list[str]:
    return [binary, "--socket", socket_path, *args]


async def tailscale_status_json(
    binary: str,
    socket_path: str,
    timeout_seconds: int = 10,
) -> tuple[dict[str, Any] | None, str | None]:
    command = build_tailscale_command(binary, socket_path, ["status", "--json"])
    code, stdout, stderr = await run_command(command, timeout_seconds=timeout_seconds)
    if code != 0:
        return None, stderr.strip() or f"tailscale status failed with exit code {code}"

    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return None, f"Could not parse status JSON: {exc}"


async def tailscale_ping(
    binary: str,
    socket_path: str,
    ip: str,
    count: int,
    timeout_seconds: int,
) -> tuple[str | None, str | None]:
    command = build_tailscale_command(binary, socket_path, ["ping", "-c", str(count), ip])
    code, stdout, stderr = await run_command(command, timeout_seconds=timeout_seconds)
    combined_output = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part).strip()
    if code != 0:
        return combined_output or None, stderr.strip() or f"tailscale ping failed with exit code {code}"
    return combined_output or stdout, None
