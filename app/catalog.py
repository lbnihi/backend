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
    "moringa": CatalogProduct(
        slug="moringa",
        name="كبسولات المورينجا",
        tagline="طاقة تكفي يومك... بكبسولة وحدة",
    ),
}

# quantity -> (price, label)
OFFERS: dict[int, tuple[Decimal, str]] = {
    1: (Decimal("199.00"), "عبوة واحدة"),
    2: (Decimal("279.00"), "عبوتين — نتيجة تثبت"),
    3: (Decimal("349.00"), "٣ عبوات — نتيجة كاملة"),
}

SINGLE_PRICE = OFFERS[1][0]
UPSELL_PRICE = Decimal("99.00")
# Thank-you page: one pack added to the same order (same delivery, same confirmation call).
ADDON_PRICE = Decimal("149.00")
ADDON_WINDOW_HOURS = 2
MAX_ADDONS = 2

# Post-checkout upsell target per product. Upsells and cross-sells only offer the capsule line
# (turmeric, hyaluronic, moringa); oregano is still sold on its own page.
UPSELL_TARGET: dict[str, str] = {
    "turmeric-golden": "hyaluronic-acid",
    "hyaluronic-acid": "turmeric-golden",
    "moringa": "turmeric-golden",
    "thyme-blackseed": "turmeric-golden",
}

CROSS_SELL: dict[str, list[str]] = {
    "turmeric-golden": ["hyaluronic-acid", "moringa"],
    "hyaluronic-acid": ["turmeric-golden", "moringa"],
    "moringa": ["turmeric-golden", "hyaluronic-acid"],
    "thyme-blackseed": ["turmeric-golden", "hyaluronic-acid"],
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
