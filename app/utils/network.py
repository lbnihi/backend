from fastapi import Request


def get_client_ip(request: Request) -> str:
    """Real client IP behind Cloudflare → EasyPanel (Traefik).

    CF-Connecting-IP is set by Cloudflare and overwrites any value the visitor sends, so it can't be
    spoofed through Cloudflare. X-Forwarded-For is the fallback; its *first* entry is client-controlled,
    so it's only used when the request didn't come through Cloudflare.
    """
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
