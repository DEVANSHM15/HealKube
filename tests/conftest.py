"""
Pytest fixtures for HealKube test suite.
Provides mock configurations, telemetry snapshots, and mock Kubernetes clients.
"""

import pytest
from unittest.mock import MagicMock

from healkube.controller.config import ControllerConfig
from healkube.controller.models import (
    SystemSnapshot,
    DeploymentHealthState,
    PodHealthState,
    ApplicationMetrics,
    MetricValue,
    MetricStatus,
)


@pytest.fixture
def mock_config():
    """Returns a test ControllerConfig with short thresholds for fast tests."""
    return ControllerConfig(
        kubernetes_mode="local_mock",
        target_namespace="test-ns",
        target_deployment="test-app",
        namespace_allowlist=["test-ns"],
        workload_allowlist=["test-app"],
        consecutive_breaches_required=3,
        error_rate_threshold_percent=5.0,
        latency_p95_threshold_seconds=1.0,
        cpu_utilization_threshold_percent=85.0,
        cooldown_period_seconds=30.0,
        dry_run=True,
        verification_wait_seconds=0.0,
        verification_checks_required=2,
        verification_interval_seconds=0.0,
        verification_timeout_seconds=5.0,
    )


@pytest.fixture
def healthy_snapshot():
    """Generates a nominal, healthy SystemSnapshot."""
    return SystemSnapshot(
        timestamp=1000.0,
        target_namespace="test-ns",
        target_deployment="test-app",
        deployment=DeploymentHealthState(
            name="test-app",
            namespace="test-ns",
            desired_replicas=2,
            ready_replicas=2,
            available_replicas=2,
            has_hpa=False,
        ),
        pods=[
            PodHealthState(
                pod_name="test-app-pod-1",
                namespace="test-ns",
                phase="Running",
                is_ready=True,
                restart_count=0,
                node_name="node-1",
                node_ready=True,
            ),
            PodHealthState(
                pod_name="test-app-pod-2",
                namespace="test-ns",
                phase="Running",
                is_ready=True,
                restart_count=0,
                node_name="node-2",
                node_ready=True,
            ),
        ],
        metrics=ApplicationMetrics(
            error_rate_percent=MetricValue(value=0.5, status=MetricStatus.AVAILABLE, unit="%"),
            latency_p95_seconds=MetricValue(value=0.15, status=MetricStatus.AVAILABLE, unit="s"),
            cpu_utilization_percent=MetricValue(value=40.0, status=MetricStatus.AVAILABLE, unit="%"),
            memory_utilization_percent=MetricValue(value=50.0, status=MetricStatus.AVAILABLE, unit="%"),
            dependency_healthy=MetricValue(value=1.0, status=MetricStatus.AVAILABLE, unit="bool"),
        ),
    )


@pytest.fixture
def degraded_app_snapshot(healthy_snapshot):
    """Snapshot where pods are Ready but application error rate is 25%."""
    snap = healthy_snapshot.model_copy(deep=True)
    snap.metrics.error_rate_percent = MetricValue(
        value=25.0,
        status=MetricStatus.AVAILABLE,
        unit="%",
    )
    return snap


@pytest.fixture
def resource_pressure_snapshot(healthy_snapshot):
    """Snapshot with 95% CPU and 2.5s P95 latency."""
    snap = healthy_snapshot.model_copy(deep=True)
    snap.metrics.cpu_utilization_percent = MetricValue(
        value=95.0,
        status=MetricStatus.AVAILABLE,
        unit="%",
    )
    snap.metrics.latency_p95_seconds = MetricValue(
        value=2.5,
        status=MetricStatus.AVAILABLE,
        unit="s",
    )
    return snap


@pytest.fixture
def dependency_down_snapshot(degraded_app_snapshot):
    """Snapshot where application error rate is high BUT upstream dependency is failing."""
    snap = degraded_app_snapshot.model_copy(deep=True)
    snap.metrics.dependency_healthy = MetricValue(
        value=0.0,
        status=MetricStatus.AVAILABLE,
        unit="bool",
        error_message="Connection refused to upstream DB",
    )
    return snap


@pytest.fixture
def pod_failure_snapshot(healthy_snapshot):
    """Snapshot where one pod is not ready / failed."""
    snap = healthy_snapshot.model_copy(deep=True)
    snap.pods[0].is_ready = False
    snap.pods[0].phase = "Running"
    snap.pods[0].restart_count = 5
    return snap
