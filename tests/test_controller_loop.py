"""
End-to-End tests for HealKubeController reconciliation loop and status API.
"""

from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from healkube.controller.main import HealKubeController, status_app, controller_instance
import healkube.controller.main as main_module
from healkube.controller.models import (
    RemediationActionType,
    DiagnosisType,
)


def test_reconcile_once_healthy(mock_config, healthy_snapshot):
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    controller = HealKubeController(
        config=mock_config,
        monitor=mock_monitor,
    )

    result = controller.reconcile_once()

    assert result["iteration"] == 1
    assert result["diagnosis"] == DiagnosisType.HEALTHY.value
    assert result["action"] == RemediationActionType.NO_ACTION.value


def test_reconcile_once_sustained_degradation_triggers_remediation(
    mock_config, degraded_app_snapshot
):
    mock_config.consecutive_breaches_required = 2
    mock_config.dry_run = True

    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = degraded_app_snapshot

    controller = HealKubeController(
        config=mock_config,
        monitor=mock_monitor,
    )

    # First iteration: not sustained yet
    res1 = controller.reconcile_once()
    assert res1["action"] == RemediationActionType.NO_ACTION.value

    # Second iteration: sustained!
    res2 = controller.reconcile_once()
    assert res2["action"] == RemediationActionType.RESTART_POD.value
    assert res2["dry_run"] is True

    # Check audit logger has recorded event
    records = controller.audit_logger.get_recent_records()
    assert len(records) == 1
    assert records[0].decision.action == RemediationActionType.RESTART_POD


def test_controller_status_api(mock_config, healthy_snapshot):
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    controller = HealKubeController(
        config=mock_config,
        monitor=mock_monitor,
    )
    controller.reconcile_once()

    # Set as global controller instance for status_app
    main_module.controller_instance = controller

    client = TestClient(status_app)

    # Healthz probe
    health_resp = client.get("/healthz")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    # Status inspection
    status_resp = client.get("/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["iteration_count"] == 1
    assert data["last_diagnosis"]["diagnosis_type"] == "HEALTHY"
    assert data["config"]["target_deployment"] == "test-app"
