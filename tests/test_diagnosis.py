"""
Tests for DiagnosisEngine and deterministic root-cause precedence.
"""

from healkube.controller.detector import AnomalyDetector
from healkube.controller.diagnosis import DiagnosisEngine
from healkube.controller.models import DiagnosisType


def test_diagnosis_healthy(mock_config, healthy_snapshot):
    detector = AnomalyDetector(mock_config)
    engine = DiagnosisEngine()

    report = detector.evaluate(healthy_snapshot)
    diagnosis = engine.diagnose(healthy_snapshot, report)

    assert diagnosis.diagnosis_type == DiagnosisType.HEALTHY
    assert "nominal" in diagnosis.reason.lower()


def test_diagnosis_application_degradation(mock_config, degraded_app_snapshot):
    detector = AnomalyDetector(mock_config)
    engine = DiagnosisEngine()

    # Sustain breach 3 times
    for _ in range(3):
        report = detector.evaluate(degraded_app_snapshot)

    diagnosis = engine.diagnose(degraded_app_snapshot, report)

    assert diagnosis.diagnosis_type == DiagnosisType.APPLICATION_DEGRADATION
    assert diagnosis.affected_workload == "test-app"
    assert "degradation" in diagnosis.reason.lower()


def test_diagnosis_resource_pressure(mock_config, resource_pressure_snapshot):
    detector = AnomalyDetector(mock_config)
    engine = DiagnosisEngine()

    for _ in range(3):
        report = detector.evaluate(resource_pressure_snapshot)

    diagnosis = engine.diagnose(resource_pressure_snapshot, report)

    assert diagnosis.diagnosis_type == DiagnosisType.RESOURCE_PRESSURE
    assert "cpu" in diagnosis.reason.lower()


def test_dependency_failure_takes_precedence_over_app_errors(
    mock_config, dependency_down_snapshot
):
    """
    CRITICAL REQUIREMENT:
    When upstream dependency probe fails, do not infer application code bug
    even if 5xx error rate or latency is high.
    """
    detector = AnomalyDetector(mock_config)
    engine = DiagnosisEngine()

    for _ in range(3):
        report = detector.evaluate(dependency_down_snapshot)

    assert report.dependency_failure_sustained
    diagnosis = engine.diagnose(dependency_down_snapshot, report)

    assert diagnosis.diagnosis_type == DiagnosisType.DEPENDENCY_FAILURE
    assert "dependency probe failed" in diagnosis.reason.lower()
    assert "restarting application pods would not resolve" in diagnosis.reason.lower()


def test_diagnosis_pod_failure(mock_config, pod_failure_snapshot):
    detector = AnomalyDetector(mock_config)
    engine = DiagnosisEngine()

    for _ in range(3):
        report = detector.evaluate(pod_failure_snapshot)

    diagnosis = engine.diagnose(pod_failure_snapshot, report)

    assert diagnosis.diagnosis_type == DiagnosisType.POD_FAILURE
    assert "test-app-pod-1" in diagnosis.affected_pods
