from __future__ import annotations

import httpx


async def send_ntfy_message(
    base_url: str,
    topic: str,
    title: str,
    priority: str,
    tags: list[str],
    body: str,
    token: str | None,
) -> tuple[bool, str | None]:
    endpoint = f"{base_url.rstrip('/')}/{topic}"
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": ",".join(tags),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = httpx.Timeout(8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, content=body.encode("utf-8"), headers=headers)
            if response.status_code >= 400:
                return False, f"Ntfy send failed with HTTP {response.status_code}: {response.text}"
    except Exception as exc:
        return False, str(exc)
    return True, None
