"""The real validate_ip against a fake MaxMind Insights client (no network)."""

import geoip2.errors
import geoip2.models
import pytest

from app.services import maxmind

pytestmark = pytest.mark.asyncio

PHONE = "0551234567"


def insights(country="SA", city="Riyadh", **traits) -> geoip2.models.Insights:
    raw = {
        "country": {"iso_code": country},
        "city": {"names": {"en": city}},
        "traits": {"ip_address": "1.2.3.4", "autonomous_system_organization": "Saudi Telecom Company", **traits},
    }
    return geoip2.models.Insights(["en"], **raw)


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response, self.error, self.calls = response, error, 0

    async def insights(self, ip):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response

    city = insights


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    maxmind.clear_cache()
    monkeypatch.setattr(maxmind.settings, "maxmind_fail_open", False)
    yield
    maxmind.clear_cache()


def use(monkeypatch, client):
    monkeypatch.setattr(maxmind, "_client", client)
    return client


async def test_saudi_residential_ip_is_allowed(monkeypatch):
    use(monkeypatch, FakeClient(insights(ip_risk_snapshot=0.5, user_type="cellular")))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason, r.country, r.city) == (True, "valid", "SA", "Riyadh")


async def test_outside_ksa_blocked(monkeypatch):
    use(monkeypatch, FakeClient(insights(country="AE", city="Dubai")))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason) == (False, "country_AE")


@pytest.mark.parametrize(
    "response",
    [
        insights(is_anonymous_vpn=True, is_anonymous=True),
        insights(is_tor_exit_node=True),
        insights(is_residential_proxy=True),
        insights(is_public_proxy=True),
        insights(is_hosting_provider=True),
        insights(user_type="hosting"),
        insights(autonomous_system_organization="M247 Europe VPN"),
    ],
)
async def test_vpn_in_ksa_allowed_but_flagged(monkeypatch, response):
    """Saudi shoppers on a VPN can order: the IP only has to be in KSA. VPN is flagged for the admin."""
    use(monkeypatch, FakeClient(response))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason, r.is_vpn) == (True, "valid", True)


async def test_high_risk_score_allowed(monkeypatch):
    use(monkeypatch, FakeClient(insights(ip_risk_snapshot=45.0)))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert r.allowed


async def test_whitelist_skips_lookup(monkeypatch):
    client = use(monkeypatch, FakeClient(insights(country="US")))
    r = await maxmind.validate_ip("8.8.8.8", "0550000000")
    assert r.allowed and r.reason == "whitelisted" and client.calls == 0


async def test_private_ip_never_reaches_api(monkeypatch):
    client = use(monkeypatch, FakeClient(insights()))
    r = await maxmind.validate_ip("10.0.0.5", PHONE)
    assert (r.allowed, r.reason, client.calls) == (False, "ip_not_found", 0)


async def test_unknown_ip(monkeypatch):
    use(monkeypatch, FakeClient(error=geoip2.errors.AddressNotFoundError("not found")))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason) == (False, "ip_not_found")


async def test_lookup_is_cached(monkeypatch):
    client = use(monkeypatch, FakeClient(insights()))
    await maxmind.validate_ip("2.88.1.1", PHONE)
    await maxmind.validate_ip("2.88.1.1", "0559876543")
    assert client.calls == 1


@pytest.mark.parametrize(
    "error,reason",
    [
        (geoip2.errors.OutOfQueriesError("no credits"), "maxmind_out_of_credits"),
        (geoip2.errors.AuthenticationError("bad key"), "maxmind_auth_error"),
        (TimeoutError(), "maxmind_unavailable"),
    ],
)
async def test_api_failure_is_strict_by_default(monkeypatch, error, reason):
    use(monkeypatch, FakeClient(error=error))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason) == (False, reason)


async def test_api_failure_fail_open(monkeypatch):
    monkeypatch.setattr(maxmind.settings, "maxmind_fail_open", True)
    use(monkeypatch, FakeClient(error=TimeoutError()))
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason) == (True, "maxmind_unavailable_fail_open")


async def test_not_configured_refuses(monkeypatch):
    use(monkeypatch, None)
    r = await maxmind.validate_ip("2.88.1.1", PHONE)
    assert (r.allowed, r.reason) == (False, "maxmind_not_configured")


def geolite(country="SA", city="Riyadh", asn="Saudi Telecom Company JSC") -> geoip2.models.City:
    raw = {
        "country": {"iso_code": country},
        "city": {"names": {"en": city}},
        "traits": {"ip_address": "2.88.0.1", "autonomous_system_organization": asn},
    }
    return geoip2.models.City(["en"], **raw)


class NoInsightsClient(FakeClient):
    async def insights(self, ip):
        self.calls += 1
        raise geoip2.errors.PermissionRequiredError("You do not have permission to use this service interface.")


@pytest.mark.parametrize(
    "response,allowed,reason",
    [
        (geolite(), True, "valid"),
        (geolite(country="FR", city="Paris", asn="Orange"), False, "country_FR"),
        (geolite(asn="Datacamp Limited VPN"), True, "valid"),
    ],
)
async def test_geolite_fallback_without_insights(monkeypatch, response, allowed, reason):
    insights_client = use(monkeypatch, NoInsightsClient())
    monkeypatch.setattr(maxmind, "_geolite_client", FakeClient(response))
    r = await maxmind.validate_ip("2.88.0.1", PHONE)
    assert (r.allowed, r.reason) == (allowed, reason)
    # Insights isn't retried on the next order (only after an hour)
    maxmind._cache.clear()
    await maxmind.validate_ip("2.88.0.2", PHONE)
    assert insights_client.calls == 1
