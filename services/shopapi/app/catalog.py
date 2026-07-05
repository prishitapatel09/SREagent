"""Product catalog: listing and detail."""

from fastapi import HTTPException

from . import flags, inventory

_NAMES = [
    "Trail Mug", "Canvas Tote", "Desk Lamp", "Wool Beanie", "Field Notebook",
    "Enamel Pin", "Water Bottle", "Phone Stand", "Coaster Set", "Key Organizer",
    "Camp Blanket", "Pocket Knife", "Travel Candle", "Sticker Pack", "Belt Bag",
    "Dopp Kit", "Bike Bell", "Puzzle Cube", "Herb Planter", "Card Wallet",
]

PRODUCTS = [
    {"id": i + 1, "name": name, "price_cents": 900 + ((i * 731) % 4200)}
    for i, name in enumerate(_NAMES)
]


def list_products() -> dict:
    items = []
    for product in PRODUCTS:
        item = dict(product)
        if flags.enabled("listing_inventory"):
            # Enrich each product with a live stock count.
            item["in_stock"] = inventory.get_stock(product["id"])
        items.append(item)
    return {"products": items}


def get_product(product_id: int) -> dict:
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    raise HTTPException(status_code=404, detail="product not found")
