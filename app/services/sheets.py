"""Google Sheets webhook (Apps Script). Failures are logged, never raised."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

logger = logging.getLogger("sheets")
KSA_TZ = timezone(timedelta(hours=3))


def _now_ksa() -> str:
    return datetime.now(KSA_TZ).strftime("%Y-%m-%d %H:%M")


def build_order_payload(order_data: dict) -> dict:
    return {
        "type": "order",
        "order_number": order_data["order_number"],
        "order_date": order_data.get("order_date") or _now_ksa(),
        "customer_name": order_data["customer_name"],
        "phone": order_data["phone"],
        "city": order_data.get("city", ""),
        "items": [
            {"product_name": item.get("product_name", ""), "quantity": item.get("quantity", 0)}
            for item in order_data.get("items", [])
        ],
        "subtotal": order_data.get("subtotal", 0),
        "upsell_product": order_data.get("upsell_product", ""),
        "upsell_amount": order_data.get("upsell_amount", 0),
        "total": order_data.get("total", 0),
        "status": "pending",
        "utm_source": order_data.get("utm_source", ""),
        "utm_medium": order_data.get("utm_medium", ""),
        "utm_campaign": order_data.get("utm_campaign", ""),
        "utm_content": order_data.get("utm_content", ""),
        "notes": order_data.get("notes", ""),
    }


async def _post(payload: dict, label: str) -> bool:
    if not settings.sheets_webhook_url:
        logger.warning("SHEETS_WEBHOOK_URL not set — %s not sent", label)
        return False
    try:
        # Apps Script answers with a 302 to googleusercontent.com; follow it to read the result.
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.post(settings.sheets_webhook_url, json=payload)
        ok = response.status_code == 200 and '"success":false' not in response.text.replace(" ", "")
        if ok:
            logger.info("Sheets: %s sent", label)
        else:
            logger.error("Sheets: %s failed %s %s", label, response.status_code, response.text[:300])
        return ok
    except httpx.HTTPError as exc:
        logger.error("Sheets: %s error %s", label, exc)
        return False


async def send_to_sheets(order_data: dict) -> bool:
    return await _post(build_order_payload(order_data), f"order {order_data['order_number']}")


async def send_contact(name: str, phone: str, subject: str, message: str) -> bool:
    payload = {
        "type": "contact",
        "date": _now_ksa(),
        "name": name,
        "phone": phone,
        "subject": subject,
        "message": message,
    }
    return await _post(payload, "contact message")
