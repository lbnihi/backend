import secrets

from fastapi import Request

from app import errors
from app.config import settings
from app.errors import ApiError


def get_client_ip(request: Request) -> str:
    """Real client IP behind Cloudflare → EasyPanel (Traefik).

    CF-Connecting-IP is set by Cloudflare and overwrites any value the visitor sends, so it can't be
    spoofed through Cloudflare. X-Forwarded-For is the fallback; its *first* entry is client-controlled,
    so it's only used when the request didn't come through Cloudflare.
    """
    # Same-origin relay on the shop domain (the shop's server, not the shopper, made this request):
    # trust its X-Client-IP only with the shared secret. A wrong/unknown secret is refused so the
    # relay falls back to calling the API directly instead of checking the server's own IP.
    relay_secret = request.headers.get("X-Proxy-Secret")
    if relay_secret is not None:
        expected = settings.proxy_secret.strip()
        if not expected or not secrets.compare_digest(relay_secret, expected):
            raise ApiError(503, *errors.PROXY_UNTRUSTED)
        client_ip = request.headers.get("X-Client-IP", "").strip()
        if client_ip:
            return client_ip

    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "0.0.0.0"
