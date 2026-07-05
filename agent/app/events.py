"""EventBus: persist every event, then fan it out to live SSE subscribers.

Persist-before-fanout is the ordering guarantee the dashboard relies on —
a reconnecting client replays from SQLite (Last-Event-ID) and can never
miss an event that a live subscriber saw.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from .store import Store


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventBus:
    def __init__(self, store: Store):
        self._store = store
        self._subscribers: set[asyncio.Queue] = set()

    async def emit(self, incident_id: str, type_: str, payload: dict) -> dict:
        envelope = {
            "event_id": uuid.uuid4().hex[:8],
            "incident_id": incident_id,
            "ts": now_iso(),
            "type": type_,
            "payload": payload,
        }
        seq, global_seq = await self._store.append_event(
            envelope["event_id"], incident_id, envelope["ts"], type_, payload
        )
        envelope["seq"] = seq
        envelope["global_seq"] = global_seq
        for queue in list(self._subscribers):
            queue.put_nowait(envelope)
        return envelope

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
