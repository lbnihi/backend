from fastapi import APIRouter, Request

from app import errors
from app.errors import ApiError
from app.schemas import ContactRequest
from app.services import rate_limit, sheets
from app.services.background import fire_and_forget
from app.utils.network import get_client_ip

router = APIRouter(prefix="/api", tags=["contact"])


@router.post("/contact")
async def contact(body: ContactRequest, request: Request) -> dict:
    """Contact form → forwarded to the Sheets webhook ("Contacts" tab)."""
    if not rate_limit.check_contact_limit(get_client_ip(request)):
        raise ApiError(429, *errors.RATE_LIMITED)
    fire_and_forget(sheets.send_contact(body.name, body.phone, body.subject, body.message))
    return {"success": True}
