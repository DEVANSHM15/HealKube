"""
Tests for ClusterMonitor telemetry collection.
"""

from unittest.mock import MagicMock
import requests
from healkube.controller.monitor import ClusterMonitor
from healkube.controller.models import MetricStatus


def test_monitor_mock_mode_defaults(mock_config):
    monitor = ClusterMonitor(mock_config)
    snapshot = monitor.collect_system_snapshot()

    assert snapshot.target_namespace == "test-ns"
    assert snapshot.target_deployment == "test-app"
    assert len(snapshot.pods) >= 1
    assert snapshot.pods[0].is_ready is True
    assert snapshot.deployment.desired_replicas == 1


def test_monitor_dependency_health_success(mock_config, monkeypatch):
    mock_config.dependency_check_enabled = True
    mock_config.dependency_health_url = "http://upstream/health"
    monitor = ClusterMonitor(mock_config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_resp)

    dep_res = monitor.check_dependency_health()
    assert dep_res.status == MetricStatus.AVAILABLE
    assert dep_res.value == 1.0


def test_monitor_dependency_health_failure(mock_config, monkeypatch):
    mock_config.dependency_check_enabled = True
    mock_config.dependency_health_url = "http://upstream/health"
    monitor = ClusterMonitor(mock_config)

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_resp)

    dep_res = monitor.check_dependency_health()
    assert dep_res.status == MetricStatus.AVAILABLE
    assert dep_res.value == 0.0
    assert "503" in (dep_res.error_message or "")


def test_monitor_dependency_health_connection_error(mock_config, monkeypatch):
    mock_config.dependency_check_enabled = True
    mock_config.dependency_health_url = "http://upstream/health"
    monitor = ClusterMonitor(mock_config)

    def mock_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection timed out")

    monkeypatch.setattr(requests, "get", mock_get)

    dep_res = monitor.check_dependency_health()
    assert dep_res.status == MetricStatus.AVAILABLE
    assert dep_res.value == 0.0
    assert "timed out" in (dep_res.error_message or "").lower()


def test_monitor_dependency_disabled(mock_config):
    mock_config.dependency_check_enabled = False
    monitor = ClusterMonitor(mock_config)

    dep_res = monitor.check_dependency_health()
    assert dep_res.status == MetricStatus.UNKNOWN
    assert dep_res.value is None
