"""
Tests for RemediationExecutor: live execution, dry-run safety, and error handling.
"""

import time
from unittest.mock import MagicMock
from healkube.controller.remediation import RemediationExecutor
from healkube.controller.models import (
    RemediationDecision,
    RemediationActionType,
)


def test_dry_run_never_calls_k8s_mutation():
    mock_core = MagicMock()
    mock_apps = MagicMock()
    executor = RemediationExecutor(core_api=mock_core, apps_api=mock_apps)

    decision = RemediationDecision(
        action=RemediationActionType.RESTART_POD,
        target_namespace="test-ns",
        target_workload="test-app",
        target_pod="test-app-pod-1",
        reason="Test restart",
        dry_run=True,  # DRY RUN ENABLED
        timestamp=time.time(),
    )

    success, err = executor.execute(decision)
    assert success is True
    assert err is None
    # Verify NO delete call was made to K8s API
    mock_core.delete_namespaced_pod.assert_not_called()
    mock_apps.patch_namespaced_deployment_scale.assert_not_called()


def test_live_execution_deletes_pod():
    mock_core = MagicMock()
    mock_apps = MagicMock()
    executor = RemediationExecutor(core_api=mock_core, apps_api=mock_apps)

    decision = RemediationDecision(
        action=RemediationActionType.RESTART_POD,
        target_namespace="test-ns",
        target_workload="test-app",
        target_pod="test-app-pod-1",
        reason="Test restart",
        dry_run=False,  # LIVE EXECUTION
        timestamp=time.time(),
    )

    success, err = executor.execute(decision)
    assert success is True
    assert err is None
    mock_core.delete_namespaced_pod.assert_called_once_with(
        name="test-app-pod-1",
        namespace="test-ns",
        grace_period_seconds=10,
    )


def test_live_execution_scales_deployment():
    mock_core = MagicMock()
    mock_apps = MagicMock()
    executor = RemediationExecutor(core_api=mock_core, apps_api=mock_apps)

    decision = RemediationDecision(
        action=RemediationActionType.SCALE_UP,
        target_namespace="test-ns",
        target_workload="test-app",
        target_replicas=4,
        reason="Test scale up",
        dry_run=False,
        timestamp=time.time(),
    )

    success, err = executor.execute(decision)
    assert success is True
    assert err is None
    mock_apps.patch_namespaced_deployment_scale.assert_called_once_with(
        name="test-app",
        namespace="test-ns",
        body={"spec": {"replicas": 4}},
    )


def test_remediation_handles_k8s_api_errors_gracefully():
    mock_core = MagicMock()
    mock_core.delete_namespaced_pod.side_effect = Exception("404 Pod Not Found")
    executor = RemediationExecutor(core_api=mock_core)

    decision = RemediationDecision(
        action=RemediationActionType.RESTART_POD,
        target_namespace="test-ns",
        target_workload="test-app",
        target_pod="stale-pod",
        reason="Test restart",
        dry_run=False,
        timestamp=time.time(),
    )

    success, err = executor.execute(decision)
    assert success is False
    assert "404 Pod Not Found" in (err or "")
