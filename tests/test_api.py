from fastapi.testclient import TestClient
from megaproject.app import app

client = TestClient(app)


def test_homepage_exposes_demo_and_docs():
    response = client.get("/")
    assert response.status_code == 200
    assert "Sandboxed Tool Agent" in response.text
    assert "/docs" in response.text


def test_run_can_read_demo_file():
    response = client.post("/run", json={"steps": [{"tool": "read", "args": {"path": "demo.txt"}}]})
    assert response.status_code == 200
    assert "read-only workspace" in response.text


def test_workspace_escape_returns_client_error():
    response = client.post("/run", json={"steps": [{"tool": "read", "args": {"path": "../README.md"}}]})
    assert response.status_code == 400


def test_too_many_steps_are_rejected():
    response = client.post("/run", json={"steps": [{"tool": "list", "args": {}}] * 5})
    assert response.status_code == 422


def test_unknown_tool_is_rejected_before_dispatch():
    response = client.post("/run", json={"steps": [{"tool": "execute", "args": {"command": "id"}}]})
    assert response.status_code == 422


def test_metrics_count_route_errors():
    client.post("/run", json={"steps": [{"tool": "read", "args": {"path": "../README.md"}}]})
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json()["routes"]["POST /run"]["errors_since_start"] >= 1


def test_unmatched_paths_do_not_create_unbounded_metric_labels():
    client = TestClient(app)
    client.get("/unmatched-a")
    client.get("/unmatched-b")
    routes = client.get("/metrics").json()["routes"]
    assert "GET <unmatched>" in routes
    assert "GET /unmatched-a" not in routes
    assert "GET /unmatched-b" not in routes
