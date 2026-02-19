from __future__ import annotations

import asyncio

import httpx


async def send_discord_webhook(webhook_url: str, payload: dict) -> tuple[bool, str | None]:
    timeout = httpx.Timeout(8.0)
    max_attempts = 3

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            except Exception as exc:
                if attempt >= max_attempts:
                    return False, str(exc)
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code < 400:
                return True, None

            body = response.text[:300] if response.text else ""
            if response.status_code == 429 and attempt < max_attempts:
                retry_after = response.headers.get("Retry-After")
                try:
                    sleep_seconds = float(retry_after) if retry_after else 1.0
                except ValueError:
                    sleep_seconds = 1.0
                await asyncio.sleep(max(0.5, min(sleep_seconds, 10.0)))
                continue

            if 500 <= response.status_code <= 599 and attempt < max_attempts:
                await asyncio.sleep(0.5 * attempt)
                continue

            return False, f"Discord webhook failed with HTTP {response.status_code}: {body}"

    return False, "Discord webhook failed after retries"
