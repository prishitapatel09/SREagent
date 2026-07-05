from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_checkout_succeeds():
    response = client.post("/checkout", json={"items": [1, 2], "amount_cents": 1899})
    assert response.status_code == 200
    assert response.json()["receipt"]["charged"] is True


def test_checkout_rejects_bad_payload():
    response = client.post("/checkout", json={"items": "nope"})
    assert response.status_code == 422
