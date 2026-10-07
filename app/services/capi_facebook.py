import time

from app.config import settings
from app.services.capi_common import CapiResult, post_with_retry
from app.services.tracking_context import TrackingContext
from app.utils.hashing import normalize_city, sha256_hash
from app.utils.phone import normalize_phone_fb

API_VERSION = "v21.0"
PLATFORM = "facebook"


def build_payload(ctx: TrackingContext, event_name: str) -> dict:
    parts = ctx.customer_name.split(maxsplit=1)
    user_data: dict = {
        "ph": [sha256_hash(normalize_phone_fb(ctx.phone))],
        "country": [sha256_hash("sa")],
        "client_ip_address": ctx.ip_address,  # raw, not hashed
        "client_user_agent": ctx.user_agent,  # raw, not hashed
    }
    if parts:
        user_data["fn"] = [sha256_hash(parts[0])]
    if len(parts) > 1:
        user_data["ln"] = [sha256_hash(parts[1])]
    if ctx.city:
        user_data["ct"] = [sha256_hash(normalize_city(ctx.city))]
    if ctx.fbc:
        user_data["fbc"] = ctx.fbc
    if ctx.fbp:
        user_data["fbp"] = ctx.fbp

    return {
        "data": [
            {
                "event_name": event_name,
                "event_time": int(time.time()),
                "event_id": ctx.event_id,
                "event_source_url": ctx.page_url,
                "action_source": "website",
                "user_data": user_data,
                "custom_data": {
                    "content_ids": ctx.content_ids,
                    "content_type": "product",
                    "contents": [
                        {"id": i.slug, "quantity": i.quantity, "item_price": i.unit_price} for i in ctx.items
                    ],
                    "value": ctx.value,
                    "currency": "SAR",
                    "num_items": ctx.num_items,
                },
            }
        ],
        "access_token": settings.fb_access_token,
    }


async def send_event(ctx: TrackingContext, event_name: str = "Purchase") -> CapiResult | None:
    if not settings.fb_pixel_id or not settings.fb_access_token:
        return None
    url = f"https://graph.facebook.com/{API_VERSION}/{settings.fb_pixel_id}/events"
    return await post_with_retry(url, build_payload(ctx, event_name))
