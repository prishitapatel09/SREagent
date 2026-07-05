"""Product catalog: listing and detail."""

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
    return {"products": [dict(product) for product in PRODUCTS]}


def get_product(product_id: int):
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    return None
