"""
Explainable Root Cause Diagnosis Engine for HealKube.
Applies deterministic rule precedence, prevents contradictory diagnosis,
and provides detailed evidence trails for every classification.
"""

import logging
from typing import Dict, Any
from healkube.controller.models import (
    SystemSnapshot,
    DiagnosisResult,
    DiagnosisType,
    MetricStatus,
)
from healkube.controller.detector import AnomalyReport

logger = logging.getLogger(__name__)


class DiagnosisEngine:
    """Diagnoses root cause from anomaly reports with strict rule precedence."""

    def diagnose(self, snapshot: SystemSnapshot, report: AnomalyReport) -> DiagnosisResult:
        workload = snapshot.target_deployment
        evidence: Dict[str, Any] = {
            "consecutive_counts": report.consecutive_counts,
            "signal_details": report.signal_details,
            "observed_metrics": {
                "error_rate": {
                    "value": snapshot.metrics.error_rate_percent.value,
                    "status": snapshot.metrics.error_rate_percent.status.value,
                },
                "latency_p95": {
                    "value": snapshot.metrics.latency_p95_seconds.value,
                    "status": snapshot.metrics.latency_p95_seconds.status.value,
                },
                "cpu_utilization": {
                    "value": snapshot.metrics.cpu_utilization_percent.value,
                    "status": snapshot.metrics.cpu_utilization_percent.status.value,
                },
                "dependency_healthy": {
                    "value": snapshot.metrics.dependency_healthy.value,
                    "status": snapshot.metrics.dependency_healthy.status.value,
                },
            },
            "pods_count": len(snapshot.pods),
            "pods_ready_count": sum(1 for p in snapshot.pods if p.is_ready),
        }

        # Rule 1: Dependency Failure has top precedence over application symptoms
        if report.dependency_failure_sustained:
            reason = (
                "Upstream dependency probe failed across consecutive checks. "
                "Any observed application error rate or latency is attributed to dependency outage; "
                "restarting application pods would not resolve this issue."
            )
            logger.info("Diagnosis: DEPENDENCY_FAILURE for workload %s", workload)
            return DiagnosisResult(
                diagnosis_type=DiagnosisType.DEPENDENCY_FAILURE,
                affected_workload=workload,
                affected_pods=[],
                confidence=0.95,
                reason=reason,
                evidence=evidence,
            )

        # Rule 2: Node Failure Precedence
        if report.node_failure_sustained:
            affected_pods = [
                p.pod_name for p in snapshot.pods if not p.node_ready and p.node_name
            ]
            reason = (
                f"Sustained node failure detected for node(s) hosting pods: {affected_pods}. "
                "Issue stems from infrastructure node condition, not application container logic."
            )
            logger.warning("Diagnosis: NODE_FAILURE for workload %s", workload)
            return DiagnosisResult(
                diagnosis_type=DiagnosisType.NODE_FAILURE,
                affected_workload=workload,
                affected_pods=affected_pods,
                confidence=0.90,
                reason=reason,
                evidence=evidence,
            )

        # Rule 3: Pod / Container Infrastructure Failure
        if report.pod_failure_sustained:
            affected_pods = report.unhealthy_pods
            reason = (
                f"Sustained pod failure or not-ready condition for pods: {affected_pods}. "
                "Pods are failing Kubernetes health checks or terminated prematurely."
            )
            logger.info("Diagnosis: POD_FAILURE for workload %s (%s)", workload, affected_pods)
            return DiagnosisResult(
                diagnosis_type=DiagnosisType.POD_FAILURE,
                affected_workload=workload,
                affected_pods=affected_pods,
                confidence=0.95,
                reason=reason,
                evidence=evidence,
            )

        # Rule 4: Resource Pressure (High CPU + High Latency)
        if report.resource_pressure_sustained:
            reason = (
                "Sustained high CPU utilization accompanied by elevated P95 latency. "
                "Workload is experiencing resource exhaustion under traffic load."
            )
            logger.info("Diagnosis: RESOURCE_PRESSURE for workload %s", workload)
            return DiagnosisResult(
                diagnosis_type=DiagnosisType.RESOURCE_PRESSURE,
                affected_workload=workload,
                affected_pods=[p.pod_name for p in snapshot.pods],
                confidence=0.88,
                reason=reason,
                evidence=evidence,
            )

        # Rule 5: Application Degradation (High error rate or latency while Pods are Ready)
        if report.app_degradation_sustained:
            reason = (
                "Sustained application-level degradation (5xx error rate or elevated latency) "
                "detected via Prometheus while Kubernetes reports all pods in Ready state. "
                "Kubernetes native liveness probes did not catch this degradation."
            )
            logger.info("Diagnosis: APPLICATION_DEGRADATION for workload %s", workload)
            return DiagnosisResult(
                diagnosis_type=DiagnosisType.APPLICATION_DEGRADATION,
                affected_workload=workload,
                affected_pods=[p.pod_name for p in snapshot.pods],
                confidence=0.90,
                reason=reason,
                evidence=evidence,
            )

        # Check for Insufficient Telemetry Data
        metrics_available = any(
            m.status == MetricStatus.AVAILABLE
            for m in [
                snapshot.metrics.error_rate_percent,
                snapshot.metrics.latency_p95_seconds,
            ]
        )
        if not metrics_available and len(snapshot.pods) == 0:
            reason = "No pods found and application telemetry unavailable."
            return DiagnosisResult(
                diagnosis_type=DiagnosisType.INSUFFICIENT_DATA,
                affected_workload=workload,
                affected_pods=[],
                confidence=0.40,
                reason=reason,
                evidence=evidence,
            )

        # Default: System is Healthy
        reason = "All observed Kubernetes pod states and application telemetry are nominal."
        return DiagnosisResult(
            diagnosis_type=DiagnosisType.HEALTHY,
            affected_workload=workload,
            affected_pods=[],
            confidence=1.0,
            reason=reason,
            evidence=evidence,
        )
