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

        assert stubs["capi"] == []  # no Purchase before the upsell decision: one Purchase per order
        assert stubs["sheets"] == []  # not before the upsell decision

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
        assert len(stubs["capi"]) == 3  # one per platform, not one per item/decision
        # CAPI: ONE Purchase per platform after the decision, original event_id, order + upsell value
        by_host = {httpx.URL(u).host: p for u, p, _ in stubs["capi"]}
        fb = by_host["graph.facebook.com"]["data"][0]
        assert fb["event_name"] == "Purchase" and fb["event_id"] == "evt_abc"
        assert fb["user_data"]["ph"] == [sha("966551234567")]
        assert fb["user_data"]["country"] == [sha("sa")]
        assert fb["user_data"]["ct"] == [sha("riyadh")]
        assert fb["user_data"]["client_ip_address"] == SA_IP
        assert fb["user_data"]["fbc"] == "fb.1.1.abc"
        assert fb["custom_data"]["contents"] == [
            {"id": "turmeric-golden", "quantity": 3, "item_price": 116.33},
            {"id": "hyaluronic-acid", "quantity": 1, "item_price": 99.0},
        ]
        assert fb["custom_data"]["value"] == 448.0

        tt_body = by_host["business-api.tiktok.com"]
        assert tt_body["event_source"] == "web" and tt_body["event_source_id"] == "tiktok-pixel"
        tt = tt_body["data"][0]
        assert tt["event"] == "PlaceAnOrder" and tt["event_id"] == "evt_abc"
        assert tt["user"]["phone"] == sha("+966551234567")
        assert tt["user"]["ip"] == SA_IP and tt["user"]["ttclid"] == "TTC"
        assert isinstance(tt["event_time"], int)
        assert tt["properties"]["value"] == 448.0 and tt["properties"]["currency"] == "SAR"

        sn = by_host["tr.snapchat.com"]["data"][0]
        assert sn["event_type"] == "PURCHASE" and sn["uuid_c1"] == "evt_abc"
        assert sn["hashed_phone_number"] == sha("+966551234567")
        assert sn["hashed_ip_address"] == sha(SA_IP)
        assert sn["price"] == "448.00" and sn["number_items"] == "4"
        assert sn["transaction_id"] == order["order_number"] and sn["click_id"] == "SC"


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
    assert len(stubs["capi"]) == 3  # abandoned upsell: the fallback still sends the single Purchase


async def test_upsell_skips_products_already_in_cart():
    items = [
        {"product_slug": "turmeric-golden", "quantity": 1},
        {"product_slug": "hyaluronic-acid", "quantity": 1},
    ]
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=items, phone="0558888888"), headers={"X-Forwarded-For": SA_IP})
    assert r.status_code == 201
    assert r.json()["upsell"]["product_slug"] == "moringa"
    assert r.json()["order"]["total"] == 398.0
    await asyncio.sleep(1.3)  # let the fallback finalizer run here, not during the next test


@pytest.mark.parametrize(
    "ip,code,message",
    [
        ("8.8.8.8", "geo_blocked", "عذراً، الخدمة متاحة فقط داخل المملكة العربية السعودية"),
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


async def test_click_tracking_never_calls_maxmind(monkeypatch):
    """Page views must not spend MaxMind lookups: order checks depend on that quota."""

    class Boom:
        async def insights(self, ip):
            raise AssertionError("MaxMind called for a page view")

        city = insights

    monkeypatch.setattr(maxmind, "_client", Boom())
    monkeypatch.setattr(maxmind, "_geolite_client", Boom())
    maxmind.clear_cache()
    async with client() as c:
        r = await c.post(
            "/api/clicks",
            json={"page_url": "https://qaalbalkhalij.store/products/hyaluronic-acid", "utm_source": "snapchat"},
            headers={"CF-Connecting-IP": "2.88.1.1", "CF-IPCountry": "SA"},
        )
    assert r.status_code == 204
    from app.database import async_session
    from app.models import Visit
    from sqlalchemy import select

    async with async_session() as s:
        visit = (await s.scalars(select(Visit).order_by(Visit.id.desc()))).first()
    assert visit.ip_address == "2.88.1.1" and visit.country_code == "SA" and visit.utm_source == "snapchat"


async def test_admin_locked_with_default_jwt_secret(monkeypatch):
    """The default secret is public on GitHub: a token forged with it must never work."""
    import jwt as pyjwt

    monkeypatch.setattr(maxmind.settings, "admin_jwt_secret", "change-me-in-production")
    forged = pyjwt.encode({"sub": "attacker", "exp": 9999999999}, "change-me-in-production", algorithm="HS256")
    async with client() as c:
        r = await c.get("/api/admin/metrics", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 503


async def test_admin_metrics_endpoint(monkeypatch):
    """Dashboard metrics load (sources / cities grouping) with a valid admin token."""
    from app.services.auth import create_token

    monkeypatch.setattr(maxmind.settings, "admin_jwt_secret", "x" * 40)
    token = create_token("admin")
    async with client() as c:
        await c.post("/api/orders", json=order_body(phone="0554445555"), headers={"X-Forwarded-For": SA_IP})
        r = await c.get("/api/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(s["source"] == "snapchat" for s in body["top_sources"])
    assert any(c["city"] == "Riyadh" for c in body["top_cities"])


async def test_moringa_orders_and_oregano_never_upsold():
    from app.catalog import CROSS_SELL, UPSELL_TARGET, pick_upsell

    assert all(t != "thyme-blackseed" for t in UPSELL_TARGET.values())
    assert all("thyme-blackseed" not in v for v in CROSS_SELL.values())
    for cart in (["moringa"], ["turmeric-golden", "hyaluronic-acid"], ["thyme-blackseed"]):
        assert pick_upsell(cart) != "thyme-blackseed"
    items = [{"product_slug": "moringa", "product_name": "x", "quantity": 1, "unit_price": 1, "total_price": 1, "offer_label": "x"}]
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=items, phone="0556667777"), headers={"X-Forwarded-For": SA_IP})
    assert r.status_code == 201, r.text
    assert r.json()["order"]["total"] == 199.0 and r.json()["order"]["items"][0]["product_name"] == "كبسولات المورينجا"


async def test_thank_you_addon(stubs):
    """Add one pack from the thank-you page: same order, buyer-only, no duplicates, sheet note."""
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(phone="0551110000"), headers={"X-Forwarded-For": SA_IP})
        order = r.json()["order"]
        oid, number = order["id"], order["order_number"]
        await c.post(f"/api/orders/{oid}/upsell", json={"accepted": False, "event_id": "evt_u"})

        bad = await c.post(f"/api/orders/{oid}/addons", json={"product_slug": "moringa", "order_event_id": "guess"})
        assert bad.status_code == 404  # someone else's order id

        await settle()
        purchases_before = len(stubs["capi"])
        ok = await c.post(f"/api/orders/{oid}/addons", json={"product_slug": "moringa", "order_event_id": "evt_abc", "event_id": "evt_add"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["order"]["total"] == 349.0 + 149.0

        dup = await c.post(f"/api/orders/{oid}/addons", json={"product_slug": "moringa", "order_event_id": "evt_abc"})
        assert dup.status_code == 409
        same = await c.post(f"/api/orders/{oid}/addons", json={"product_slug": "turmeric-golden", "order_event_id": "evt_abc"})
        assert same.status_code == 409  # already in the original order

        detail = (await c.get(f"/api/orders/{number}")).json()
    assert [i["product_slug"] for i in detail["items"]] == ["turmeric-golden", "moringa"]
    assert len(stubs["capi"]) == purchases_before  # add-on: no second Purchase
    await settle()
    assert any("أضافت بعد الطلب: كبسولات المورينجا" in (s.get("notes") or "") for s in stubs["sheets"])


async def test_admin_funnel_metrics(monkeypatch):
    """Clicks/page views/checkouts → orders → confirmed → delivered; revenue & AOV on delivered only."""
    from app.services.auth import create_token

    monkeypatch.setattr(maxmind.settings, "admin_jwt_secret", "y" * 40)
    auth = {"Authorization": f"Bearer {create_token('admin')}"}
    rate_limit.limiter.reset()
    async with client() as c:
        m0 = (await c.get("/api/admin/metrics", headers=auth)).json()
        sa = {"CF-IPCountry": "SA"}
        for ip, kind in [("2.88.9.1", None), ("2.88.9.1", None), ("2.88.9.2", None), ("2.88.9.2", "checkout"), ("2.88.9.3", "checkout")]:
            await c.post("/api/clicks", json={"page_url": "https://x", **({"kind": kind} if kind else {})}, headers={**sa, "CF-Connecting-IP": ip})
        ids = []
        for phone in ("0554440001", "0554440002", "0554440003"):
            r = await c.post("/api/orders", json=order_body(phone=phone), headers={"X-Forwarded-For": SA_IP})
            ids.append(r.json()["order"]["id"])
            await c.post(f"/api/orders/{ids[-1]}/upsell", json={"accepted": phone.endswith("1"), "product_slug": "hyaluronic-acid"})
        for oid, status in zip(ids, ("delivered", "confirmed", "cancelled")):
            assert (await c.patch(f"/api/admin/orders/{oid}/status", json={"status": status}, headers=auth)).status_code == 200
        m = (await c.get("/api/admin/metrics", headers=auth)).json()
    assert m["page_views"] - m0["page_views"] == 3
    assert m["clicks"] - m0["clicks"] == 2  # unique visitors
    assert m["checkouts"] - m0["checkouts"] == 2
    assert m["orders"] - m0["orders"] == 3
    assert m["confirmed"] - m0["confirmed"] == 2  # delivered + confirmed
    assert m["delivered"] - m0["delivered"] == 1
    assert m["revenue"] - m0["revenue"] == 448.0 + 349.0 + 349.0  # every order placed
    assert m["delivered_revenue"] - m0["delivered_revenue"] == 448.0  # delivered order only (349 + upsell 99)
    for key in ("confirmation_rate", "delivery_rate", "checkout_cvr", "conversion_rate", "aov", "booked_revenue"):
        assert key in m
