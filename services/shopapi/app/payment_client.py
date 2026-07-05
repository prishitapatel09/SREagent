"""Payment gateway clients: legacy (v1) and the new v2 client."""

import time

import httpx

from . import flags

PAYMENTS_V2_URL = "http://payments-v2.internal:9443/charge"


class PaymentGatewayError(Exception):
    pass


def charge(order: dict) -> dict:
    if flags.enabled("payments_v2"):
        return charge_v2(order)
    return _charge_legacy(order)


def _charge_legacy(order: dict) -> dict:
    time.sleep(0.03)  # simulated gateway round trip
    return {"charged": True, "provider": "legacy", "amount_cents": order["amount_cents"]}


def charge_v2(order: dict) -> dict:
    try:
        response = httpx.post(PAYMENTS_V2_URL, json=order, timeout=0.5)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaymentGatewayError(f"payments-v2 gateway unreachable: {exc}") from exc
    return response.json()
