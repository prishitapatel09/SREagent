"""shopapi — a small online-store API used as the breakable demo service."""

import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import catalog, flags, logging_setup, metrics, order_utils, payment_client
from .logging_setup import log


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging_setup.init()
    log("INFO", event="startup", message="shopapi started")
    yield


app = FastAPI(title="shopapi", lifespan=lifespan)
app.middleware("http")(metrics.middleware)
app.include_router(flags.router)


@app.get("/products")
def products() -> dict:
    return catalog.list_products()


@app.get("/products/{product_id}")
def product(product_id: int) -> dict:
    return catalog.get_product(product_id)


class CheckoutRequest(BaseModel):
    items: list[int]
    amount_cents: int


@app.post("/checkout")
def checkout(request: CheckoutRequest):
    order = {"items": request.items, "amount_cents": request.amount_cents}
    try:
        receipt = payment_client.charge(order)
    except payment_client.PaymentGatewayError:
        log(
            "ERROR",
            endpoint="/checkout",
            method="POST",
            status=502,
            message="payment charge failed",
            exc_type="PaymentGatewayError",
            stack=traceback.format_exc(),
        )
        return JSONResponse(status_code=502, content={"detail": "payment gateway error"})
    return {"order": order, "receipt": receipt}


@app.get("/orders/{order_id}")
def order(order_id: int):
    try:
        return order_utils.order_status(order_id)
    except HTTPException:
        raise
    except Exception as exc:
        log(
            "ERROR",
            endpoint="/orders/{order_id}",
            method="GET",
            status=500,
            message="failed to compute order status",
            exc_type=type(exc).__name__,
            stack=traceback.format_exc(),
        )
        return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/metrics")
def metrics_endpoint() -> Response:
    payload, content_type = metrics.exposition()
    return Response(content=payload, media_type=content_type)
