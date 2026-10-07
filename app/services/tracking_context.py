from dataclasses import dataclass, field


@dataclass
class TrackedItem:
    slug: str
    quantity: int
    unit_price: float
    total_price: float


@dataclass
class TrackingContext:
    """Everything a CAPI call needs, captured at request time (no DB access in background tasks)."""

    order_id: int
    order_number: str
    event_id: str
    phone: str
    customer_name: str
    city: str
    ip_address: str
    user_agent: str
    page_url: str
    referrer: str
    fbc: str
    fbp: str
    ttclid: str
    ttp: str
    sclid: str
    items: list[TrackedItem] = field(default_factory=list)

    @property
    def content_ids(self) -> list[str]:
        return [i.slug for i in self.items]

    @property
    def num_items(self) -> int:
        return sum(i.quantity for i in self.items)

    @property
    def value(self) -> float:
        return round(sum(i.total_price for i in self.items), 2)
