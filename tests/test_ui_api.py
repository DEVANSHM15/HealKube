"""
Unit tests for HealKube Mission Control Web UI and Status API endpoints.
"""

from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from healkube.controller.main import HealKubeController, status_app
import healkube.controller.main as main_module
from healkube.controller.config import ControllerConfig


def test_ui_dashboard_page(mock_config, healthy_snapshot):
    """Verifies that GET / and GET /dashboard render the interactive UI HTML."""
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    controller = HealKubeController(config=mock_config, monitor=mock_monitor)
    main_module.controller_instance = controller

    with TestClient(status_app) as client:
        # Redirect from root
        root_resp = client.get("/", follow_redirects=False)
        assert root_resp.status_code == 307
        assert root_resp.headers["location"] == "/dashboard"

        # Direct dashboard load
        dash_resp = client.get("/dashboard")
        assert dash_resp.status_code == 200
        assert "text/html" in dash_resp.headers["content-type"]
        assert "HealKube Mission Control" in dash_resp.text
        assert "Native Kubelet Liveness vs. Actual User Traffic Health" in dash_resp.text


def test_ui_api_status_and_alias(mock_config, healthy_snapshot):
    """Verifies both /status and /api/status return unified telemetry and audit state."""
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    controller = HealKubeController(config=mock_config, monitor=mock_monitor)
    controller.reconcile_once()
    main_module.controller_instance = controller

    with TestClient(status_app) as client:
        for endpoint in ["/status", "/api/status"]:
            resp = client.get(endpoint)
            assert resp.status_code == 200
            data = resp.json()
            assert data["iteration_count"] == 1
            assert data["config"]["target_namespace"] == "test-ns"
            assert data["config"]["target_deployment"] == "test-app"
            assert "app_base_url" in data["config"]
            assert data["last_diagnosis"]["diagnosis_type"] == "HEALTHY"


def test_ui_api_toggle_mode(mock_config, healthy_snapshot):
    """Verifies POST /api/mode/toggle switches between dry-run and live remediation."""
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    mock_config.dry_run = True
    controller = HealKubeController(config=mock_config, monitor=mock_monitor)
    main_module.controller_instance = controller

    with TestClient(status_app) as client:
        # Initial is dry_run = True -> toggle to False (Live)
        resp1 = client.get("/api/status").json()
        assert resp1["config"]["dry_run"] is True

        toggle1 = client.post("/api/mode/toggle").json()
        assert toggle1["dry_run"] is False
        assert "LIVE REMEDIATION" in toggle1["message"]
        assert controller.remediation_executor.dry_run_default is False

        # Toggle back to True (Safe Dry Run)
        toggle2 = client.post("/api/mode/toggle").json()
        assert toggle2["dry_run"] is True
        assert "DRY-RUN" in toggle2["message"]
        assert controller.remediation_executor.dry_run_default is True


def test_ui_api_benchmarks(mock_config, healthy_snapshot):
    """Verifies GET /api/benchmarks returns comparative experimental findings."""
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    controller = HealKubeController(config=mock_config, monitor=mock_monitor)
    main_module.controller_instance = controller

    with TestClient(status_app) as client:
        resp = client.get("/api/benchmarks")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) >= 2
        # Check that both systems are represented
        systems = {row["system"] for row in data["results"]}
        assert "Native K8s" in systems
        assert any("HealKube" in s for s in systems)


def test_ui_api_chaos_endpoints_graceful_offline(mock_config, healthy_snapshot):
    """Verifies chaos proxy endpoints handle target unreachable gracefully without crashing."""
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot
    mock_config.app_base_url = "http://127.0.0.1:9999"  # Deliberately unreachable port

    controller = HealKubeController(config=mock_config, monitor=mock_monitor)
    main_module.controller_instance = controller

    with TestClient(status_app) as client:
        # Chaos injection
        inject_resp = client.post(
            "/api/chaos/inject",
            json={"error_rate": 0.8, "extra_latency_seconds": 1.0, "simulate_dependency_down": False},
        )
        assert inject_resp.status_code == 200
        assert inject_resp.json()["status"] == "error"
        assert "Could not reach" in inject_resp.json()["message"]

        # Chaos reset
        reset_resp = client.post("/api/chaos/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json()["status"] == "error"

        # Chaos status
        status_resp = client.get("/api/chaos/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "unreachable"

        # Traffic test probe
        test_resp = client.get("/api/test-request")
        assert test_resp.status_code == 200
        assert test_resp.json()["status_code"] == 503

