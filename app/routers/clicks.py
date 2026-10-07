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

    # Quick geo check — reuses MaxMind cache so almost free for repeat IPs
    geo = await maxmind.check_ip(ip)

    try:
        body = await request.json()
    except Exception:
        body = {}

    async with async_session() as session:
        visit = Visit(
            ip_address=ip,
            country_code=geo.country[:2] if geo.country else None,
            city=geo.city if geo.city != "Unknown" else None,
            is_vpn=geo.is_vpn or (not geo.allowed and geo.reason != "whitelisted"),
            page_url=str(body.get("page_url", ""))[:2048] or None,
            referrer=str(body.get("referrer", ""))[:2048] or None,
            user_agent=(request.headers.get("user-agent", ""))[:1024] or None,
            utm_source=str(body.get("utm_source", ""))[:100] or None,
            utm_medium=str(body.get("utm_medium", ""))[:100] or None,
            utm_campaign=str(body.get("utm_campaign", ""))[:255] or None,
        )
        session.add(visit)
        await session.commit()
