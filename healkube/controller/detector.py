"""
Deterministic Anomaly Detector for HealKube.
Evaluates observed metrics against configured thresholds and requires
sustained consecutive breaches before flagging anomalies.
"""

import logging
from typing import Dict, List, Optional
from healkube.controller.config import ControllerConfig
from healkube.controller.models import SystemSnapshot, MetricStatus

logger = logging.getLogger(__name__)


class AnomalyReport:
    """Encapsulates raw anomaly signals and whether they are sustained."""

    def __init__(self):
        self.dependency_failure_sustained: bool = False
        self.node_failure_sustained: bool = False
        self.pod_failure_sustained: bool = False
        self.resource_pressure_sustained: bool = False
        self.app_degradation_sustained: bool = False

        self.consecutive_counts: Dict[str, int] = {}
        self.unhealthy_pods: List[str] = []
        self.signal_details: Dict[str, str] = {}


class AnomalyDetector:
    """Tracks consecutive anomaly observations to prevent spurious flapping."""

    def __init__(self, config: ControllerConfig):
        self.config = config
        self.consecutive_counts = {
            "dependency_failure": 0,
            "node_failure": 0,
            "pod_failure": 0,
            "resource_pressure": 0,
            "app_degradation": 0,
        }

    def reset(self):
        """Reset all consecutive counters."""
        for k in self.consecutive_counts:
            self.consecutive_counts[k] = 0

    def evaluate(self, snapshot: SystemSnapshot) -> AnomalyReport:
        """
        Evaluates a SystemSnapshot against configured thresholds.
        Increments consecutive counter if breach detected; resets if normal.
        """
        report = AnomalyReport()
        req_consecutive = self.config.consecutive_breaches_required

        # 1. Dependency Health Check
        dep_metric = snapshot.metrics.dependency_healthy
        if dep_metric.status == MetricStatus.AVAILABLE and dep_metric.value == 0.0:
            self.consecutive_counts["dependency_failure"] += 1
            report.signal_details["dependency"] = (
                f"Dependency probe failed: {dep_metric.error_message or 'unhealthy'}"
            )
        else:
            self.consecutive_counts["dependency_failure"] = 0

        # 2. Node Health Check
        unhealthy_nodes = [p.node_name for p in snapshot.pods if not p.node_ready and p.node_name]
        if unhealthy_nodes:
            self.consecutive_counts["node_failure"] += 1
            report.signal_details["node"] = f"Unhealthy node(s) detected: {unhealthy_nodes}"
        else:
            self.consecutive_counts["node_failure"] = 0

        # 3. Pod Phase & Readiness Failure Check
        unhealthy_pods = []
        for pod in snapshot.pods:
            if pod.deletion_timestamp:
                continue  # already being terminated
            if pod.phase in ["Failed", "Unknown"]:
                unhealthy_pods.append(pod.pod_name)
            elif pod.phase == "Running" and not pod.is_ready:
                unhealthy_pods.append(pod.pod_name)

        if unhealthy_pods:
            self.consecutive_counts["pod_failure"] += 1
            report.unhealthy_pods = unhealthy_pods
            report.signal_details["pod_failure"] = (
                f"Pods not ready or failed: {', '.join(unhealthy_pods)}"
            )
        else:
            self.consecutive_counts["pod_failure"] = 0

        # 4. Resource Pressure Check: High CPU AND High Latency
        cpu_metric = snapshot.metrics.cpu_utilization_percent
        latency_metric = snapshot.metrics.latency_p95_seconds

        cpu_high = (
            cpu_metric.status == MetricStatus.AVAILABLE
            and cpu_metric.value is not None
            and cpu_metric.value >= self.config.cpu_utilization_threshold_percent
        )
        latency_high = (
            latency_metric.status == MetricStatus.AVAILABLE
            and latency_metric.value is not None
            and latency_metric.value >= self.config.latency_p95_threshold_seconds
        )

        if cpu_high and latency_high:
            self.consecutive_counts["resource_pressure"] += 1
            report.signal_details["resource_pressure"] = (
                f"CPU ({cpu_metric.value:.1f}%) and P95 latency ({latency_metric.value:.2f}s) "
                f"both exceeded thresholds ({self.config.cpu_utilization_threshold_percent}%, "
                f"{self.config.latency_p95_threshold_seconds}s)"
            )
        else:
            self.consecutive_counts["resource_pressure"] = 0

        # 5. Application Degradation Check: High error rate OR High latency while Pods are Ready
        err_metric = snapshot.metrics.error_rate_percent
        all_pods_ready = (
            len(snapshot.pods) > 0 and all(p.is_ready for p in snapshot.pods)
        )

        err_high = (
            err_metric.status == MetricStatus.AVAILABLE
            and err_metric.value is not None
            and err_metric.value >= self.config.error_rate_threshold_percent
        )

        # Latency high without high CPU (if high CPU was present, it's resource pressure)
        app_degraded = all_pods_ready and (err_high or (latency_high and not cpu_high))

        if app_degraded:
            self.consecutive_counts["app_degradation"] += 1
            details = []
            if err_high:
                details.append(
                    f"5xx error rate ({err_metric.value:.1f}%) >= {self.config.error_rate_threshold_percent}%"
                )
            if latency_high and not cpu_high:
                details.append(
                    f"P95 latency ({latency_metric.value:.2f}s) >= {self.config.latency_p95_threshold_seconds}s"
                )
            report.signal_details["app_degradation"] = (
                f"Application degradation with Ready pods: {'; '.join(details)}"
            )
        else:
            self.consecutive_counts["app_degradation"] = 0

        # Determine sustained status
        report.dependency_failure_sustained = (
            self.consecutive_counts["dependency_failure"] >= req_consecutive
        )
        report.node_failure_sustained = (
            self.consecutive_counts["node_failure"] >= req_consecutive
        )
        report.pod_failure_sustained = (
            self.consecutive_counts["pod_failure"] >= req_consecutive
        )
        report.resource_pressure_sustained = (
            self.consecutive_counts["resource_pressure"] >= req_consecutive
        )
        report.app_degradation_sustained = (
            self.consecutive_counts["app_degradation"] >= req_consecutive
        )

        report.consecutive_counts = dict(self.consecutive_counts)
        return report
