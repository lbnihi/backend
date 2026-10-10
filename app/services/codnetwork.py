"""COD Network (seller API v2): push each finalized order as a lead for the confirmation call center.

Docs: https://developer.cod.network/#seller-v2 — Bearer token, POST /v2/seller/orders.
The exact create-order contract is published at GET /v2/seller/public/descriptor/orders; it is logged once
at startup so the payload below can be checked against it. Failures are logged and retried, never raised.
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
RETRY_DELAYS = (0, 5, 30)


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.codnetwork_api_token.strip()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def build_payload(order: Order) -> dict | None:
    """Lead payload, or None when no item has a COD Network SKU yet."""
    skus = settings.codnetwork_sku_map
    items = []
    for item in order.items:
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
    if order.upsell_accepted and order.upsell_product_slug and skus.get(order.upsell_product_slug):
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
    return {
        "reference": order.order_number,
        "full_name": order.customer_name,
        "phone": order.phone,
        "country": settings.codnetwork_country,
        "city": order.city if order.city not in (None, "Unknown", "Test") else "",
        "address": "",
        "currency": settings.codnetwork_currency,
        "total": float(order.total),
        "items": items,
        "note": f"طلب من الموقع {order.order_number}",
    }


def _enabled_for(order: Order) -> bool:
    if not settings.codnetwork_api_token.strip():
        return False
    if order.phone in settings.whitelist and not settings.codnetwork_send_test_orders:
        logger.info("COD Network: %s is a test order (whitelisted phone) — not sent", order.order_number)
        return False
    return True


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

        url = f"{settings.codnetwork_base_url.rstrip('/')}/v2/seller/orders"
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with _http() as client:
                    response = await client.post(url, headers=_headers(), json=payload)
            except httpx.HTTPError as exc:
                logger.error("COD Network: %s attempt %d network error %s", order.order_number, attempt, exc)
                continue

            body = response.text[:1000]
            if response.is_success:
                try:
                    data = response.json().get("data") or {}
                except ValueError:
                    data = {}
                remote_id = str((data.get("id") if isinstance(data, dict) else "") or "sent")
                order.codnetwork_order_id = remote_id[:64]
                await session.commit()
                logger.info("COD Network: %s sent, id=%s", order.order_number, remote_id)
                return
            if DUPLICATE_LEAD in body:
                order.codnetwork_order_id = "duplicate"
                await session.commit()
                logger.info("COD Network: %s already exists there", order.order_number)
                return
            if response.status_code < 500:
                # Validation/auth error: retrying won't help. The body says which field is wrong.
                logger.error(
                    "COD Network: %s rejected %s %s | payload=%s",
                    order.order_number, response.status_code, body, json.dumps(payload, ensure_ascii=False),
                )
                return
            logger.error("COD Network: %s attempt %d failed %s %s", order.order_number, attempt, response.status_code, body)


async def log_order_contract() -> None:
    """Startup: print COD Network's own create-order contract once, to check the payload fields."""
    if not settings.codnetwork_api_token.strip():
        return
    url = f"{settings.codnetwork_base_url.rstrip('/')}/v2/seller/public/descriptor/orders"
    try:
        async with _http() as client:
            response = await client.get(url, headers=_headers())
        logger.info("COD Network descriptor/orders %s: %s", response.status_code, response.text[:6000])
    except httpx.HTTPError as exc:
        logger.warning("COD Network descriptor unavailable: %s", exc)
