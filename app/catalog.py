"""Server-side source of truth for products and prices. Client-sent prices are never trusted."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CatalogProduct:
    slug: str
    name: str
    tagline: str


PRODUCTS: dict[str, CatalogProduct] = {
    "turmeric-golden": CatalogProduct(
        slug="turmeric-golden",
        name="كبسولات الكركم الذهبي",
        tagline="ودّعي ألم المفاصل... وعيشي حياتك بحرية",
    ),
    "thyme-blackseed": CatalogProduct(
        slug="thyme-blackseed",
        name="زيت الأوريجانو والحبة السوداء",
        tagline="بطن مرتاح ومناعة أقوى... من كنوز الطبيعة",
    ),
    "hyaluronic-acid": CatalogProduct(
        slug="hyaluronic-acid",
        name="كبسولات حمض الهيالورونيك",
        tagline="ركب أخف وبشرة تشع... بكبسولتين في اليوم",
    ),
}

# quantity -> (price, label)
OFFERS: dict[int, tuple[Decimal, str]] = {
    1: (Decimal("199.00"), "بداية الفرق — عبوة واحدة"),
    2: (Decimal("279.00"), "الفرق الواضح — عبوتين"),
    3: (Decimal("349.00"), "النتيجة الكاملة — ٣ عبوات"),
}

SINGLE_PRICE = OFFERS[1][0]
UPSELL_PRICE = Decimal("99.00")

# Post-checkout upsell target per product (docs/03-product-catalog.md)
UPSELL_TARGET: dict[str, str] = {
    "turmeric-golden": "hyaluronic-acid",
    "thyme-blackseed": "turmeric-golden",
    "hyaluronic-acid": "thyme-blackseed",
}

CROSS_SELL: dict[str, list[str]] = {
    "turmeric-golden": ["hyaluronic-acid", "thyme-blackseed"],
    "thyme-blackseed": ["turmeric-golden", "hyaluronic-acid"],
    "hyaluronic-acid": ["thyme-blackseed", "turmeric-golden"],
}


def pick_upsell(cart_slugs: list[str]) -> str:
    """Matrix target of the first cart item; if already in the cart, the next matrix product that isn't."""
    first = cart_slugs[0]
    target = UPSELL_TARGET[first]
    if target not in cart_slugs:
        return target
    for candidate in CROSS_SELL[first]:
        if candidate not in cart_slugs:
            return candidate
    return target


def upsell_payload(slug: str) -> dict:
    product = PRODUCTS[slug]
    return {
        "product_slug": slug,
        "product_name": product.name,
        "original_price": float(SINGLE_PRICE),
        "offer_price": float(UPSELL_PRICE),
        "tagline": product.tagline,
    }
