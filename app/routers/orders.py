import asyncio
from datetime import datetime, timedelta, timezone
import logging
import secrets
import string
from decimal import Decimal

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.catalog import (
    ADDON_PRICE,
    ADDON_WINDOW_HOURS,
    MAX_ADDONS,
    OFFERS,
    PRODUCTS,
    UPSELL_PRICE,
    pick_upsell,
    upsell_payload,
)
from app.config import settings
from app.database import async_session, get_db
from app.errors import ApiError
from app.models import Order
from app.schemas import AddonRequest, CreateOrderRequest, UpsellRequest
from app.services import capi, codnetwork, maxmind, rate_limit, sheets
from app.services.background import fire_and_forget
from app.services.tracking_context import TrackedItem, TrackingContext
from app.utils.network import get_client_ip
from app.utils.phone import mask_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["orders"])

ORDER_ALPHABET = string.ascii_uppercase + string.digits
# If the customer leaves during the 15s upsell, the order is still finalized and sent to Sheets.
UPSELL_FINALIZE_AFTER_SECONDS = 60


def generate_order_number() -> str:
    return "QAK-" + "".join(secrets.choice(ORDER_ALPHABET) for _ in range(5))


async def unique_order_number(db: AsyncSession) -> str:
    for _ in range(20):
        candidate = generate_order_number()
        exists = await db.scalar(select(Order.id).where(Order.order_number == candidate))
        if exists is None:
            return candidate
    raise ApiError(500, "server_error", "صار خطأ غير متوقع، حاولي مرة ثانية", "Could not allocate order number")


def money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def sheet_data(order: Order) -> dict:
    upsell_name = PRODUCTS[order.upsell_product_slug].name if order.upsell_accepted and order.upsell_product_slug else ""
    return {
        "order_number": order.order_number,
        "order_date": order.created_at.astimezone(sheets.KSA_TZ).strftime("%Y-%m-%d %H:%M") if order.created_at else "",
        "customer_name": order.customer_name,
        "phone": order.phone,
        "city": order.city or "",
        "items": order.items,
        "subtotal": money(order.subtotal),
        "upsell_product": upsell_name,
        "upsell_amount": money(order.upsell_amount) if order.upsell_accepted else 0,
        "total": money(order.total),
        "utm_source": order.utm_source or "",
        "utm_medium": order.utm_medium or "",
        "utm_campaign": order.utm_campaign or "",
        "utm_content": order.utm_content or "",
        "notes": addon_note(order),
    }


def addon_note(order: Order) -> str:
    """Tells the confirmation team which items were added from the thank-you page."""
    added = [i["product_name"] for i in order.items if i.get("addon")]
    return f"أضافت بعد الطلب: {'، '.join(added)}" if added else ""


async def finalize_if_undecided(order_id: int) -> None:
    await asyncio.sleep(UPSELL_FINALIZE_AFTER_SECONDS)
    async with async_session() as session:
        result = await session.execute(
            update(Order).where(Order.id == order_id, Order.upsell_decided.is_(False)).values(upsell_decided=True)
        )
        await session.commit()
        if result.rowcount == 0:
            return  # customer already decided; that request sent the order to Sheets
        order = await session.get(Order, order_id)
        if order is not None:
            logger.info("Order %s finalized without an upsell decision", order.order_number)
            await sheets.send_to_sheets(sheet_data(order))
            await codnetwork.send_order(order.id)


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(body: CreateOrderRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    ip_address = get_client_ip(request)
    whitelisted = body.phone in settings.whitelist

    if not whitelisted and not rate_limit.check_order_limits(ip_address, body.phone):
        logger.info("rate_limited ip=%s phone=%s", ip_address, mask_phone(body.phone))
        raise ApiError(429, *errors.RATE_LIMITED)

    geo = await maxmind.validate_ip(ip_address, body.phone)
    if not geo.allowed:
        if geo.reason in maxmind.VPN_REASONS:
            raise ApiError(403, *errors.VPN_DETECTED)
        if geo.reason == "high_risk":
            raise ApiError(403, *errors.SUSPICIOUS_IP)
        if geo.reason == "ip_not_found":
            raise ApiError(403, *errors.IP_NOT_FOUND)
        if geo.reason.startswith("maxmind_"):
            raise ApiError(503, *errors.VERIFICATION_UNAVAILABLE)
        raise ApiError(403, *errors.GEO_BLOCKED)

    # Prices come from the catalog, never from the client.
    items: list[dict] = []
    tracked: list[TrackedItem] = []
    for item in body.items:
        price, label = OFFERS[item.quantity]
        unit = (price / item.quantity).quantize(Decimal("0.01"))
        items.append(
            {
                "product_slug": item.product_slug,
                "product_name": PRODUCTS[item.product_slug].name,
                "quantity": item.quantity,
                "unit_price": money(unit),
                "total_price": money(price),
                "offer_label": label,
            }
        )
        tracked.append(TrackedItem(item.product_slug, item.quantity, money(unit), money(price)))
    subtotal = sum((OFFERS[i.quantity][0] for i in body.items), Decimal("0"))

    cart_slugs = [i.product_slug for i in body.items]
    upsell_slug = pick_upsell(cart_slugs)
    user_agent = body.user_agent or request.headers.get("user-agent", "")

    order = Order(
        order_number=await unique_order_number(db),
        customer_name=body.customer_name,
        phone=body.phone,
        ip_address=ip_address,
        country_code=(geo.country or None) and geo.country[:2],
        city=geo.city or None,
        is_vpn=geo.is_vpn,
        items=items,
        subtotal=subtotal,
        total=subtotal,
        upsell_product_slug=upsell_slug,
        event_id=body.event_id or None,
        utm_source=body.utm_source or None,
        utm_medium=body.utm_medium or None,
        utm_campaign=body.utm_campaign or None,
        utm_content=body.utm_content or None,
        utm_term=body.utm_term or None,
        fbclid=body.fbclid or None,
        ttclid=body.ttclid or None,
        sclid=body.sclid or None,
        fbc=body.fbc or None,
        fbp=body.fbp or None,
        ttp=body.ttp or None,
        user_agent=user_agent[:1024],
        page_url=body.page_url or None,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    if not whitelisted:
        rate_limit.record_order(ip_address, body.phone)

    # Geo city is only meaningful for real lookups.
    capi_city = geo.city if geo.reason == "valid" and geo.city != "Unknown" else ""
    fire_and_forget(
        capi.fire_purchase(
            TrackingContext(
                order_id=order.id,
                order_number=order.order_number,
                event_id=body.event_id,
                phone=body.phone,
                customer_name=body.customer_name,
                city=capi_city,
                ip_address=ip_address,
                user_agent=user_agent,
                page_url=body.page_url or settings.site_url,
                referrer=body.referrer,
                fbc=body.fbc,
                fbp=body.fbp,
                ttclid=body.ttclid,
                ttp=body.ttp,
                sclid=body.sclid,
                items=tracked,
            )
        )
    )
    # Sheets waits for the upsell decision (or this fallback).
    fire_and_forget(finalize_if_undecided(order.id))

    return {
        "success": True,
        "order": {
            "id": order.id,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "phone": order.phone,
            "items": order.items,
            "subtotal": money(order.subtotal),
            "total": money(order.total),
            "status": order.status,
            "city": order.city,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        },
        "upsell": upsell_payload(upsell_slug),
    }


@router.post("/orders/{order_id}/addons")
async def add_to_order(order_id: int, body: AddonRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Thank-you page: add one pack of another product to the same order (same delivery, same call)."""
    order = await db.get(Order, order_id)
    if order is None or not order.event_id or not secrets.compare_digest(order.event_id, body.order_event_id):
        raise ApiError(404, "not_found", "الطلب غير موجود", "Order not found")
    if body.product_slug not in PRODUCTS:
        raise ApiError(422, "invalid_product", "المنتج غير متوفر", "Unknown product")
    created = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=timezone.utc)
    if order.status != "pending" or datetime.now(timezone.utc) - created > timedelta(hours=ADDON_WINDOW_HOURS):
        raise ApiError(409, "addon_closed", "انتهى وقت الإضافة على هذا الطلب", "Order can no longer be changed")
    if any(i["product_slug"] == body.product_slug for i in order.items) or (
        order.upsell_accepted and order.upsell_product_slug == body.product_slug
    ):
        raise ApiError(409, "already_in_order", "المنتج موجود في طلبك", "Product already in order")
    if sum(1 for i in order.items if i.get("addon")) >= MAX_ADDONS:
        raise ApiError(409, "addon_limit", "وصلتي للحد الأقصى للإضافات", "Add-on limit reached")

    price = money(ADDON_PRICE)
    order.items = [
        *order.items,
        {
            "product_slug": body.product_slug,
            "product_name": PRODUCTS[body.product_slug].name,
            "quantity": 1,
            "unit_price": price,
            "total_price": price,
            "offer_label": "إضافة على الطلب",
            "addon": True,
        },
    ]
    order.subtotal = order.subtotal + ADDON_PRICE
    order.total = order.total + ADDON_PRICE
    await db.commit()
    await db.refresh(order)

    fire_and_forget(
        capi.fire_purchase(
            TrackingContext(
                order_id=order.id,
                order_number=order.order_number,
                event_id=body.event_id,
                phone=order.phone,
                customer_name=order.customer_name,
                city=order.city if order.city not in (None, "Unknown", "Test") else "",
                ip_address=order.ip_address or get_client_ip(request),
                user_agent=body.user_agent or order.user_agent or "",
                page_url=body.page_url or order.page_url or settings.site_url,
                referrer="",
                fbc=order.fbc or "",
                fbp=order.fbp or "",
                ttclid=order.ttclid or "",
                ttp=order.ttp or "",
                sclid=order.sclid or "",
                items=[TrackedItem(body.product_slug, 1, price, price)],
            )
        )
    )
    # Sheet rows are upserted by order number. Before the upsell decision, that step sends the row.
    if order.upsell_decided:
        fire_and_forget(sheets.send_to_sheets(sheet_data(order)))

    return {"success": True, "order": {"order_number": order.order_number, "total": money(order.total)}}


@router.post("/orders/{order_id}/upsell")
async def handle_upsell(order_id: int, body: UpsellRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    order = await db.get(Order, order_id)
    if order is None:
        raise ApiError(404, "not_found", "الطلب غير موجود", "Order not found")

    # Atomic claim: only the first decision counts (double clicks, fallback finalizer).
    claimed = await db.execute(
        update(Order).where(Order.id == order_id, Order.upsell_decided.is_(False)).values(upsell_decided=True)
    )
    if claimed.rowcount == 0:
        await db.rollback()
        raise ApiError(409, "upsell_closed", "انتهى وقت العرض", "Upsell already decided")

    accepted = body.accepted and body.product_slug == order.upsell_product_slug
    if accepted:
        order.upsell_accepted = True
        order.upsell_amount = UPSELL_PRICE
        order.total = order.subtotal + UPSELL_PRICE
    await db.commit()
    await db.refresh(order)

    if accepted and order.upsell_product_slug:
        upsell_price = money(UPSELL_PRICE)
        fire_and_forget(
            capi.fire_purchase(
                TrackingContext(
                    order_id=order.id,
                    order_number=order.order_number,
                    event_id=body.event_id,
                    phone=order.phone,
                    customer_name=order.customer_name,
                    city=order.city if order.city not in (None, "Unknown", "Test") else "",
                    ip_address=order.ip_address or get_client_ip(request),
                    user_agent=body.user_agent or order.user_agent or "",
                    page_url=body.page_url or order.page_url or settings.site_url,
                    referrer="",
                    fbc=order.fbc or "",
                    fbp=order.fbp or "",
                    ttclid=order.ttclid or "",
                    ttp=order.ttp or "",
                    sclid=order.sclid or "",
                    items=[TrackedItem(order.upsell_product_slug, 1, upsell_price, upsell_price)],
                )
            )
        )

    fire_and_forget(sheets.send_to_sheets(sheet_data(order)))
    fire_and_forget(codnetwork.send_order(order.id))

    response: dict = {
        "order_number": order.order_number,
        "total": money(order.total),
        "upsell_accepted": order.upsell_accepted,
    }
    if order.upsell_accepted and order.upsell_product_slug:
        response["upsell_product"] = PRODUCTS[order.upsell_product_slug].name
        response["upsell_amount"] = money(order.upsell_amount)
    return {"success": True, "order": response}


@router.get("/orders/{order_number}")
async def get_order(order_number: str, db: AsyncSession = Depends(get_db)) -> dict:
    order = await db.scalar(select(Order).where(Order.order_number == order_number.upper()[:20]))
    if order is None:
        raise ApiError(404, "not_found", "الطلب غير موجود", "Order not found")

    return {
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "items": [
            {
                "product_slug": i["product_slug"],
                "product_name": i["product_name"],
                "quantity": i["quantity"],
                "total_price": i["total_price"],
                "offer_label": i.get("offer_label", ""),
            }
            for i in order.items
        ],
        "upsell_product": PRODUCTS[order.upsell_product_slug].name
        if order.upsell_accepted and order.upsell_product_slug
        else "",
        "upsell_amount": money(order.upsell_amount) if order.upsell_accepted else 0,
        "upsell_accepted": order.upsell_accepted,
        "subtotal": money(order.subtotal),
        "total": money(order.total),
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }
