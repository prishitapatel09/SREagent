"""Steady, realistic traffic against shopapi so metrics and impact numbers mean something."""

import asyncio
import os
import random
import time

import httpx

TARGET = os.environ.get("TARGET_URL", "http://shopapi:8000")
RPS = float(os.environ.get("RPS", "10"))

# (weight, kind) — mirrors a plausible store traffic mix
MIX = [
    (0.60, "list_products"),
    (0.20, "product_detail"),
    (0.12, "checkout"),
    (0.08, "order_status"),
]

counts = {"2xx": 0, "4xx": 0, "5xx": 0, "error": 0}


async def hit(client: httpx.AsyncClient, kind: str) -> None:
    try:
        if kind == "list_products":
            response = await client.get("/products")
        elif kind == "product_detail":
            response = await client.get(f"/products/{random.randint(1, 20)}")
        elif kind == "checkout":
            items = random.sample(range(1, 21), k=random.randint(1, 3))
            response = await client.post(
                "/checkout",
                json={"items": items, "amount_cents": random.randint(900, 9900)},
            )
        else:
            response = await client.get(f"/orders/{random.randint(1, 50)}")
        counts[f"{response.status_code // 100}xx"] = (
            counts.get(f"{response.status_code // 100}xx", 0) + 1
        )
    except httpx.HTTPError:
        counts["error"] += 1  # 5xx and timeouts are the point during incidents


async def report() -> None:
    while True:
        await asyncio.sleep(30)
        total = sum(counts.values())
        print(f"[loadgen] last totals: {counts} ({total} requests)", flush=True)


async def main() -> None:
    print(f"[loadgen] target={TARGET} rps={RPS}", flush=True)
    async with httpx.AsyncClient(base_url=TARGET, timeout=10.0) as client:
        asyncio.ensure_future(report())
        while True:
            weights, kinds = zip(*[(w, k) for w, k in MIX])
            kind = random.choices(kinds, weights=weights)[0]
            asyncio.ensure_future(hit(client, kind))
            # jittered pacing around the target RPS
            await asyncio.sleep(random.uniform(0.8, 1.2) / RPS)


if __name__ == "__main__":
    asyncio.run(main())
