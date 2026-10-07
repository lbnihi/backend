"""Fan-out of a purchase to the three Conversions APIs. Never raises — orders must not depend on ads."""

import asyncio
import logging

from app.services import capi_facebook, capi_snapchat, capi_tiktok
from app.services.capi_common import record_event
from app.services.tracking_context import TrackingContext

logger = logging.getLogger("capi")


async def _one(platform: str, event_name: str, ctx: TrackingContext, send) -> None:
    try:
        result = await send(ctx, event_name)
        if result is not None:
            await record_event(ctx.order_id, platform, event_name, ctx.event_id, result)
    except Exception as exc:
        logger.exception("CAPI %s crashed: %s", platform, exc)


async def fire_purchase(ctx: TrackingContext) -> None:
    if not ctx.event_id:
        logger.warning("Order %s has no event_id — CAPI events cannot be deduplicated", ctx.order_number)
    await asyncio.gather(
        _one(capi_facebook.PLATFORM, "Purchase", ctx, capi_facebook.send_event),
        _one(capi_tiktok.PLATFORM, "PlaceAnOrder", ctx, capi_tiktok.send_event),
        _one(capi_snapchat.PLATFORM, "PURCHASE", ctx, capi_snapchat.send_event),
    )
