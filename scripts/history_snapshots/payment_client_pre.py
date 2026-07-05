"""Payment gateway client."""

import time


class PaymentGatewayError(Exception):
    pass


def charge(order: dict) -> dict:
    return _charge_legacy(order)


def _charge_legacy(order: dict) -> dict:
    time.sleep(0.03)  # simulated gateway round trip
    return {"charged": True, "provider": "legacy", "amount_cents": order["amount_cents"]}
