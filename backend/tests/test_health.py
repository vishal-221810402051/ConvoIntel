"""Health endpoint tests."""

import importlib

from fastapi.testclient import TestClient


def test_application_imports_successfully() -> None:
    main = importlib.import_module("backend.app.main")

    assert main.app.title == "Convointel Backend"


def test_health_endpoint_contract() -> None:
    main = importlib.import_module("backend.app.main")

    with TestClient(main.app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "convointel-backend",
        "api_version": "v1",
    }
    assert response.headers["content-type"].startswith("application/json")
