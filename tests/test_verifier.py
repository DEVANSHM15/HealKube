"""
Tests for RecoveryVerifier: stabilization delay, multi-check success, and timeout.
"""

from unittest.mock import MagicMock
from healkube.controller.verifier import RecoveryVerifier
from healkube.controller.models import (
    VerificationStatus,
    MetricValue,
    MetricStatus,
)


def test_verification_declares_success_on_consecutive_passes(
    mock_config, healthy_snapshot
):
    mock_config.verification_wait_seconds = 5.0
    mock_config.verification_checks_required = 2
    mock_config.verification_timeout_seconds = 10.0

    verifier = RecoveryVerifier(mock_config)
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = healthy_snapshot

    sleep_calls = []

    def mock_sleep(secs):
        sleep_calls.append(secs)

    result = verifier.verify(mock_monitor, sleeper=mock_sleep)

    assert result.status == VerificationStatus.SUCCESS
    assert result.checks_completed == 2
    assert "recovered successfully" in result.details.lower()
    # Verified stabilization wait occurred
    assert 5.0 in sleep_calls


def test_verification_detects_continued_failure_and_times_out(
    mock_config, degraded_app_snapshot
):
    mock_config.verification_wait_seconds = 0.0
    mock_config.verification_checks_required = 3
    mock_config.verification_timeout_seconds = 2.0
    mock_config.verification_interval_seconds = 1.0

    verifier = RecoveryVerifier(mock_config)
    mock_monitor = MagicMock()
    mock_monitor.collect_system_snapshot.return_value = degraded_app_snapshot

    result = verifier.verify(mock_monitor, sleeper=lambda s: None)

    assert result.status == VerificationStatus.TIMEOUT
    assert "failed to stabilize" in result.details.lower()


def test_verification_does_not_declare_success_if_pods_recreated_but_app_still_errors(
    mock_config, healthy_snapshot
):
    """
    CRITICAL REQUIREMENT:
    Do not claim recovery just because a Pod was recreated if application metrics remain bad.
    """
    verifier = RecoveryVerifier(mock_config)
    mock_monitor = MagicMock()

    # Pods are running and ready, but 5xx error rate is still 40%
    recreated_but_still_failing = healthy_snapshot.model_copy(deep=True)
    recreated_but_still_failing.metrics.error_rate_percent = MetricValue(
        value=40.0,
        status=MetricStatus.AVAILABLE,
        unit="%",
    )
    mock_monitor.collect_system_snapshot.return_value = recreated_but_still_failing

    result = verifier.verify(mock_monitor, sleeper=lambda s: None)

    assert result.status != VerificationStatus.SUCCESS
    assert result.status == VerificationStatus.TIMEOUT
