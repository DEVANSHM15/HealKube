"""
Tests for AnomalyDetector requiring sustained consecutive breaches.
"""

from healkube.controller.detector import AnomalyDetector


def test_healthy_snapshot_produces_no_anomalies(mock_config, healthy_snapshot):
    detector = AnomalyDetector(mock_config)
    report = detector.evaluate(healthy_snapshot)

    assert not report.app_degradation_sustained
    assert not report.resource_pressure_sustained
    assert not report.pod_failure_sustained
    assert not report.dependency_failure_sustained
    assert not report.node_failure_sustained
    assert detector.consecutive_counts["app_degradation"] == 0


def test_high_error_rate_requires_consecutive_checks(mock_config, degraded_app_snapshot):
    # Config requires 3 consecutive breaches
    assert mock_config.consecutive_breaches_required == 3
    detector = AnomalyDetector(mock_config)

    # Check 1: single breach - NOT sustained
    report1 = detector.evaluate(degraded_app_snapshot)
    assert not report1.app_degradation_sustained
    assert detector.consecutive_counts["app_degradation"] == 1

    # Check 2: second breach - NOT sustained yet
    report2 = detector.evaluate(degraded_app_snapshot)
    assert not report2.app_degradation_sustained
    assert detector.consecutive_counts["app_degradation"] == 2

    # Check 3: third consecutive breach - SUSTAINED!
    report3 = detector.evaluate(degraded_app_snapshot)
    assert report3.app_degradation_sustained
    assert detector.consecutive_counts["app_degradation"] == 3


def test_transient_spike_resets_consecutive_counter(
    mock_config, healthy_snapshot, degraded_app_snapshot
):
    detector = AnomalyDetector(mock_config)

    # 2 breaches
    detector.evaluate(degraded_app_snapshot)
    detector.evaluate(degraded_app_snapshot)
    assert detector.consecutive_counts["app_degradation"] == 2

    # Healthy snapshot intervenes
    report_healthy = detector.evaluate(healthy_snapshot)
    assert not report_healthy.app_degradation_sustained
    assert detector.consecutive_counts["app_degradation"] == 0

    # Next single breach starts count over at 1
    report_next = detector.evaluate(degraded_app_snapshot)
    assert not report_next.app_degradation_sustained
    assert detector.consecutive_counts["app_degradation"] == 1


def test_resource_pressure_requires_both_high_cpu_and_latency(
    mock_config, healthy_snapshot, resource_pressure_snapshot
):
    detector = AnomalyDetector(mock_config)

    # 3 consecutive resource pressure breaches
    detector.evaluate(resource_pressure_snapshot)
    detector.evaluate(resource_pressure_snapshot)
    report3 = detector.evaluate(resource_pressure_snapshot)

    assert report3.resource_pressure_sustained
    assert not report3.app_degradation_sustained


def test_high_cpu_alone_without_latency_is_not_resource_pressure(
    mock_config, healthy_snapshot
):
    detector = AnomalyDetector(mock_config)
    snap = healthy_snapshot.model_copy(deep=True)
    snap.metrics.cpu_utilization_percent.value = 99.0
    # Latency stays low
    snap.metrics.latency_p95_seconds.value = 0.1

    for _ in range(3):
        report = detector.evaluate(snap)

    assert not report.resource_pressure_sustained
