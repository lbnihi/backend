import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Raised anywhere in the app; rendered as the standard error envelope."""

    def __init__(self, status_code: int, code: str, message: str, message_en: str = "") -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.message_en = message_en


GEO_BLOCKED = ("geo_blocked", "عذراً، الخدمة متاحة فقط داخل المملكة العربية السعودية", "Service only available in Saudi Arabia")
VPN_DETECTED = ("vpn_detected", "عذراً، يرجى تعطيل VPN والمحاولة مرة أخرى", "Please disable your VPN and try again")
IP_NOT_FOUND = ("ip_not_found", "عذراً، لم نتمكن من التحقق من موقعك", "Could not verify your location")
SUSPICIOUS_IP = (
    "suspicious_ip",
    "عذراً، ما قدرنا نأكد طلبك من هالشبكة. جربي من بيانات الجوال أو شبكة واي فاي ثانية",
    "Order refused: high-risk IP",
)
VERIFICATION_UNAVAILABLE = (
    "verification_unavailable",
    "عذراً، صار خطأ مؤقت. حاولي مرة ثانية بعد دقيقة",
    "Location verification temporarily unavailable",
)
RATE_LIMITED = ("rate_limited", "عدد محاولات كثيرة، يرجى المحاولة لاحقاً", "Too many attempts, please try later")

FIELD_MESSAGES = {
    "phone": "يجب أن يبدأ الرقم بـ 05 ويتكون من ١٠ أرقام",
    "customer_name": "الاسم يجب أن يكون ٣ أحرف على الأقل، أحرف فقط",
    "items": "السلة فارغة أو فيها منتج غير صحيح",
    "name": "الاسم يجب أن يكون ٣ أحرف على الأقل، أحرف فقط",
    "message": "الرسالة قصيرة أو طويلة جداً",
    "subject": "الموضوع غير صحيح",
}

TOP_MESSAGES = {
    "phone": "رقم الجوال غير صحيح",
    "customer_name": "الاسم غير صحيح",
    "name": "الاسم غير صحيح",
    "items": "السلة غير صحيحة",
}


def _error_body(code: str, message: str, message_en: str = "", details: list | None = None) -> dict:
    body: dict = {"error": True, "code": code, "message": message}
    if message_en:
        body["message_en"] = message_en
    if details:
        body["details"] = details
    return body


async def catch_unhandled(request: Request, call_next):
    """Turn crashes into the standard 500 *inside* the CORS middleware.

    Starlette's own 500 handler runs outside CORS, so the browser can't read it and the shopper sees
    "check your internet" instead of a retry message.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("server_error", "صار خطأ غير متوقع، حاولي مرة ثانية", "Internal server error"),
        )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc.code, exc.message, exc.message_en))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p != "body"]
            field = loc[0] if loc else "body"
            details.append({"field": field, "message": FIELD_MESSAGES.get(field, "قيمة غير صحيحة")})
        first_field = details[0]["field"] if details else ""
        message = TOP_MESSAGES.get(first_field, "البيانات المدخلة غير صحيحة")
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", message, "Validation error", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "غير موجود" if exc.status_code == 404 else "طلب غير صحيح"
        return JSONResponse(status_code=exc.status_code, content=_error_body(code, message, str(exc.detail)))

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("server_error", "صار خطأ غير متوقع، حاولي مرة ثانية", "Internal server error"),
        )
