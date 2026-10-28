"""
Tests for Demo Application: health probes, metrics, endpoints, and controlled fault injection.
"""

from fastapi.testclient import TestClient
from healkube.app.main import app, fault_state
import healkube.app.main as demo_module

client = TestClient(app)


def test_health_probe_always_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_ready_probe():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_normal_data_request():
    # Ensure faults are cleared
    demo_module.ENABLE_FAULT_INJECTION = True
    client.post("/fault/reset")

    response = client.get("/api/data")
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_metrics_endpoint_exposes_prometheus_format():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "healkube_demo_requests_total" in response.text


def test_fault_injection_produces_500_while_health_probe_remains_200():
    demo_module.ENABLE_FAULT_INJECTION = True

    # Inject 100% error rate
    inject_resp = client.post(
        "/fault/inject",
        json={"error_rate": 1.0, "extra_latency_seconds": 0.0},
    )
    assert inject_resp.status_code == 200

    # Business endpoint now returns 500
    data_resp = client.get("/api/data")
    assert data_resp.status_code == 500

    # CRUCIAL DEMO PRINCIPLE: Basic liveness probe REMAINS 200 OK!
    # This proves Kubernetes native liveness check alone cannot detect application degradation!
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "alive"

    # Reset fault
    reset_resp = client.post("/fault/reset")
    assert reset_resp.status_code == 200

    # Data endpoint recovers
    recovered_resp = client.get("/api/data")
    assert recovered_resp.status_code == 200


def test_dependency_health_and_simulation():
    demo_module.ENABLE_FAULT_INJECTION = True
    client.post("/fault/reset")

    # Normally healthy
    resp_healthy = client.get("/dependency-health")
    assert resp_healthy.status_code == 200
    assert resp_healthy.json()["status"] == "healthy"

    # Simulate dependency down
    client.post(
        "/fault/inject",
        json={"simulate_dependency_down": True},
    )
    resp_down = client.get("/dependency-health")
    assert resp_down.status_code == 503
    assert resp_down.json()["status"] == "unhealthy"

    client.post("/fault/reset")
