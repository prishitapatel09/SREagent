"""The CI gate: does the whole pipeline still hang together in stub mode?

No LLM, no docker, no network — runs in seconds on every push.
"""

from fastapi.testclient import TestClient

from ..integration.test_webhook_to_incident import wait_for_status


def test_alert_to_postmortem(app, firing_payload, resolved_payload):
    with TestClient(app) as client:
        assert client.get("/healthz").json()["ok"] is True

        response = client.post("/webhook/alertmanager", json=firing_payload)
        incident_id = response.json()["processed"][0]["created"]

        wait_for_status(client, incident_id, "diagnosed")
        client.post("/webhook/alertmanager", json=resolved_payload)
        data = wait_for_status(client, incident_id, "postmortem_published")

        assert data["incident"]["postmortem_md"]
        assert data["incident"]["diagnosis"]["suspect_commit"] is not None
