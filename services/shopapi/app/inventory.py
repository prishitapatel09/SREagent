"""Live stock lookup against the (simulated) inventory service.

Each call is a synchronous round trip to a downstream system.
"""

import random
import time

from .logging_setup import log

_LOOKUP_LATENCY_S = (0.08, 0.12)


def get_stock(product_id: int) -> int:
    took = random.uniform(*_LOOKUP_LATENCY_S)
    time.sleep(took)
    took_ms = int(took * 1000)
    if took_ms > 100:
        log(
            "WARN",
            endpoint="/products",
            message=f"inventory lookup for product {product_id} took {took_ms}ms",
        )
    return (product_id * 7919) % 42
