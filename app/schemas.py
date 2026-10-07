import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.catalog import OFFERS, PRODUCTS
from app.utils.phone import KSA_PHONE_RE
from app.utils.sanitize import clean_text

NAME_RE = re.compile(r"^[؀-ۿa-zA-Z\s]+$")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _validate_name(v: str) -> str:
    v = clean_text(v, 100)
    if len(v) < 3 or not NAME_RE.match(v):
        raise ValueError("invalid name")
    return v


def _validate_phone(v: str) -> str:
    v = re.sub(r"[\s-]", "", v.translate(ARABIC_DIGITS))
    if not KSA_PHONE_RE.match(v):
        raise ValueError("invalid phone")
    return v


class OrderItemIn(BaseModel):
    product_slug: str
    product_name: str = Field(default="", max_length=255)
    quantity: int
    unit_price: float = 0
    total_price: float = 0
    offer_label: str = Field(default="", max_length=50)

    @field_validator("product_slug")
    @classmethod
    def known_product(cls, v: str) -> str:
        if v not in PRODUCTS:
            raise ValueError("unknown product")
        return v

    @field_validator("quantity")
    @classmethod
    def known_offer(cls, v: int) -> int:
        if v not in OFFERS:
            raise ValueError("unknown offer")
        return v


class CreateOrderRequest(BaseModel):
    customer_name: str
    phone: str
    items: list[OrderItemIn] = Field(min_length=1, max_length=3)
    # Client totals are accepted for compatibility but recomputed server-side.
    subtotal: float = 0
    total: float = 0
    event_id: str = Field(default="", max_length=100)
    page_url: str = Field(default="", max_length=2048)
    user_agent: str = Field(default="", max_length=1024)
    referrer: str = Field(default="", max_length=2048)
    utm_source: str = Field(default="", max_length=2048)
    utm_medium: str = Field(default="", max_length=2048)
    utm_campaign: str = Field(default="", max_length=2048)
    utm_content: str = Field(default="", max_length=2048)
    utm_term: str = Field(default="", max_length=2048)
    fbclid: str = Field(default="", max_length=2048)
    ttclid: str = Field(default="", max_length=2048)
    sclid: str = Field(default="", max_length=2048)
    fbc: str = Field(default="", max_length=2048)
    fbp: str = Field(default="", max_length=2048)
    ttp: str = Field(default="", max_length=2048)

    @field_validator("customer_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        return _validate_phone(v)

    @field_validator("items")
    @classmethod
    def unique_products(cls, v: list[OrderItemIn]) -> list[OrderItemIn]:
        slugs = [i.product_slug for i in v]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate product")
        return v

    @field_validator(
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "fbclid", "ttclid", "sclid", "fbc", "fbp", "ttp", "event_id",
    )
    @classmethod
    def clean_tracking(cls, v: str) -> str:
        return clean_text(v, 255)


class UpsellRequest(BaseModel):
    accepted: bool
    product_slug: str = Field(default="", max_length=100)
    amount: float = 0
    event_id: str = Field(default="", max_length=100)
    page_url: str = Field(default="", max_length=2048)
    user_agent: str = Field(default="", max_length=1024)


ContactSubject = Literal["استفسار عن منتج", "استفسار عن طلب", "اقتراح", "أخرى"]


class ContactRequest(BaseModel):
    name: str
    phone: str
    subject: ContactSubject
    message: str = Field(min_length=5, max_length=2000)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        return _validate_phone(v)

    @field_validator("message")
    @classmethod
    def clean_message(cls, v: str) -> str:
        v = clean_text(v, 2000)
        if len(v) < 5:
            raise ValueError("message too short")
        return v
