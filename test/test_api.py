import os

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    token = os.getenv("API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}

def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_returns_all_flags(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "postgres" in data
    assert "redis" in data
    assert "ml_service" in data
    assert "ndtp_tcp" in data

def test_list_vehicles(client):
    r = client.get("/vehicles/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_vehicle_requires_auth(client):
    token = os.getenv("API_TOKEN", "")
    if not token:
        pytest.skip("API_TOKEN не задан, авторизация отключена")

    r = client.post("/vehicles/", json={
        "unit_id": 111111,
        "vehicle_id": "test-noauth",
        "route_id": "test-route",
        "active": True,
    })
    assert r.status_code == 401


def test_create_vehicle_with_auth(client, auth_headers):
    token = os.getenv("API_TOKEN", "")
    if not token:
        pytest.skip("API_TOKEN не задан")

    r = client.post(
        "/vehicles/",
        json={
            "unit_id": 777001,
            "vehicle_id": "test-bus-api",
            "route_id": "test-route-api",
            "active": True,
        },
        headers=auth_headers,
    )
    assert r.status_code in (200, 201)
    data = r.json()
    assert data["unit_id"] == 777001
    assert data["vehicle_id"] == "test-bus-api"


def test_get_vehicle_404(client):
    r = client.get("/vehicles/999999999")
    assert r.status_code == 404

def test_predictions_latest_unknown_route(client):
    r = client.get("/predictions/route-does-not-exist/latest")
    assert r.status_code == 404


def test_predictions_unknown_route(client):
    r = client.get("/predictions/route-does-not-exist")
    assert r.status_code == 404

def test_schedules_unknown_route(client):
    r = client.get("/schedules/route-does-not-exist")
    assert r.status_code == 200
    assert r.json() == []