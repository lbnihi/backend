from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import async_session
from app.services.maxmind import is_ready as maxmind_ready

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> JSONResponse:
    db_status = "disconnected"
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        pass

    healthy = db_status == "connected"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "database": db_status,
            "maxmind": "ready" if maxmind_ready() else "not_configured",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
