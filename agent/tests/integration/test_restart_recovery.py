"""Startup recovery: an incident stranded mid-investigation by an agent
restart must be re-investigated — not left deduping every future webhook."""

import asyncio

from fastapi.testclient import TestClient

from app.store import Store
from app.webhook import parse_alert

from .test_webhook_to_incident import wait_for_status


def test_stranded_incident_is_reinvestigated(settings, app_factory, firing_payload,
                                             demo_repo):
    # Simulate the "old" process dying mid-investigation: a row exists in
    # 'investigating' but no asyncio task is running for it.
    alert = parse_alert(firing_payload["alerts"][0])
    setup_store = Store(settings.db_path)
    asyncio.run(setup_store.create_incident({
        "id": "inc-stranded-0001",
        "created_at": "2026-07-04T18:30:12.000+00:00",
        "status": "investigating",
        "service": "shopapi",
        "severity": "critical",
        "title": "HighErrorRate on /checkout",
        "fingerprint": alert.fingerprint,
        "alert_json": alert.model_dump_json(),
    }))

    # "Restart" the agent: a fresh app over the same database.
    with TestClient(app_factory()) as client:
        data = wait_for_status(client, "inc-stranded-0001", "diagnosed")

        # The recovered investigation produced a real diagnosis...
        assert data["incident"]["diagnosis"]["suspect_commit"]["sha"] == demo_repo.bad_sha

        # ...the restart left an audit trail...
        recovery = [e for e in data["events"]
                    if e["type"] == "agent_error" and e["payload"]["stage"] == "recovery"]
        assert recovery and recovery[0]["payload"]["recovered"] is True

        # ...and the fingerprint still dedups instead of double-opening.
        repeat = client.post("/webhook/alertmanager", json=firing_payload)
        assert repeat.json()["processed"][0] == {"deduped": "inc-stranded-0001"}
