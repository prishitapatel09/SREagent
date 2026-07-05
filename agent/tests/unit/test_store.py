from app.store import Store


async def test_incident_round_trip(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    await store.create_incident({
        "id": "inc-1", "created_at": "2026-07-04T18:00:00+00:00",
        "status": "detected", "service": "shopapi", "severity": "critical",
        "title": "t", "fingerprint": "abc",
        "alert_json": '{"fingerprint": "abc", "alertname": "X"}',
    })
    incident = store.get_incident("inc-1")
    assert incident["status"] == "detected"
    assert incident["alert"]["alertname"] == "X"
    assert incident["diagnosis"] is None

    await store.update_incident("inc-1", status="investigating")
    assert store.get_incident("inc-1")["status"] == "investigating"


async def test_fingerprint_dedup_scope(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    await store.create_incident({
        "id": "inc-1", "created_at": "2026-07-04T18:00:00+00:00",
        "status": "detected", "fingerprint": "abc", "alert_json": "{}",
    })
    assert store.find_unresolved_by_fingerprint("abc")["id"] == "inc-1"
    await store.update_incident("inc-1", resolved_at="2026-07-04T19:00:00+00:00")
    # resolved incidents no longer dedup a re-firing alert
    assert store.find_unresolved_by_fingerprint("abc") is None


async def test_event_replay_and_resume(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    seqs = []
    for n in range(3):
        seq, global_seq = await store.append_event(
            f"e{n}", "inc-1", "2026-07-04T18:00:00+00:00", "tool_call", {"n": n}
        )
        seqs.append((seq, global_seq))

    assert [s for s, _ in seqs] == [1, 2, 3]  # per-incident seq is monotonic

    events = store.events_for("inc-1")
    assert [e["payload"]["n"] for e in events] == [0, 1, 2]

    # Last-Event-ID resume: everything after global_seq of the first event
    resumed = store.events_after(seqs[0][1])
    assert [e["payload"]["n"] for e in resumed] == [1, 2]
