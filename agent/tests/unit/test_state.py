import pytest

from app.events import EventBus
from app.state import InvalidTransition, StateMachine
from app.store import Store


async def _machine(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    bus = EventBus(store)
    await store.create_incident({
        "id": "inc-1", "created_at": "2026-07-04T18:00:00+00:00",
        "status": "detected", "fingerprint": "abc", "alert_json": "{}",
    })
    return store, StateMachine(store, bus)


async def test_happy_path_chain(tmp_path):
    store, machine = await _machine(tmp_path)
    for target in ("investigating", "diagnosed", "resolved", "postmortem_published"):
        await machine.advance("inc-1", target)
    assert store.get_incident("inc-1")["status"] == "postmortem_published"
    types = [e["type"] for e in store.events_for("inc-1")]
    assert types == ["state_changed"] * 4


async def test_illegal_transition_raises(tmp_path):
    store, machine = await _machine(tmp_path)
    with pytest.raises(InvalidTransition):
        await machine.advance("inc-1", "resolved")  # detected -> resolved is not allowed
    assert store.get_incident("inc-1")["status"] == "detected"


async def test_resolved_stamps_timestamp(tmp_path):
    store, machine = await _machine(tmp_path)
    await machine.advance("inc-1", "investigating")
    await machine.advance("inc-1", "diagnosed")
    await machine.advance("inc-1", "resolved")
    assert store.get_incident("inc-1")["resolved_at"] is not None
