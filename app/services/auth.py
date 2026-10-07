import time

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.errors import ApiError

ALGORITHM = "HS256"
TOKEN_LIFETIME = 86400  # 24h

_bearer = HTTPBearer(auto_error=False)

# The default lives in public source code: anyone could sign tokens with it.
_UNSAFE_SECRETS = {"", "change-me-in-production"}


def _secret() -> str:
    """Admin stays locked until ADMIN_JWT_SECRET is set to a long random value."""
    secret = settings.admin_jwt_secret
    if secret in _UNSAFE_SECRETS or len(secret) < 32:
        raise ApiError(503, "admin_not_configured", "لوحة التحكم غير مفعّلة", "Set ADMIN_JWT_SECRET (32+ chars)")
    return secret


def create_token(username: str) -> str:
    return jwt.encode(
        {"sub": username, "exp": int(time.time()) + TOKEN_LIFETIME},
        _secret(),
        algorithm=ALGORITHM,
    )


async def require_admin(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if cred is None:
        raise ApiError(401, "unauthorized", "يجب تسجيل الدخول", "Authentication required")
    try:
        payload = jwt.decode(cred.credentials, _secret(), algorithms=[ALGORITHM])
        return payload["sub"]
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise ApiError(401, "unauthorized", "انتهت الجلسة", "Token expired or invalid")
