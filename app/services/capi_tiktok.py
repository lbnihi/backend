from datetime import datetime, timezone

from app.config import settings
from app.services.capi_common import CapiResult, post_with_retry
from app.services.tracking_context import TrackingContext
from app.utils.hashing import sha256_hash
from app.utils.phone import normalize_phone_tiktok

API_URL = "https://business-api.tiktok.com/open_api/v1.3/event/track/"
PLATFORM = "tiktok"


def build_payload(ctx: TrackingContext, event_name: str) -> dict:
    """Events API v1.3: pixel in event_source_id, events in data[] (the v1.2 pixel_code/context shape is rejected)."""
    user: dict = {
        "phone": sha256_hash(normalize_phone_tiktok(ctx.phone)),  # +966... before hashing
        "external_id": sha256_hash(ctx.order_number),
        "ip": ctx.ip_address,  # not hashed
        "user_agent": ctx.user_agent,  # not hashed
    }
    if ctx.ttclid:
        user["ttclid"] = ctx.ttclid
    if ctx.ttp:
        user["ttp"] = ctx.ttp

    return {
        "event_source": "web",
        "event_source_id": settings.tiktok_pixel_id,
        "data": [
            {
                "event": event_name,
                "event_time": int(datetime.now(timezone.utc).timestamp()),
                "event_id": ctx.event_id,
                "user": user,
                "page": {"url": ctx.page_url, "referrer": ctx.referrer},
                "properties": {
                    "content_type": "product",
                    "currency": "SAR",
                    "value": ctx.value,
                    "order_id": ctx.order_number,
                    "contents": [
                        {"content_id": i.slug, "quantity": i.quantity, "price": i.unit_price} for i in ctx.items
                    ],
                },
            }
        ],
    }


async def send_event(ctx: TrackingContext, event_name: str = "PlaceAnOrder") -> CapiResult | None:
    if not settings.tiktok_pixel_id or not settings.tiktok_access_token:
        return None
    return await post_with_retry(
        API_URL,
        build_payload(ctx, event_name),
        headers={"Access-Token": settings.tiktok_access_token, "Content-Type": "application/json"},
    )
