"""COD Network push: payload, once-only, test-order skip, missing SKU."""

import httpx
import pytest

from app.services import codnetwork, rate_limit
from tests.test_api import SA_IP, client, order_body, settle, stubs  # noqa: F401  (stubs: fake geo/CAPI/Sheets)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("stubs")]


@pytest.fixture
def cod(monkeypatch):
    sent: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append({"url": url, "auth": headers["Authorization"], "json": json})
            return httpx.Response(201, json={"status": "success", "data": {"id": 777}})

    monkeypatch.setattr(codnetwork, "_http", FakeClient)
    monkeypatch.setattr(codnetwork.settings, "codnetwork_api_token", "tok")
    monkeypatch.setattr(codnetwork.settings, "codnetwork_skus", "hyaluronic-acid:MP-OHVNOQLIBYO8")
    monkeypatch.setattr(codnetwork.settings, "codnetwork_send_test_orders", False)
    rate_limit.limiter.reset()
    return sent


def ha_items(qty=3):
    return [{"product_slug": "hyaluronic-acid", "product_name": "x", "quantity": qty, "unit_price": 1, "total_price": 1, "offer_label": "x"}]


async def test_order_pushed_once_with_sku(cod):
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=ha_items(), phone="0552220000"), headers={"X-Forwarded-For": SA_IP})
        oid = r.json()["order"]["id"]
        await c.post(f"/api/orders/{oid}/upsell", json={"accepted": False, "event_id": "evt_u"})
        await settle()
        await codnetwork.send_order(oid)  # second call must not resend
    assert len(cod) == 1
    sent = cod[0]
    assert sent["url"] == "https://api.cod.network/v2/seller/orders" and sent["auth"] == "Bearer tok"
    body = sent["json"]
    assert body["phone"] == "0552220000" and body["country"] == "SA" and body["currency"] == "SAR"
    assert body["items"] == [{"sku": "MP-OHVNOQLIBYO8", "name": "كبسولات حمض الهيالورونيك", "quantity": 3, "price": 349.0}]
    assert body["city"] == "Riyadh" and body["area"] == "Riyadh" and body["address"]
    assert body["total"] == 349.0 and body["reference"] == r.json()["order"]["order_number"]


async def test_test_orders_and_missing_sku_not_sent(cod):
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=ha_items(), phone="0550000000"), headers={"X-Forwarded-For": SA_IP})
        await c.post(f"/api/orders/{r.json()['order']['id']}/upsell", json={"accepted": False})
        r2 = await c.post("/api/orders", json=order_body(phone="0552221111"), headers={"X-Forwarded-For": SA_IP})  # turmeric: no SKU
        await c.post(f"/api/orders/{r2.json()['order']['id']}/upsell", json={"accepted": False})
        await settle()
    assert cod == []


async def test_test_orders_sent_when_enabled(cod, monkeypatch):
    monkeypatch.setattr(codnetwork.settings, "codnetwork_send_test_orders", True)
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=ha_items(1), phone="0550000000"), headers={"X-Forwarded-For": SA_IP})
        await c.post(f"/api/orders/{r.json()['order']['id']}/upsell", json={"accepted": False})
        await settle()
    assert len(cod) == 1 and cod[0]["json"]["items"][0]["quantity"] == 1


async def test_drop_product_falls_back_to_lead(cod, monkeypatch):
    """/orders refuses marketplace SKUs (40049): the same payload is sent to /leads."""
    calls: list[str] = []

    class DropClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(url)
            if url.endswith("/orders"):
                return httpx.Response(400, json={"status": "error", "errors": [{"code": "40049", "message": "Cannot create order with drop product."}]})
            return httpx.Response(201, json={"status": "success", "data": {"id": 55}})

    monkeypatch.setattr(codnetwork, "_http", DropClient)
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=ha_items(1), phone="0552223333"), headers={"X-Forwarded-For": SA_IP})
        await c.post(f"/api/orders/{r.json()['order']['id']}/upsell", json={"accepted": False})
        await settle()
    assert calls == ["https://api.cod.network/v2/seller/orders", "https://api.cod.network/v2/seller/leads"]


async def test_thank_you_addon_sent_after_order(cod, monkeypatch):
    """Add-on after the order reached COD Network → sent once as its own lead, flagged as the same order."""
    monkeypatch.setattr(codnetwork.settings, "codnetwork_skus", "hyaluronic-acid:MP-HA,moringa:MP-MO")
    async with client() as c:
        r = await c.post("/api/orders", json=order_body(items=ha_items(1), phone="0552224444", event_id="evt_abc"), headers={"X-Forwarded-For": SA_IP})
        order = r.json()["order"]
        await c.post(f"/api/orders/{order['id']}/upsell", json={"accepted": False})
        await settle()
        assert len(cod) == 1  # the order itself
        add = await c.post(f"/api/orders/{order['id']}/addons", json={"product_slug": "moringa", "order_event_id": "evt_abc"})
        assert add.status_code == 200, add.text
        await settle()
        await codnetwork.send_addon(order["id"], "moringa")  # never twice
    assert len(cod) == 2
    extra = cod[1]["json"]
    assert extra["items"] == [{"sku": "MP-MO", "name": "كبسولات المورينجا", "quantity": 1, "price": 149.0}]
    assert extra["total"] == 149.0 and extra["reference"] == f"{order['order_number']}-moringa"
    assert order["order_number"] in extra["note"] and extra["phone"] == "0552224444"
