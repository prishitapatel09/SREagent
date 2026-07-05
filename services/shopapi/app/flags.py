"""In-memory feature-flag registry with an admin API.

Each flag gates a code path that shipped in a specific commit, so enabling
one simulates a rollout activating latent bad code.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .logging_setup import log

FLAGS = {
    "payments_v2": False,        # route checkout through the new payments client
    "listing_inventory": False,  # show live stock counts on the product listing
    "order_timestamps": False,   # use the centralized timestamp parser
}


def enabled(name: str) -> bool:
    return FLAGS.get(name, False)


router = APIRouter(prefix="/admin")


class FlagUpdate(BaseModel):
    enabled: bool


@router.get("/flags")
def get_flags() -> dict:
    return FLAGS


@router.post("/flags/{flag}")
def set_flag(flag: str, update: FlagUpdate) -> dict:
    if flag not in FLAGS:
        raise HTTPException(status_code=404, detail=f"unknown flag: {flag}")
    FLAGS[flag] = update.enabled
    log(
        "INFO",
        event="flag_change",
        flag=flag,
        enabled=update.enabled,
        message=f"feature flag {flag} set to {update.enabled}",
    )
    return {flag: FLAGS[flag]}
