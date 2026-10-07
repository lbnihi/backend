import time

from app.config import settings
from app.services.capi_common import CapiResult, post_with_retry
from app.services.tracking_context import TrackingContext
from app.utils.hashing import sha256_hash
from app.utils.phone import normalize_phone_snap

PLATFORM = "snapchat"


def build_payload(ctx: TrackingContext, event_type: str) -> dict:
    event: dict = {
        "event_type": event_type,
        "event_conversion_type": "WEB",
        "timestamp": int(time.time() * 1000),
        "event_tag": event_type.lower(),
        "uuid_c1": ctx.event_id,  # dedup with web client_dedup_id
        "hashed_phone_number": sha256_hash(normalize_phone_snap(ctx.phone)),  # +966... before hashing
        "hashed_ip_address": sha256_hash(ctx.ip_address),  # Snapchat hashes the IP
        "user_agent": ctx.user_agent,  # not hashed
        "page_url": ctx.page_url,
        "item_ids": ctx.content_ids,
        "price": f"{ctx.value:.2f}",
        "currency": "SAR",
        "number_items": str(ctx.num_items),
        "transaction_id": ctx.order_number,
    }
    if ctx.sclid:
        event["click_id"] = ctx.sclid
    return {"data": [event]}


async def send_event(ctx: TrackingContext, event_type: str = "PURCHASE") -> CapiResult | None:
    if not settings.snap_pixel_id or not settings.snap_access_token:
        return None
    return await post_with_retry(
        f"https://tr.snapchat.com/v3/{settings.snap_pixel_id}/events",
        build_payload(ctx, event_type),
        headers={"Authorization": f"Bearer {settings.snap_access_token}", "Content-Type": "application/json"},
    )
