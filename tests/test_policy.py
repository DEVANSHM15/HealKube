"""
Tests for PolicyEngine safeguards, cooldowns, scaling limits, and allowlists.
"""

import time
from healkube.controller.policy import PolicyEngine
from healkube.controller.models import (
    DiagnosisResult,
    DiagnosisType,
    RemediationActionType,
    RemediationDecision,
)


def test_policy_healthy_produces_no_action(mock_config, healthy_snapshot):
    policy = PolicyEngine(mock_config)
    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.HEALTHY,
        affected_workload="test-app",
        reason="Nominal",
    )

    decision = policy.evaluate(healthy_snapshot, diag)
    assert decision.action == RemediationActionType.NO_ACTION


def test_policy_allowlist_enforcement(mock_config, degraded_app_snapshot):
    mock_config.namespace_allowlist = ["allowed-ns"]
    policy = PolicyEngine(mock_config)

    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.APPLICATION_DEGRADATION,
        affected_workload="test-app",
        reason="Degraded",
    )

    # snapshot is in test-ns, not allowed-ns
    decision = policy.evaluate(degraded_app_snapshot, diag)
    assert decision.action == RemediationActionType.NO_ACTION
    assert "not in allowed list" in decision.reason.lower()


def test_policy_cooldown_prevents_repeated_action(mock_config, degraded_app_snapshot):
    mock_config.cooldown_period_seconds = 60.0
    policy = PolicyEngine(mock_config)

    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.APPLICATION_DEGRADATION,
        affected_workload="test-app",
        reason="Degraded",
    )

    # First evaluation produces RESTART_POD
    decision1 = policy.evaluate(degraded_app_snapshot, diag)
    assert decision1.action == RemediationActionType.RESTART_POD

    # Record remediation executed at now
    policy.record_remediation("test-ns", "test-app", timestamp=time.time())

    # Second evaluation immediately after -> must be blocked by cooldown!
    decision2 = policy.evaluate(degraded_app_snapshot, diag)
    assert decision2.action == RemediationActionType.NO_ACTION
    assert "cooldown period" in decision2.reason.lower()


def test_policy_cooldown_expires(mock_config, degraded_app_snapshot):
    mock_config.cooldown_period_seconds = 10.0
    policy = PolicyEngine(mock_config)

    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.APPLICATION_DEGRADATION,
        affected_workload="test-app",
        reason="Degraded",
    )

    # Record remediation 15 seconds ago
    past_time = time.time() - 15.0
    policy.record_remediation("test-ns", "test-app", timestamp=past_time)

    # Now cooldown should be expired
    decision = policy.evaluate(degraded_app_snapshot, diag)
    assert decision.action == RemediationActionType.RESTART_POD


def test_policy_dependency_failure_does_not_restart_pod(
    mock_config, dependency_down_snapshot
):
    policy = PolicyEngine(mock_config)
    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.DEPENDENCY_FAILURE,
        affected_workload="test-app",
        reason="DB is down",
    )

    decision = policy.evaluate(dependency_down_snapshot, diag)
    assert decision.action == RemediationActionType.REPORT_DEPENDENCY_ISSUE
    assert decision.target_pod is None


def test_policy_scaling_obeys_max_replicas(mock_config, resource_pressure_snapshot):
    mock_config.max_replicas = 3
    mock_config.scaling_step = 1
    policy = PolicyEngine(mock_config)

    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.RESOURCE_PRESSURE,
        affected_workload="test-app",
        reason="High CPU and latency",
    )

    # Desired is 2, max is 3 -> scales to 3
    resource_pressure_snapshot.deployment.desired_replicas = 2
    decision = policy.evaluate(resource_pressure_snapshot, diag)
    assert decision.action == RemediationActionType.SCALE_UP
    assert decision.target_replicas == 3

    # When already at max (3), should NOT scale higher
    resource_pressure_snapshot.deployment.desired_replicas = 3
    decision_max = policy.evaluate(resource_pressure_snapshot, diag)
    assert decision_max.action == RemediationActionType.NO_ACTION
    assert "already at max limit" in decision_max.reason.lower()


def test_policy_scaling_avoids_fighting_hpa(mock_config, resource_pressure_snapshot):
    mock_config.check_hpa_exists = True
    policy = PolicyEngine(mock_config)

    diag = DiagnosisResult(
        diagnosis_type=DiagnosisType.RESOURCE_PRESSURE,
        affected_workload="test-app",
        reason="High CPU and latency",
    )

    # Deployment has HPA attached
    resource_pressure_snapshot.deployment.has_hpa = True
    decision = policy.evaluate(resource_pressure_snapshot, diag)

    assert decision.action == RemediationActionType.NO_ACTION
    assert "hpa is already configured" in decision.reason.lower()


def test_allowlist_string_and_json_normalization():
    from healkube.controller.config import ControllerConfig

    # Comma-separated string
    cfg_comma = ControllerConfig(
        namespace_allowlist="ns-a, ns-b, ns-c",
        workload_allowlist="app-1, app-2",
    )
    assert cfg_comma.namespace_allowlist == ["ns-a", "ns-b", "ns-c"]
    assert cfg_comma.workload_allowlist == ["app-1", "app-2"]

    # JSON formatted string
    cfg_json = ControllerConfig(
        namespace_allowlist='["ns-1", "ns-2"]',
        workload_allowlist='["web"]',
    )
    assert cfg_json.namespace_allowlist == ["ns-1", "ns-2"]
    assert cfg_json.workload_allowlist == ["web"]


def test_remediation_executor_dry_run_default_override():
    from unittest.mock import MagicMock
    from healkube.controller.remediation import RemediationExecutor

    mock_core = MagicMock()
    # Executor configured with dry_run_default=True
    executor = RemediationExecutor(core_api=mock_core, dry_run_default=True)

    # Decision mistakenly marked dry_run=False
    decision = RemediationDecision(
        action=RemediationActionType.RESTART_POD,
        target_namespace="test-ns",
        target_workload="test-app",
        target_pod="pod-1",
        reason="Forced live restart",
        dry_run=False,  # Mistakenly set to False!
        timestamp=time.time(),
    )

    success, err = executor.execute(decision)
    assert success is True
    assert err is None
    # Must NOT call delete_namespaced_pod due to executor-level safety latch
    mock_core.delete_namespaced_pod.assert_not_called()
