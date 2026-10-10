import logging

from fastapi import APIRouter, Request, status

from app.database import async_session
from app.models import Visit
from app.services import maxmind
from app.utils.network import get_client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["clicks"])


@router.post("/clicks", status_code=status.HTTP_204_NO_CONTENT)
async def record_click(request: Request) -> None:
    """Record a page visit. Fire-and-forget from the frontend — always returns 204."""
    ip = get_client_ip(request)

    # No MaxMind call here: page views would use up the lookup quota that order checks depend on.
    # Country comes free from Cloudflare (CF-IPCountry; "T1" = Tor); VPN is known only if this IP
    # already went through an order check.
    cached = maxmind.cached_result(ip)
    cf_country = request.headers.get("CF-IPCountry", "").upper()
    country = cached.country if cached and cached.country else (cf_country if len(cf_country) == 2 else "")
    is_vpn = cached.is_vpn if cached else cf_country == "T1"

    try:
        body = await request.json()
    except Exception:
        body = {}

    try:
        async with async_session() as session:
            session.add(
                Visit(
                    ip_address=ip,
                    kind="checkout" if body.get("kind") == "checkout" else "page_view",
                    country_code=country[:2] if country and country not in ("XX", "T1") else None,
                    city=cached.city if cached and cached.city != "Unknown" else None,
                    is_vpn=is_vpn,
                    page_url=str(body.get("page_url", ""))[:2048] or None,
                    referrer=str(body.get("referrer", ""))[:2048] or None,
                    user_agent=(request.headers.get("user-agent", ""))[:1024] or None,
                    utm_source=str(body.get("utm_source", ""))[:100] or None,
                    utm_medium=str(body.get("utm_medium", ""))[:100] or None,
                    utm_campaign=str(body.get("utm_campaign", ""))[:255] or None,
                )
            )
            await session.commit()
    except Exception as exc:  # tracking must never error for the shopper
        logger.warning("Could not record visit: %s", exc)
