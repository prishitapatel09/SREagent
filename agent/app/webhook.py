"""Alertmanager webhook: parse, dedup by fingerprint, route firing/resolved.

Returns 200 immediately — the investigation runs as a background task, since
Alertmanager retries slow webhook deliveries.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from .events import now_iso
from .models import AlertInfo

router = APIRouter()


def parse_alert(alert: dict) -> AlertInfo:
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    return AlertInfo(
        fingerprint=alert.get("fingerprint", ""),
        alertname=labels.get("alertname", "UnknownAlert"),
        service=labels.get("service", ""),
        severity=labels.get("severity", ""),
        endpoint=labels.get("endpoint", ""),
        summary=annotations.get("summary", ""),
        description=annotations.get("description", ""),
        starts_at=alert.get("startsAt", ""),
    )


@router.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request) -> dict:
    payload = await request.json()
    runtime = request.app.state.runtime
    results = []
    for raw_alert in payload.get("alerts", []):
        info = parse_alert(raw_alert)
        if not info.fingerprint:
            continue
        if raw_alert.get("status") == "resolved":
            results.append(await _handle_resolved(runtime, info))
        else:
            results.append(await _handle_firing(runtime, info))
    return {"processed": results}


async def _handle_firing(runtime, info: AlertInfo) -> dict:
    existing = runtime.store.find_unresolved_by_fingerprint(info.fingerprint)
    if existing:
        return {"deduped": existing["id"]}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    incident_id = f"inc-{stamp}-{uuid.uuid4().hex[:4]}"
    title = f"{info.alertname} on {info.endpoint or info.service or 'unknown'}"
    await runtime.store.create_incident({
        "id": incident_id,
        "created_at": now_iso(),
        "status": "detected",
        "service": info.service or "unknown",
        "severity": info.severity or "unknown",
        "title": title,
        "fingerprint": info.fingerprint,
        "alert_json": info.model_dump_json(),
    })
    await runtime.bus.emit(incident_id, "alert_received", info.model_dump())
    runtime.spawn(runtime.investigator.run(incident_id))
    return {"created": incident_id}


async def _handle_resolved(runtime, info: AlertInfo) -> dict:
    incident = runtime.store.find_unresolved_by_fingerprint(info.fingerprint)
    if incident is None:
        return {"ignored": info.fingerprint}
    await runtime.store.update_incident(incident["id"], resolved_at=now_iso())
    await runtime.bus.emit(incident["id"], "alert_resolved", {"fingerprint": info.fingerprint})
    # If diagnosis already landed, this completes resolved -> postmortem.
    # If the investigation is still running, its own maybe_finalize will.
    # Backgrounded: postmortem generation includes LLM calls, and Alertmanager
    # retries slow webhook deliveries.
    runtime.spawn(runtime.investigator.maybe_finalize(incident["id"]))
    return {"resolved": incident["id"]}
