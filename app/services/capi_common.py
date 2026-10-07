"""Shared HTTP sender for the Conversions APIs: 5s timeout, one retry on timeout, result logged to order_events."""

import logging
from dataclasses import dataclass

import httpx

from app.database import async_session
from app.models import OrderEvent

logger = logging.getLogger("capi")

TIMEOUT_SECONDS = 5.0


@dataclass
class CapiResult:
    status: int | None
    body: str


async def post_with_retry(url: str, payload: dict, headers: dict[str, str] | None = None) -> CapiResult:
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload, headers=headers)
            return CapiResult(response.status_code, response.text[:4000])
        except httpx.TimeoutException:
            if attempt == 2:
                return CapiResult(None, "timeout")
            logger.warning("CAPI timeout, retrying once: %s", url.split("?")[0])
        except httpx.HTTPError as exc:
            return CapiResult(None, f"http_error: {exc}")
    return CapiResult(None, "unreachable")


async def record_event(order_id: int, platform: str, event_name: str, event_id: str, result: CapiResult) -> None:
    logger.info("CAPI %s %s order=%s status=%s", platform, event_name, order_id, result.status)
    if result.status is None or result.status >= 400:
        logger.warning("CAPI %s failure body: %s", platform, result.body[:500])
    try:
        async with async_session() as session:
            session.add(
                OrderEvent(
                    order_id=order_id,
                    event_name=event_name,
                    event_id=event_id,
                    platform=platform,
                    response_status=result.status,
                    response_body=result.body,
                )
            )
            await session.commit()
    except Exception as exc:
        logger.error("Could not record order_event: %s", exc)
