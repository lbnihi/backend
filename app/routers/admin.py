import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.errors import ApiError
from app.models import Order, Visit
from app.services.auth import create_token, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/login")
async def login(body: dict) -> dict:
    username = body.get("username", "")
    password = body.get("password", "")
    if (
        not settings.admin_username
        or username != settings.admin_username
        or password != settings.admin_password
    ):
        raise ApiError(401, "unauthorized", "بيانات خاطئة", "Invalid credentials")
    return {"token": create_token(username)}


@router.get("/metrics", dependencies=[Depends(require_admin)])
async def get_metrics(
    start: date = Query(default=None),
    end: date = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Dashboard overview metrics."""
    # Default to last 30 days
    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=29)

    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)

    # Valid clicks (KSA, non-VPN)
    valid_clicks = await db.scalar(
        select(func.count(Visit.id)).where(
            Visit.created_at.between(start_dt, end_dt),
            Visit.is_vpn.is_(False),
            Visit.country_code == "SA",
        )
    ) or 0

    # Unique visitors (by IP, valid only)
    unique_visitors = await db.scalar(
        select(func.count(func.distinct(Visit.ip_address))).where(
            Visit.created_at.between(start_dt, end_dt),
            Visit.is_vpn.is_(False),
            Visit.country_code == "SA",
        )
    ) or 0

    # Orders
    total_orders = await db.scalar(
        select(func.count(Order.id)).where(Order.created_at.between(start_dt, end_dt))
    ) or 0

    revenue_result = await db.scalar(
        select(func.coalesce(func.sum(Order.total), 0)).where(Order.created_at.between(start_dt, end_dt))
    )
    total_revenue = float(revenue_result or 0)

    aov = total_revenue / total_orders if total_orders > 0 else 0
    conversion_rate = (total_orders / unique_visitors * 100) if unique_visitors > 0 else 0

    # Orders by status
    status_rows = await db.execute(
        select(Order.status, func.count(Order.id))
        .where(Order.created_at.between(start_dt, end_dt))
        .group_by(Order.status)
    )
    status_breakdown = {row[0]: row[1] for row in status_rows}

    # Upsell stats
    upsell_offered = await db.scalar(
        select(func.count(Order.id)).where(
            Order.created_at.between(start_dt, end_dt),
            Order.upsell_product_slug.isnot(None),
        )
    ) or 0
    upsell_accepted = await db.scalar(
        select(func.count(Order.id)).where(
            Order.created_at.between(start_dt, end_dt),
            Order.upsell_accepted.is_(True),
        )
    ) or 0
    upsell_revenue_result = await db.scalar(
        select(func.coalesce(func.sum(Order.upsell_amount), 0)).where(
            Order.created_at.between(start_dt, end_dt),
            Order.upsell_accepted.is_(True),
        )
    )
    upsell_revenue = float(upsell_revenue_result or 0)

    # Daily chart data
    daily_clicks = await db.execute(
        select(
            func.date(Visit.created_at).label("day"),
            func.count(Visit.id),
            func.count(func.distinct(Visit.ip_address)),
        )
        .where(
            Visit.created_at.between(start_dt, end_dt),
            Visit.is_vpn.is_(False),
            Visit.country_code == "SA",
        )
        .group_by("day")
        .order_by("day")
    )
    clicks_by_day = {str(row[0]): {"clicks": row[1], "unique": row[2]} for row in daily_clicks}

    daily_orders = await db.execute(
        select(
            func.date(Order.created_at).label("day"),
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        )
        .where(Order.created_at.between(start_dt, end_dt))
        .group_by("day")
        .order_by("day")
    )
    orders_by_day = {str(row[0]): {"orders": row[1], "revenue": float(row[2])} for row in daily_orders}

    # Merge into daily array
    all_days = sorted(set(list(clicks_by_day.keys()) + list(orders_by_day.keys())))
    daily = []
    for day in all_days:
        c = clicks_by_day.get(day, {"clicks": 0, "unique": 0})
        o = orders_by_day.get(day, {"orders": 0, "revenue": 0})
        daily.append({
            "date": day,
            "clicks": c["clicks"],
            "unique_visitors": c["unique"],
            "orders": o["orders"],
            "revenue": o["revenue"],
            "conversion": round(o["orders"] / c["unique"] * 100, 1) if c["unique"] > 0 else 0,
        })

    # Top sources. The default is a SQL literal, not a bound parameter: Postgres treats two
    # parameters as different expressions and rejects the GROUP BY.
    source = func.coalesce(Order.utm_source, literal_column("'direct'")).label("source")
    source_rows = await db.execute(
        select(
            source,
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        )
        .where(Order.created_at.between(start_dt, end_dt))
        .group_by(source)
        .order_by(func.count(Order.id).desc())
        .limit(10)
    )
    top_sources = [{"source": row[0], "orders": row[1], "revenue": float(row[2])} for row in source_rows]

    # Top products — aggregate from JSONB items array
    product_sales: dict[str, dict] = {}
    orders_result = await db.execute(
        select(Order.items, Order.total).where(Order.created_at.between(start_dt, end_dt))
    )
    for items, total in orders_result:
        for item in items:
            slug = item.get("product_slug", "unknown")
            name = item.get("product_name", slug)
            if slug not in product_sales:
                product_sales[slug] = {"name": name, "orders": 0, "units": 0, "revenue": 0}
            product_sales[slug]["orders"] += 1
            product_sales[slug]["units"] += item.get("quantity", 1)
            product_sales[slug]["revenue"] += item.get("total_price", 0)
    top_products = sorted(product_sales.values(), key=lambda x: x["revenue"], reverse=True)

    # Top cities
    city = func.coalesce(Order.city, literal_column("'Unknown'")).label("city")
    city_rows = await db.execute(
        select(
            city,
            func.count(Order.id),
        )
        .where(Order.created_at.between(start_dt, end_dt))
        .group_by(city)
        .order_by(func.count(Order.id).desc())
        .limit(10)
    )
    top_cities = [{"city": row[0], "orders": row[1]} for row in city_rows]

    return {
        "period": {"start": str(start), "end": str(end)},
        "clicks": valid_clicks,
        "unique_visitors": unique_visitors,
        "orders": total_orders,
        "revenue": round(total_revenue, 2),
        "aov": round(aov, 2),
        "conversion_rate": round(conversion_rate, 2),
        "status_breakdown": status_breakdown,
        "upsell": {
            "offered": upsell_offered,
            "accepted": upsell_accepted,
            "rate": round(upsell_accepted / upsell_offered * 100, 1) if upsell_offered > 0 else 0,
            "revenue": round(upsell_revenue, 2),
        },
        "daily": daily,
        "top_sources": top_sources,
        "top_products": top_products,
        "top_cities": top_cities,
    }


@router.get("/orders", dependencies=[Depends(require_admin)])
async def list_orders(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    status: str = Query(default=""),
    search: str = Query(default=""),
    start: date = Query(default=None),
    end: date = Query(default=None),
    source: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Paginated order list with filters."""
    query = select(Order)
    count_query = select(func.count(Order.id))

    # Filters
    if status:
        query = query.where(Order.status == status)
        count_query = count_query.where(Order.status == status)
    if search:
        search_filter = (
            Order.order_number.ilike(f"%{search}%")
            | Order.customer_name.ilike(f"%{search}%")
            | Order.phone.ilike(f"%{search}%")
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    if start:
        start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        query = query.where(Order.created_at >= start_dt)
        count_query = count_query.where(Order.created_at >= start_dt)
    if end:
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
        query = query.where(Order.created_at <= end_dt)
        count_query = count_query.where(Order.created_at <= end_dt)
    if source:
        query = query.where(Order.utm_source == source)
        count_query = count_query.where(Order.utm_source == source)

    total = await db.scalar(count_query) or 0
    offset = (page - 1) * per_page
    orders = await db.scalars(
        query.order_by(Order.created_at.desc()).offset(offset).limit(per_page)
    )

    return {
        "orders": [
            {
                "id": o.id,
                "order_number": o.order_number,
                "customer_name": o.customer_name,
                "phone": o.phone,
                "status": o.status,
                "city": o.city or "",
                "items": o.items,
                "subtotal": float(o.subtotal),
                "upsell_accepted": o.upsell_accepted,
                "upsell_amount": float(o.upsell_amount) if o.upsell_accepted else 0,
                "total": float(o.total),
                "utm_source": o.utm_source or "",
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/orders/{order_id}", dependencies=[Depends(require_admin)])
async def get_order_detail(order_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Full order detail for admin view."""
    order = await db.get(Order, order_id)
    if order is None:
        raise ApiError(404, "not_found", "الطلب غير موجود", "Order not found")

    return {
        "id": order.id,
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "phone": order.phone,
        "status": order.status,
        "ip_address": order.ip_address,
        "country_code": order.country_code,
        "city": order.city,
        "is_vpn": order.is_vpn,
        "items": order.items,
        "subtotal": float(order.subtotal),
        "upsell_product_slug": order.upsell_product_slug,
        "upsell_accepted": order.upsell_accepted,
        "upsell_amount": float(order.upsell_amount),
        "upsell_decided": order.upsell_decided,
        "total": float(order.total),
        "utm_source": order.utm_source,
        "utm_medium": order.utm_medium,
        "utm_campaign": order.utm_campaign,
        "utm_content": order.utm_content,
        "utm_term": order.utm_term,
        "fbclid": order.fbclid,
        "ttclid": order.ttclid,
        "sclid": order.sclid,
        "user_agent": order.user_agent,
        "page_url": order.page_url,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


@router.patch("/orders/{order_id}/status", dependencies=[Depends(require_admin)])
async def update_order_status(order_id: int, body: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """Update order status."""
    new_status = body.get("status", "")
    valid_statuses = {"pending", "confirmed", "shipped", "delivered", "cancelled"}
    if new_status not in valid_statuses:
        raise ApiError(400, "invalid_status", "حالة غير صالحة", f"Status must be one of: {', '.join(valid_statuses)}")

    order = await db.get(Order, order_id)
    if order is None:
        raise ApiError(404, "not_found", "الطلب غير موجود", "Order not found")

    order.status = new_status
    await db.commit()
    await db.refresh(order)

    return {"success": True, "order_number": order.order_number, "status": order.status}
