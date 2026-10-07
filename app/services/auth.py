import time

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.errors import ApiError

ALGORITHM = "HS256"
TOKEN_LIFETIME = 86400  # 24h

_bearer = HTTPBearer(auto_error=False)


def create_token(username: str) -> str:
    return jwt.encode(
        {"sub": username, "exp": int(time.time()) + TOKEN_LIFETIME},
        settings.admin_jwt_secret,
        algorithm=ALGORITHM,
    )


async def require_admin(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if cred is None:
        raise ApiError(401, "unauthorized", "يجب تسجيل الدخول", "Authentication required")
    try:
        payload = jwt.decode(cred.credentials, settings.admin_jwt_secret, algorithms=[ALGORITHM])
        return payload["sub"]
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise ApiError(401, "unauthorized", "انتهت الجلسة", "Token expired or invalid")
