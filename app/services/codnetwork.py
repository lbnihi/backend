"""COD Network (seller API v2): push each finalized order as a lead for the confirmation call center.

Docs: https://developer.cod.network/#seller-v2 — Bearer token, POST /v2/seller/orders.
Marketplace (drop) SKUs are refused by /orders (40049) and sent to /leads instead.
Failures are logged and retried, never raised.
"""

import asyncio
import json
import logging

import httpx

from app.catalog import PRODUCTS
from app.config import settings
from app.database import async_session
from app.models import Order

logger = logging.getLogger("codnetwork")

DUPLICATE_LEAD = "20000"
# /orders refuses marketplace (dropshipping) SKUs; those go to /leads and COD Network creates the order.
DROP_PRODUCT = "40049"
CITY_PLACEHOLDER = "يتم التأكيد بالاتصال"
ADDRESS_PLACEHOLDER = "يتم تأكيد العنوان بالاتصال"
RETRY_DELAYS = (0, 5, 30)


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.codnetwork_api_token.strip()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def build_payload(order: Order, only: list[dict] | None = None, reference: str = "", note: str = "") -> dict | None:
    """Lead payload, or None when no item has a COD Network SKU yet. `only` = send just these items (add-ons)."""
    skus = settings.codnetwork_sku_map
    items = []
    for item in only if only is not None else order.items:
        sku = skus.get(item["product_slug"])
        if not sku:
            continue
        items.append(
            {
                "sku": sku,
                "name": item["product_name"],
                "quantity": item["quantity"],
                "price": item["total_price"],
            }
        )
    if only is None and order.upsell_accepted and order.upsell_product_slug and skus.get(order.upsell_product_slug):
        items.append(
            {
                "sku": skus[order.upsell_product_slug],
                "name": PRODUCTS[order.upsell_product_slug].name,
                "quantity": 1,
                "price": float(order.upsell_amount),
            }
        )
    if not items:
        return None
    city = order.city if order.city not in (None, "", "Unknown", "Test") else CITY_PLACEHOLDER
    return {
        "reference": reference or order.order_number,
        "full_name": order.customer_name,
        "phone": order.phone,
        "country": settings.codnetwork_country,
        # The site doesn't ask for an address: the call center confirms it on the call.
        # COD Network requires city/area/address, so send the IP city when known and a clear placeholder.
        "city": city,
        "area": city,
        "address": ADDRESS_PLACEHOLDER,
        "currency": settings.codnetwork_currency,
        "total": float(order.total) if only is None else float(sum(i["total_price"] for i in only)),
        "items": items,
        "note": note or f"طلب من الموقع {order.order_number}",
    }


def _enabled_for(order: Order) -> bool:
    if not settings.codnetwork_api_token.strip():
        return False
    if order.phone in settings.whitelist and not settings.codnetwork_send_test_orders:
        logger.info("COD Network: %s is a test order (whitelisted phone) — not sent", order.order_number)
        return False
    return True


def _remote_id(response: httpx.Response, fallback: str) -> str:
    try:
        data = response.json().get("data") or {}
    except ValueError:
        data = {}
    return str((data.get("id") if isinstance(data, dict) else "") or fallback)


async def _deliver(label: str, payload: dict) -> str | None:
    """POST to /orders (drop products → /leads). Returns the COD Network id ("lead:…", "duplicate") or None."""
    base = settings.codnetwork_base_url.rstrip("/")
    url = f"{base}/v2/seller/orders"
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with _http() as client:
                response = await client.post(url, headers=_headers(), json=payload)
        except httpx.HTTPError as exc:
            logger.error("COD Network: %s attempt %d network error %s", label, attempt, exc)
            continue

        body = response.text[:1000]
        if response.is_success:
            remote_id = _remote_id(response, "sent")
            logger.info("COD Network: %s sent, id=%s", label, remote_id)
            return remote_id[:64]
        if DROP_PRODUCT in body and url.endswith("/orders"):
            logger.info("COD Network: %s is a drop product — sending as a lead", label)
            url = f"{base}/v2/seller/leads"
            async with _http() as client:
                response = await client.post(url, headers=_headers(), json=payload)
            body = response.text[:1000]
            if response.is_success:
                remote_id = _remote_id(response, "lead")
                logger.info("COD Network: %s sent as lead, id=%s", label, remote_id)
                return f"lead:{remote_id}"[:64]
        if DUPLICATE_LEAD in body:
            logger.info("COD Network: %s already exists there", label)
            return "duplicate"
        if response.status_code < 500:
            # Validation/auth error: retrying won't help. The body says which field is wrong.
            logger.error(
                "COD Network: %s rejected %s %s | payload=%s",
                label, response.status_code, body, json.dumps(payload, ensure_ascii=False),
            )
            return None
        logger.error("COD Network: %s attempt %d failed %s %s", label, attempt, response.status_code, body)
    return None


async def send_order(order_id: int) -> None:
    """Send once (the COD Network id is stored on the order); retry on network/5xx errors."""
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if order is None or order.codnetwork_order_id or not _enabled_for(order):
            return
        payload = build_payload(order)
        if payload is None:
            logger.warning("COD Network: %s has no product with a SKU in CODNETWORK_SKUS — not sent", order.order_number)
            return
        remote_id = await _deliver(order.order_number, payload)
        if remote_id:
            order.codnetwork_order_id = remote_id
            await session.commit()


async def send_addon(order_id: int, product_slug: str) -> None:
    """Thank-you add-on after the order already reached COD Network: send it as its own lead, flagged as
    part of the same order. If the order wasn't sent yet, send_order will include it — nothing to do."""
    async with async_session() as session:
        order = await session.get(Order, order_id)
        if order is None or not order.codnetwork_order_id or not _enabled_for(order):
            return
        item = next((i for i in order.items if i.get("addon") and i["product_slug"] == product_slug), None)
        if item is None or item.get("codnetwork_id"):
            return
        payload = build_payload(
            order,
            only=[item],
            reference=f"{order.order_number}-{product_slug}"[:60],
            note=f"إضافة على الطلب {order.order_number} — نفس العميلة ونفس التوصيل. اتصال واحد للطلبين.",
        )
        if payload is None:
            logger.warning("COD Network: add-on %s on %s has no SKU — not sent", product_slug, order.order_number)
            return
        remote_id = await _deliver(f"{order.order_number} add-on {product_slug}", payload)
        if remote_id:
            order.items = [{**i, "codnetwork_id": remote_id} if i is item else i for i in order.items]
            await session.commit()
