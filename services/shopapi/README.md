# shopapi

A small online-store API. Serves the product catalog, checkout, and order status.

## API

```bash
# List products
curl http://localhost:8000/products

# Product detail
curl http://localhost:8000/products/3

# Checkout
curl -X POST http://localhost:8000/checkout \
  -H 'content-type: application/json' \
  -d '{"items": [1, 4], "amount_cents": 2599}'

# Order status
curl http://localhost:8000/orders/17
```

## Operations

- Metrics: `GET /metrics` (Prometheus exposition)
- Health: `GET /healthz`
- Feature flags: `GET /admin/flags`, `POST /admin/flags/{flag}` with `{"enabled": true}`

Logs are JSON lines written to `$LOG_PATH` (default `/var/log/shopapi/app.log`).
