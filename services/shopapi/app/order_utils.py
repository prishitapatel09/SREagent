"""Order storage and status formatting."""

from datetime import datetime

from fastapi import HTTPException

from . import flags


def _seed_orders() -> dict:
    orders = {}
    for i in range(1, 51):
        fulfilled = i % 5 not in (0, 3)  # ~40% of orders are still processing
        orders[i] = {
            "id": i,
            "item_count": (i % 4) + 1,
            "amount_cents": 1299 + i * 37,
            "created_at": f"2026-07-0{(i % 3) + 1}T10:{i % 60:02d}:00+00:00",
            "fulfilled_at": (
                f"2026-07-0{(i % 3) + 1}T14:{i % 60:02d}:00+00:00" if fulfilled else None
            ),
        }
    return orders


ORDERS = _seed_orders()


def order_status(order_id: int) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if flags.enabled("order_timestamps"):
        timestamps = parse_order_timestamps(order)
    else:
        timestamps = {
            "created_at": order["created_at"],
            "fulfilled_at": order["fulfilled_at"],
        }
    return {
        "id": order["id"],
        "status": "fulfilled" if order["fulfilled_at"] else "processing",
        "item_count": order["item_count"],
        "timestamps": timestamps,
    }


def parse_order_timestamps(order: dict) -> dict:
    """Normalize order timestamps to UTC ISO-8601."""
    return {
        "created_at": datetime.fromisoformat(order["created_at"]).isoformat(),
        "fulfilled_at": datetime.fromisoformat(order["fulfilled_at"]).isoformat(),
    }
