"""ntfy push notification helper."""

from typing import Optional


async def send_notification(
    server_url: str,
    topic: str,
    title: str,
    message: str,
    token: Optional[str] = None,
) -> None:
    """Send a push notification via ntfy.

    Silently no-ops when topic is empty, so callers do not need to guard
    against unconfigured notification settings.  The Authorization header is
    included only when a token is provided.
    """
    if not topic:
        return

    import httpx

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{server_url}/{topic}",
            headers=headers,
            json={"title": title, "message": message},
        )
