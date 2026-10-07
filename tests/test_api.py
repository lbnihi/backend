import asyncio
import hashlib

import httpx
import pytest

from app.main import app
from app.routers import orders as orders_router
from app.services import capi_common, maxmind, rate_limit, sheets
from app.services.maxmind import GeoResult

pytestmark = pytest.mark.asyncio

SA_IP = "185.1.1.1"
PHONE = "0551234567"


def sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """Capture CAPI + Sheets traffic, make geo deterministic, no 60s sleeps."""
    calls: dict[str, list] = {"capi": [], "sheets": []}

    async def fake_post(url, payload, headers=None):
        calls["capi"].append((url, payload, headers))
        return capi_common.CapiResult(200, "{}")

    async def fake_sheets(data):
        calls["sheets"].append(data)
        return True

    async def fake_geo(ip, phone):
        if phone in maxmind.settings.whitelist:
            return GeoResult(True, "whitelisted", "SA", "Test")
        table = {
            SA_IP: GeoResult(True, "valid", "SA", "Riyadh", "STC"),
            "8.8.8.8": GeoResult(False, "country_US", "US", "X"),
            "5.5.5.5": GeoResult(False, "suspicious_asn", "SA", "Riyadh", "M247 VPN", True),
        }
        return table.get(ip, GeoResult(False, "ip_not_found"))

    monkeypatch.setattr(capi_common, "post_with_retry", fake_post)
    for mod in ("capi_facebook", "capi_tiktok", "capi_snapchat"):
        monkeypatch.setattr(f"app.services.{mod}.post_with_retry", fake_post)
    monkeypatch.setattr(sheets, "send_to_sheets", fake_sheets)
    monkeypatch.setattr(maxmind, "validate_ip", fake_geo)
    monkeypatch.setattr(orders_router, "UPSELL_FINALIZE_AFTER_SECONDS", 1.0)
    rate_limit.limiter.reset()
    return calls


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def order_body(**overrides) -> dict:
    body = {
        "customer_name": "نورة أحمد",
        "phone": PHONE,
        "items": [
            {
                "product_slug": "turmeric-golden",
                "product_name": "x",
                "quantity": 3,
                "unit_price": 1,
                "total_price": 1,  # tampered price must be ignored
                "offer_label": "٣ قطع",
            }
        ],
        "subtotal": 1,
        "total": 1,
        "event_id": "evt_abc",
        "page_url": "https://qaalbalkhalij.store/products/turmeric-golden",
        "user_agent": "UA",
        "utm_source": "snapchat",
        "ttclid": "TTC",
        "sclid": "SC",
        "fbc": "fb.1.1.abc",
        "fbp": "fb.1.1.123",
        "ttp": "TTP",
    }
    body.update(overrides)
    return body


async def settle():
    for _ in range(10):
        await asyncio.sleep(0.05)


async def test_health():
    async with client() as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["database"] == "connected"


async def test_full_flow_with_upsell(stubs):
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(), headers={"X-Forwarded-For": f"{SA_IP}, 10.0.0.1"})
        assert r.status_code == 201, r.text
        data = r.json()
        order = data["order"]
        assert order["order_number"].startswith("QAK-") and len(order["order_number"]) == 9
        assert order["total"] == 349.0  # server price, not the tampered 1
        assert order["city"] == "Riyadh"
        assert data["upsell"] == {
            "product_slug": "hyaluronic-acid",
            "product_name": "كبسولات حمض الهيالورونيك",
            "original_price": 199.0,
            "offer_price": 99.0,
            "tagline": "ركب أخف وبشرة تشع... بكبسولتين في اليوم",
        }
        await settle()

        # CAPI: three platforms, same event_id, correct hashing per platform
        by_host = {httpx.URL(u).host: p for u, p, _ in stubs["capi"]}
        fb = by_host["graph.facebook.com"]["data"][0]
        assert fb["event_name"] == "Purchase" and fb["event_id"] == "evt_abc"
        assert fb["user_data"]["ph"] == [sha("966551234567")]
        assert fb["user_data"]["country"] == [sha("sa")]
        assert fb["user_data"]["ct"] == [sha("riyadh")]
        assert fb["user_data"]["client_ip_address"] == SA_IP
        assert fb["user_data"]["fbc"] == "fb.1.1.abc"
        assert fb["custom_data"]["contents"] == [{"id": "turmeric-golden", "quantity": 3, "item_price": 116.33}]
        assert fb["custom_data"]["value"] == 349.0

        tt = by_host["business-api.tiktok.com"]
        assert tt["event"] == "PlaceAnOrder" and tt["event_id"] == "evt_abc"
        assert tt["context"]["user"]["phone_number"] == sha("+966551234567")
        assert tt["context"]["ip"] == SA_IP and tt["context"]["ad"] == {"callback": "TTC"}
        assert tt["timestamp"].endswith("+03:00")

        sn = by_host["tr.snapchat.com"]["data"][0]
        assert sn["event_type"] == "PURCHASE" and sn["uuid_c1"] == "evt_abc"
        assert sn["hashed_phone_number"] == sha("+966551234567")
        assert sn["hashed_ip_address"] == sha(SA_IP)
        assert sn["price"] == "349.00" and sn["number_items"] == "3"
        assert sn["transaction_id"] == order["order_number"] and sn["click_id"] == "SC"

        assert stubs["sheets"] == []  # not before the upsell decision
        stubs["capi"].clear()

        r = await c.post(
            f"/api/orders/{order['id']}/upsell",
            json={"accepted": True, "product_slug": "hyaluronic-acid", "amount": 1, "event_id": "evt_up"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["order"] == {
            "order_number": order["order_number"],
            "total": 448.0,
            "upsell_accepted": True,
            "upsell_product": "كبسولات حمض الهيالورونيك",
            "upsell_amount": 99.0,
        }
        await asyncio.sleep(1.3)  # past the fallback finalizer

        assert len(stubs["sheets"]) == 1  # decision sends once; fallback finalizer is a no-op
        row = stubs["sheets"][0]
        assert row["total"] == 448.0 and row["upsell_amount"] == 99.0 and row["utm_source"] == "snapchat"
        assert {httpx.URL(u).host for u, _, _ in stubs["capi"]} == {
            "graph.facebook.com", "business-api.tiktok.com", "tr.snapchat.com"
        }
        assert all("evt_up" in str(p) for _, p, _ in stubs["capi"])

        # second decision rejected
        r = await c.post(f"/api/orders/{order['id']}/upsell", json={"accepted": True, "product_slug": "hyaluronic-acid"})
        assert r.status_code == 409

        r = await c.get(f"/api/orders/{order['order_number']}")
        assert r.status_code == 200
        assert r.json()["total"] == 448.0 and r.json()["upsell_accepted"] is True


async def test_abandoned_upsell_still_reaches_sheets(stubs):
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(phone="0559999999"), headers={"X-Forwarded-For": SA_IP})
        assert r.status_code == 201
    await asyncio.sleep(1.3)
    assert len(stubs["sheets"]) == 1 and stubs["sheets"][0]["total"] == 349.0


async def test_upsell_skips_products_already_in_cart():
    items = [
        {"product_slug": "turmeric-golden", "quantity": 1},
        {"product_slug": "hyaluronic-acid", "quantity": 1},
    ]
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=items, phone="0558888888"), headers={"X-Forwarded-For": SA_IP})
    assert r.status_code == 201
    assert r.json()["upsell"]["product_slug"] == "thyme-blackseed"
    assert r.json()["order"]["total"] == 398.0


@pytest.mark.parametrize(
    "ip,code,message",
    [
        ("8.8.8.8", "geo_blocked", "عذراً، الخدمة متاحة فقط داخل المملكة العربية السعودية"),
        ("5.5.5.5", "vpn_detected", "عذراً، يرجى تعطيل VPN والمحاولة مرة أخرى"),
        ("9.9.9.9", "ip_not_found", "عذراً، لم نتمكن من التحقق من موقعك"),
    ],
)
async def test_geo_blocks(ip, code, message, stubs):
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(), headers={"X-Forwarded-For": ip})
    assert r.status_code == 403
    assert r.json() == {"error": True, "code": code, "message": message, "message_en": r.json()["message_en"]}
    await settle()
    assert stubs["capi"] == []


async def test_whitelist_bypasses_geo_and_rate_limits():
    async with client() as c:
        for _ in range(5):
            r = await c.post("/api/orders", json=order_body(phone="0550000000"), headers={"X-Forwarded-For": "8.8.8.8"})
            assert r.status_code == 201


async def test_rate_limit_per_phone():
    async with client() as c:
        statuses = [
            (await c.post("/api/orders", json=order_body(phone="0557777777"), headers={"X-Forwarded-For": SA_IP})).status_code
            for _ in range(4)
        ]
    assert statuses == [201, 201, 201, 429]


async def test_validation_errors():
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(phone="0612345678"))
        assert r.status_code == 422
        assert r.json()["code"] == "validation_error" and r.json()["message"] == "رقم الجوال غير صحيح"
        assert r.json()["details"][0]["field"] == "phone"

        r = await c.post("/api/orders", json=order_body(items=[{"product_slug": "fake", "quantity": 1}]))
        assert r.status_code == 422

        r = await c.post("/api/orders", json=order_body(customer_name="<b>x</b>"))
        assert r.status_code == 422


async def test_arabic_digits_phone_accepted():
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(phone="٠٥٥٦٦٦٦٦٦٦"), headers={"X-Forwarded-For": SA_IP})
    assert r.status_code == 201 and r.json()["order"]["phone"] == "0556666666"


async def test_contact(monkeypatch):
    sent = []

    async def fake_contact(*args):
        sent.append(args)
        return True

    monkeypatch.setattr(sheets, "send_contact", fake_contact)
    async with client() as c:
        r = await c.post(
            "/api/contact",
            json={"name": "سارة", "phone": "0551112222", "subject": "اقتراح", "message": "عندي اقتراح بسيط"},
        )
    await settle()
    assert r.status_code == 200 and sent


async def test_not_found():
    async with client() as c:
        r = await c.get("/api/orders/QAK-ZZZZZ")
    assert r.status_code == 404 and r.json()["error"] is True


async def test_cloudflare_ip_wins_over_spoofed_forwarded_for(stubs):
    """A visitor can write X-Forwarded-For themselves; Cloudflare's header is what counts."""
    async with client() as c:
        r = await c.post(
            "/api/orders",
            json=order_body(phone="0553334444"),
            headers={"X-Forwarded-For": SA_IP, "CF-Connecting-IP": "8.8.8.8"},
        )
    assert r.status_code == 403 and r.json()["code"] == "geo_blocked"


async def test_unexpected_error_still_has_cors_headers(monkeypatch):
    """Without CORS headers the browser can't read a 500 and shows 'check your internet'."""

    async def boom(ip, phone):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(maxmind, "validate_ip", boom)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/orders",
            json=order_body(phone="0552223333"),
            headers={"X-Forwarded-For": SA_IP, "Origin": "https://qaalbalkhalij.store"},
        )
    assert r.status_code == 500
    assert r.json()["code"] == "server_error"
    assert r.headers.get("access-control-allow-origin") == "https://qaalbalkhalij.store"
