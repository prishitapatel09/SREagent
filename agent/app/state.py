"""Incident state machine. The whole policy is the ALLOWED set below."""

from .events import EventBus, now_iso
from .store import Store

ALLOWED = {
    ("detected", "investigating"),
    ("investigating", "diagnosed"),
    ("diagnosed", "resolved"),
    ("resolved", "postmortem_published"),
}


class InvalidTransition(Exception):
    pass


class StateMachine:
    def __init__(self, store: Store, bus: EventBus):
        self._store = store
        self._bus = bus

    async def advance(self, incident_id: str, to_status: str) -> None:
        incident = self._store.get_incident(incident_id)
        if incident is None:
            raise InvalidTransition(f"unknown incident {incident_id}")
        from_status = incident["status"]
        if (from_status, to_status) not in ALLOWED:
            raise InvalidTransition(f"{from_status} -> {to_status}")
        fields = {"status": to_status}
        if to_status == "resolved" and not incident["resolved_at"]:
            fields["resolved_at"] = now_iso()
        await self._store.update_incident(incident_id, **fields)
        await self._bus.emit(
            incident_id, "state_changed", {"from": from_status, "to": to_status}
        )
