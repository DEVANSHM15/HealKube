"""
Recovery Verifier for HealKube.
Conducts multi-stage post-remediation verification:
1. Stabilization delay
2. Multi-point validation of Pod readiness AND application metrics
Declares SUCCESS only when recovery conditions consistently hold.
"""

import time
import logging
from typing import Optional
from healkube.controller.config import ControllerConfig
from healkube.controller.models import (
    VerificationResult,
    VerificationStatus,
    MetricStatus,
)
from healkube.controller.monitor import ClusterMonitor

logger = logging.getLogger(__name__)


class RecoveryVerifier:
    """Verifies workload recovery after an autonomous remediation event."""

    def __init__(self, config: ControllerConfig):
        self.config = config

    def verify(
        self,
        monitor: ClusterMonitor,
        sleeper=time.sleep,
    ) -> VerificationResult:
        """
        Executes post-remediation verification.
        Uses injectable sleeper function for testability without real delays.
        """
        start_time = time.time()
        logger.info(
            "Starting stabilization wait (%ss) for %s/%s before verification",
            self.config.verification_wait_seconds,
            self.config.target_namespace,
            self.config.target_deployment,
        )

        # 1. Stabilization Wait
        if self.config.verification_wait_seconds > 0:
            sleeper(self.config.verification_wait_seconds)

        consecutive_passes = 0
        total_checks = 0
        had_available_metrics = False
        deadline = start_time + self.config.verification_timeout_seconds

        logger.info(
            "Verifying recovery: requiring %d consecutive healthy checks (interval %ss)",
            self.config.verification_checks_required,
            self.config.verification_interval_seconds,
        )

        while time.time() < deadline:
            total_checks += 1
            snapshot = monitor.collect_system_snapshot()

            # Condition 1: Pods must be running and ready
            pods = snapshot.pods
            pods_healthy = len(pods) > 0 and all(p.phase == "Running" and p.is_ready for p in pods)

            # Condition 2: Application error rate must not breach threshold
            err_m = snapshot.metrics.error_rate_percent
            err_healthy = True
            if err_m.status == MetricStatus.AVAILABLE and err_m.value is not None:
                had_available_metrics = True
                if err_m.value >= self.config.error_rate_threshold_percent:
                    err_healthy = False

            # Condition 3: Latency must not breach threshold
            lat_m = snapshot.metrics.latency_p95_seconds
            lat_healthy = True
            if lat_m.status == MetricStatus.AVAILABLE and lat_m.value is not None:
                had_available_metrics = True
                if lat_m.value >= self.config.latency_p95_threshold_seconds:
                    lat_healthy = False

            is_healthy_now = pods_healthy and err_healthy and lat_healthy

            if is_healthy_now:
                consecutive_passes += 1
                logger.info(
                    "Verification check %d passed (consecutive passes: %d/%d)",
                    total_checks,
                    consecutive_passes,
                    self.config.verification_checks_required,
                )
                if consecutive_passes >= self.config.verification_checks_required:
                    elapsed = time.time() - start_time
                    status = (
                        VerificationStatus.SUCCESS
                        if had_available_metrics or pods_healthy
                        else VerificationStatus.INSUFFICIENT_DATA
                    )
                    details = (
                        f"Target recovered successfully. Met all health and metric criteria "
                        f"across {consecutive_passes} consecutive checks."
                    )
                    return VerificationResult(
                        status=status,
                        checks_completed=total_checks,
                        duration_seconds=round(elapsed, 2),
                        details=details,
                    )
            else:
                consecutive_passes = 0
                logger.info(
                    "Verification check %d failed (pods_healthy=%s, err_healthy=%s, lat_healthy=%s)",
                    total_checks,
                    pods_healthy,
                    err_healthy,
                    lat_healthy,
                )

            # Wait before next check
            if time.time() + self.config.verification_interval_seconds <= deadline:
                sleeper(self.config.verification_interval_seconds)
            else:
                break

        elapsed = time.time() - start_time
        if not had_available_metrics and len(snapshot.pods) == 0:
            return VerificationResult(
                status=VerificationStatus.INSUFFICIENT_DATA,
                checks_completed=total_checks,
                duration_seconds=round(elapsed, 2),
                details="Verification timed out with no pods or application telemetry available.",
            )

        return VerificationResult(
            status=VerificationStatus.TIMEOUT,
            checks_completed=total_checks,
            duration_seconds=round(elapsed, 2),
            details=(
                f"Workload failed to stabilize within {self.config.verification_timeout_seconds}s. "
                f"Completed {total_checks} checks with {consecutive_passes} consecutive passes."
            ),
        )
