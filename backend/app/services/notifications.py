"""ntfy push notification helper."""

from typing import Dict, Optional

from app.core.logging import get_logger, log_call

logger = get_logger(__name__)


@log_call
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
        logger.debug("notification skipped: empty topic")
        return

    import httpx

    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.info(f"sending notification to {server_url}/{topic} title={title}")

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{server_url}/{topic}",
                headers=headers,
                json={"title": title, "message": message},
            )
        logger.info(f"notification sent successfully to {topic}")
    except Exception as exc:
        logger.error(f"notification failed to {topic}: {exc}")
