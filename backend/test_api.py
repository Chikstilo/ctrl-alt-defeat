"""Тесты API. Запуск: py -m pytest test_api.py -v"""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200


def test_routes_list():
    r = client.get("/routes")
    assert r.status_code == 200
    assert "routes" in r.json()


def test_telemetry_ingest():
    payload = {
        "vehicle_id": "test-bus",
        "route_id": "test-route",
        "lat": 55.75,
        "lon": 37.61,
        "speed": 50.0,
        "timestamp": "2026-09-25T10:00:00",
        "stop_id": "stop-1",
        "dwell_time_seconds": 30.0,
    }
    r = client.post("/telemetry/", json=payload)
    assert r.status_code == 201


def test_invalid_telemetry():
    payload = {"vehicle_id": "", "route_id": "abc!@#", "lat": 999, "lon": 0, "timestamp": "2026-09-25T10:00:00"}
    r = client.post("/telemetry/", json=payload)
    assert r.status_code == 422


def test_prediction_not_found():
    r = client.get("/predictions/nonexistent-route")
    assert r.status_code == 404