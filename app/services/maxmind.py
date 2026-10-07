"""KSA-only geo restriction and VPN / proxy / risk detection via MaxMind web services.

Every non-whitelisted order is checked live. GeoIP2 Insights (paid credits) gives VPN/proxy/hosting flags and a
risk score. Accounts without Insights get PERMISSION_REQUIRED; we then fall back to the free GeoLite City web
service (country + network owner, VPN detected by network name only) and retry Insights an hour later.
Strict by default: if no verdict is possible the order is refused, unless MAXMIND_FAIL_OPEN=true.
"""

import ipaddress
import logging
import time
from dataclasses import dataclass

import geoip2.errors
import geoip2.models
import geoip2.webservice

from app.config import settings
from app.utils.phone import mask_phone

logger = logging.getLogger("fraud")

_client: geoip2.webservice.AsyncClient | None = None
_geolite_client: geoip2.webservice.AsyncClient | None = None
# monotonic time until which Insights is skipped (account lacks permission)
_insights_disabled_until = 0.0
INSIGHTS_RETRY_SECONDS = 3600

# Lookups cost credits: reuse a verdict for the same IP for an hour.
CACHE_TTL_SECONDS = 3600
_cache: dict[str, tuple[float, "GeoResult"]] = {}

VPN_KEYWORDS = [
    "vpn", "proxy", "tunnel", "anonymous", "hosting", "cloud",
    "data center", "datacenter", "colocation", "server",
]
# Insights user_type values that are never a real shopper's connection.
BLOCKED_USER_TYPES = {"hosting", "search_engine_spider"}

# Reasons the orders router maps to an error response.
VPN_REASONS = {"anonymous_ip", "suspicious_asn", "hosting_network"}


@dataclass
class GeoResult:
    allowed: bool
    reason: str
    country: str = ""
    city: str = ""
    asn_org: str = ""
    is_vpn: bool = False
    risk: float | None = None


def is_configured() -> bool:
    return bool(settings.maxmind_account_id.strip() and settings.maxmind_license_key.strip())


def start_client() -> None:
    global _client, _geolite_client
    if not is_configured():
        mode = "ALLOWED (fail-open)" if settings.maxmind_fail_open else "REFUSED"
        logger.warning("MaxMind credentials missing: non-whitelisted orders will be %s", mode)
        return
    try:
        account_id = int(settings.maxmind_account_id.strip())
    except ValueError:
        logger.error("MAXMIND_ACCOUNT_ID must be numeric")
        return
    key = settings.maxmind_license_key.strip()
    timeout = settings.maxmind_timeout_seconds
    _client = geoip2.webservice.AsyncClient(account_id, key, timeout=timeout)
    _geolite_client = geoip2.webservice.AsyncClient(account_id, key, host="geolite.info", timeout=timeout)
    logger.info("MaxMind web service clients ready")


async def close_client() -> None:
    global _client, _geolite_client
    for c in (_client, _geolite_client):
        if c is not None:
            await c.close()
    _client = _geolite_client = None


def is_ready() -> bool:
    return _client is not None


def _log(result: GeoResult, ip: str, phone: str) -> None:
    logger.info(
        "order_attempt ip=%s country=%s city=%s asn=%r risk=%s allowed=%s reason=%s phone=%s",
        ip, result.country or "-", result.city or "-", result.asn_org or "-",
        "-" if result.risk is None else result.risk,
        result.allowed, result.reason, mask_phone(phone),
    )


async def validate_ip(ip_address: str, phone: str) -> GeoResult:
    result = await _validate(ip_address, phone)
    _log(result, ip_address, phone)
    return result


def _unavailable(reason: str) -> GeoResult:
    """The API could not give a verdict (not configured, out of credits, timeout...)."""
    if settings.maxmind_fail_open:
        return GeoResult(True, f"{reason}_fail_open")
    return GeoResult(False, reason)


async def _validate(ip_address: str, phone: str) -> GeoResult:
    # 1. Whitelist bypasses every check (production test orders)
    if phone.strip() in settings.whitelist:
        return GeoResult(True, "whitelisted", "SA", "Test")

    try:
        parsed = ipaddress.ip_address(ip_address)
    except ValueError:
        return GeoResult(False, "ip_not_found")
    if not parsed.is_global:
        return GeoResult(False, "ip_not_found")

    cached = _cache.get(ip_address)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    if _client is None:
        return _unavailable("maxmind_not_configured")

    # 2. Live lookup: Insights, or GeoLite City when the account has no Insights access
    global _insights_disabled_until
    try:
        if time.monotonic() >= _insights_disabled_until:
            try:
                response = await _client.insights(ip_address)
            except geoip2.errors.PermissionRequiredError:
                _insights_disabled_until = time.monotonic() + INSIGHTS_RETRY_SECONDS
                logger.warning(
                    "MaxMind account has no GeoIP2 Insights access — using free GeoLite City "
                    "(country + network-name VPN check only). Buy Insights credits for full VPN/risk detection."
                )
                response = await _geolite_client.city(ip_address)
        else:
            response = await _geolite_client.city(ip_address)
    except geoip2.errors.AddressNotFoundError:
        return GeoResult(False, "ip_not_found")
    except (geoip2.errors.AuthenticationError, geoip2.errors.PermissionRequiredError) as exc:
        logger.error("MaxMind rejected our credentials: %s", exc)
        return _unavailable("maxmind_auth_error")
    except geoip2.errors.OutOfQueriesError:
        logger.error("MaxMind account is out of credits — top up at maxmind.com")
        return _unavailable("maxmind_out_of_credits")
    except Exception as exc:  # timeout, network, 5xx
        logger.error("MaxMind lookup failed for %s: %s", ip_address, exc)
        return _unavailable("maxmind_unavailable")

    result = evaluate(response)
    _cache[ip_address] = (time.monotonic() + CACHE_TTL_SECONDS, result)
    if len(_cache) > 10_000:
        _cache.clear()
    return result


def evaluate(response: geoip2.models.City) -> GeoResult:
    """Works for Insights (all checks) and GeoLite City (no anonymizer flags / risk: those stay False/None)."""
    country = response.country.iso_code or ""
    city = response.city.name or "Unknown"
    traits = response.traits
    asn_org = traits.autonomous_system_organization or traits.isp or ""
    risk = getattr(traits, "ip_risk_snapshot", None)

    # 3. Country must be SA
    if country != "SA":
        return GeoResult(False, f"country_{country or 'unknown'}", country, city, asn_org, risk=risk)

    # 4. VPN / proxy / Tor / hosting detection
    if (
        traits.is_anonymous
        or traits.is_anonymous_vpn
        or traits.is_public_proxy
        or traits.is_residential_proxy
        or traits.is_tor_exit_node
        or traits.is_anonymous_proxy
    ):
        return GeoResult(False, "anonymous_ip", country, city, asn_org, is_vpn=True, risk=risk)
    if traits.is_hosting_provider or (traits.user_type or "") in BLOCKED_USER_TYPES:
        return GeoResult(False, "hosting_network", country, city, asn_org, is_vpn=True, risk=risk)
    if any(kw in asn_org.lower() for kw in VPN_KEYWORDS):
        return GeoResult(False, "suspicious_asn", country, city, asn_org, is_vpn=True, risk=risk)

    # 5. MaxMind's own IP risk score (0.01 – 99)
    if risk is not None and risk >= settings.maxmind_max_risk:
        return GeoResult(False, "high_risk", country, city, asn_org, risk=risk)

    return GeoResult(True, "valid", country, city, asn_org, risk=risk)


async def check_ip(ip_address: str) -> GeoResult:
    """Lightweight IP check for click tracking — no phone needed, no logging."""
    return await _validate(ip_address, "")


def clear_cache() -> None:
    global _insights_disabled_until
    _cache.clear()
    _insights_disabled_until = 0.0
