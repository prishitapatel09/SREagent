"""End-to-end pipeline in stub mode: webhook fixture in, postmortem out.

The stub LLM drives the real loop against the real tools (fixture git repo,
fixture log file, fake Prometheus, real runbooks) — so this asserts the
actual event protocol and the actual diagnosis content, deterministically.
"""

import time

from fastapi.testclient import TestClient

EXPECTED_TOOL_SEQUENCE = [
    "query_prometheus", "get_service_logs", "search_commits",
    "get_commit_diff", "search_runbooks", "calculate_user_impact",
]


def wait_for_status(client, incident_id, status, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/incidents/{incident_id}").json()
        if data["incident"]["status"] == status:
            return data
        time.sleep(0.05)
    raise AssertionError(
        f"incident never reached {status!r} (last: {data['incident']['status']!r})"
    )


def wait_for_event(client, incident_id, event_type, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/incidents/{incident_id}").json()
        if any(e["type"] == event_type for e in data["events"]):
            return data
        time.sleep(0.05)
    raise AssertionError(f"event {event_type!r} never appeared")


def test_full_pipeline(app, firing_payload, resolved_payload, demo_repo):
    with TestClient(app) as client:
        # 1. Alert fires -> incident created, investigation kicks off
        response = client.post("/webhook/alertmanager", json=firing_payload)
        assert response.status_code == 200
        incident_id = response.json()["processed"][0]["created"]

        # slack_brief_sent is the last event of the investigation, so waiting
        # for it (not just the "diagnosed" status, which lands earlier) makes
        # the event assertions below race-free.
        data = wait_for_event(client, incident_id, "slack_brief_sent")
        diagnosis = data["incident"]["diagnosis"]

        # 2. The diagnosis names the actual planted commit, found via git log -S
        assert diagnosis["suspect_commit"]["sha"] == demo_repo.bad_sha
        assert "payments v2" in diagnosis["suspect_commit"]["message"]
        assert diagnosis["runbook_slug"] == "payment-gateway-outage"
        assert diagnosis["confidence"] == "high"

        # 3. Event protocol: right types, right tool order
        types = [e["type"] for e in data["events"]]
        for expected in ["alert_received", "state_changed", "tool_call",
                         "tool_result", "impact_computed", "diagnosis_ready",
                         "slack_brief_sent"]:
            assert expected in types, f"missing event type {expected}"
        tools = [e["payload"]["tool"] for e in data["events"] if e["type"] == "tool_call"]
        assert tools == EXPECTED_TOOL_SEQUENCE

        # 4. Slack fell back to console (no webhook configured) with a real brief
        slack = next(e for e in data["events"] if e["type"] == "slack_brief_sent")
        assert slack["payload"]["delivered_to"] == "console"
        assert demo_repo.bad_sha in slack["payload"]["text_fallback"]

        # 5. Impact numbers are computed, not hallucinated
        impact = data["incident"]["impact"]
        assert impact["error_rate_pct"] == 30.1
        assert impact["severity_band"] == "major"

        # 6. A repeat firing webhook dedups against the open incident
        repeat = client.post("/webhook/alertmanager", json=firing_payload)
        assert repeat.json()["processed"][0] == {"deduped": incident_id}

        # 7. Resolution -> postmortem published, served over HTTP
        client.post("/webhook/alertmanager", json=resolved_payload)
        wait_for_status(client, incident_id, "postmortem_published")
        postmortem = client.get(f"/api/incidents/{incident_id}/postmortem")
        assert postmortem.status_code == 200
        assert demo_repo.bad_sha in postmortem.text
        assert "## Timeline" in postmortem.text

        # 8. After resolution the fingerprint is free: a new firing opens a new incident
        second = client.post("/webhook/alertmanager", json=firing_payload)
        assert "created" in second.json()["processed"][0]


async def _collect_sse_replay(app) -> str:
    """Drive the SSE generator directly — TestClient can't stream an
    endless response (httpx's ASGI transport buffers the whole body)."""
    from types import SimpleNamespace

    from app.dashboard.routes import stream

    request = SimpleNamespace(app=SimpleNamespace(state=app.state),
                              headers={"last-event-id": "0"})
    response = await stream(request, incident_id=None)
    collected = ""
    iterator = response.body_iterator
    async for frame in iterator:
        collected += frame
        if "diagnosis_ready" in collected:
            break
    await iterator.aclose()
    return collected


def test_sse_stream_replays_history(app, firing_payload):
    with TestClient(app) as client:
        response = client.post("/webhook/alertmanager", json=firing_payload)
        incident_id = response.json()["processed"][0]["created"]
        wait_for_status(client, incident_id, "diagnosed")

    # Reconnect with Last-Event-ID: 0 -> full history replayed as SSE frames
    import asyncio

    collected = asyncio.run(_collect_sse_replay(app))
    assert "alert_received" in collected
    assert "id: 1" in collected
    assert "diagnosis_ready" in collected
