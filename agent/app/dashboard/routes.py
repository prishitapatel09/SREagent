"""Dashboard read API + the SSE event stream."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..investigation.llm import llm_reachable

router = APIRouter()

HEARTBEAT_S = 15.0


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    runtime = request.app.state.runtime
    reachable = await asyncio.to_thread(llm_reachable, runtime.settings)
    return {"ok": True, "mode": runtime.settings.agent_mode, "llm_reachable": reachable}


@router.get("/api/incidents")
async def list_incidents(request: Request) -> list[dict]:
    return request.app.state.runtime.store.list_incidents()


@router.get("/api/cursor")
async def cursor(request: Request) -> dict:
    """Current max event seq — the dashboard opens its stream from here so
    nothing emitted between page load and stream-attach is ever missed."""
    return {"seq": request.app.state.runtime.store.max_global_seq()}


@router.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request) -> dict:
    store = request.app.state.runtime.store
    incident = store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return {"incident": incident, "events": store.events_for(incident_id)}


@router.get("/api/incidents/{incident_id}/postmortem")
async def get_postmortem(incident_id: str, request: Request) -> PlainTextResponse:
    incident = request.app.state.runtime.store.get_incident(incident_id)
    if incident is None or not incident.get("postmortem_md"):
        raise HTTPException(status_code=404, detail="no postmortem for this incident")
    return PlainTextResponse(incident["postmortem_md"], media_type="text/markdown")


def _sse(envelope: dict) -> str:
    return f"id: {envelope['global_seq']}\ndata: {json.dumps(envelope)}\n\n"


@router.get("/api/stream")
async def stream(request: Request, incident_id: str | None = None,
                 after: int | None = None) -> StreamingResponse:
    runtime = request.app.state.runtime

    async def generate():
        queue = runtime.bus.subscribe()
        try:
            # Replay anything missed since the client's Last-Event-ID (set by
            # the browser on reconnect) or the ?after= cursor (set on first
            # connect) — this is what makes the stream lossless.
            last_id = request.headers.get("last-event-id", "")
            if not last_id.isdigit() and after is not None:
                last_id = str(after)
            if last_id.isdigit():
                for envelope in runtime.store.events_after(int(last_id)):
                    if incident_id and envelope["incident_id"] != incident_id:
                        continue
                    yield _sse(envelope)
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if incident_id and envelope["incident_id"] != incident_id:
                    continue
                yield _sse(envelope)
        finally:
            runtime.bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
