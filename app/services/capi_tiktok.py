from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services.capi_common import CapiResult, post_with_retry
from app.services.tracking_context import TrackingContext
from app.utils.hashing import sha256_hash
from app.utils.phone import normalize_phone_tiktok

API_URL = "https://business-api.tiktok.com/open_api/v1.3/event/track/"
PLATFORM = "tiktok"
KSA_TZ = timezone(timedelta(hours=3))


def build_payload(ctx: TrackingContext, event_name: str) -> dict:
    user: dict = {
        "phone_number": sha256_hash(normalize_phone_tiktok(ctx.phone)),  # +966... before hashing
        "external_id": sha256_hash(ctx.order_number),
    }
    if ctx.ttp:
        user["ttp"] = ctx.ttp

    context: dict = {
        "user_agent": ctx.user_agent,  # not hashed
        "ip": ctx.ip_address,  # not hashed
        "page": {"url": ctx.page_url, "referrer": ctx.referrer},
        "user": user,
    }
    if ctx.ttclid:
        context["ad"] = {"callback": ctx.ttclid}

    return {
        "pixel_code": settings.tiktok_pixel_id,
        "partner_name": "qalbalkhalij",
        "event": event_name,
        "event_id": ctx.event_id,
        "timestamp": datetime.now(KSA_TZ).isoformat(timespec="seconds"),
        "context": context,
        "properties": {
            "content_id": ctx.content_ids[0] if ctx.content_ids else "",
            "content_type": "product",
            "quantity": ctx.num_items,
            "value": ctx.value,
            "currency": "SAR",
            "contents": [
                {"content_id": i.slug, "content_type": "product", "quantity": i.quantity, "price": i.unit_price}
                for i in ctx.items
            ],
        },
    }


async def send_event(ctx: TrackingContext, event_name: str = "PlaceAnOrder") -> CapiResult | None:
    if not settings.tiktok_pixel_id or not settings.tiktok_access_token:
        return None
    return await post_with_retry(
        API_URL,
        build_payload(ctx, event_name),
        headers={"Access-Token": settings.tiktok_access_token, "Content-Type": "application/json"},
    )
