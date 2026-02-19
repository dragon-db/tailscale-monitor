from __future__ import annotations

import httpx


async def send_discord_webhook(webhook_url: str, payload: dict) -> tuple[bool, str | None]:
    timeout = httpx.Timeout(8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                return False, f"Discord webhook failed with HTTP {response.status_code}: {response.text}"
    except Exception as exc:
        return False, str(exc)
    return True, None
