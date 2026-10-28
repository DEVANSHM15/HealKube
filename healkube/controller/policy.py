"""
Policy Engine for HealKube.
Enforces safeguards, allowlists, cooldown periods, replica limits,
and selects safe remediation actions based on diagnosis.
"""

import time
import logging
from typing import Dict, Optional
from healkube.controller.config import ControllerConfig
from healkube.controller.models import (
    SystemSnapshot,
    DiagnosisResult,
    DiagnosisType,
    RemediationDecision,
    RemediationActionType,
)

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Evaluates diagnosis against safety policies and generates remediation decisions."""

    def __init__(self, config: ControllerConfig):
        self.config = config
        self.last_remediation_timestamps: Dict[str, float] = {}

    def _get_workload_key(self, namespace: str, workload: str) -> str:
        return f"{namespace}/{workload}"

    def record_remediation(self, namespace: str, workload: str, timestamp: Optional[float] = None):
        """Records timestamp of an executed remediation to enforce cooldown."""
        key = self._get_workload_key(namespace, workload)
        self.last_remediation_timestamps[key] = timestamp or time.time()

    def is_in_cooldown(self, namespace: str, workload: str, current_time: float) -> bool:
        """Checks if a workload is within its cooldown window."""
        key = self._get_workload_key(namespace, workload)
        last_time = self.last_remediation_timestamps.get(key)
        if last_time is None:
            return False
        return (current_time - last_time) < self.config.cooldown_period_seconds

    def evaluate(self, snapshot: SystemSnapshot, diagnosis: DiagnosisResult) -> RemediationDecision:
        """
        Determines the safe remediation action to take, enforcing all security and operational gates.
        """
        now = time.time()
        ns = snapshot.target_namespace
        workload = snapshot.target_deployment

        # Gate 1: Namespace Allowlist
        if ns not in self.config.namespace_allowlist:
            reason = f"Namespace '{ns}' is not in allowed list: {self.config.namespace_allowlist}"
            logger.warning("Policy rejected: %s", reason)
            return RemediationDecision(
                action=RemediationActionType.NO_ACTION,
                target_namespace=ns,
                target_workload=workload,
                reason=reason,
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        # Gate 2: Workload Allowlist
        if workload not in self.config.workload_allowlist:
            reason = f"Workload '{workload}' is not in allowed list: {self.config.workload_allowlist}"
            logger.warning("Policy rejected: %s", reason)
            return RemediationDecision(
                action=RemediationActionType.NO_ACTION,
                target_namespace=ns,
                target_workload=workload,
                reason=reason,
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        # Gate 3: Healthy or Insufficient Data requires no remediation
        if diagnosis.diagnosis_type in [DiagnosisType.HEALTHY, DiagnosisType.INSUFFICIENT_DATA]:
            return RemediationDecision(
                action=RemediationActionType.NO_ACTION,
                target_namespace=ns,
                target_workload=workload,
                reason=f"No action required for diagnosis '{diagnosis.diagnosis_type.value}'",
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        # Gate 4: Cooldown enforcement (only applies when action would otherwise be taken)
        if self.is_in_cooldown(ns, workload, now):
            elapsed = now - self.last_remediation_timestamps[self._get_workload_key(ns, workload)]
            remaining = self.config.cooldown_period_seconds - elapsed
            reason = (
                f"Workload {ns}/{workload} is in cooldown period "
                f"({remaining:.1f}s remaining of {self.config.cooldown_period_seconds}s cooldown). "
                "Preventing flapping / repeated remediation."
            )
            logger.info("Policy cooldown active: %s", reason)
            return RemediationDecision(
                action=RemediationActionType.NO_ACTION,
                target_namespace=ns,
                target_workload=workload,
                reason=reason,
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        # Action Selection based on Diagnosis
        if diagnosis.diagnosis_type == DiagnosisType.DEPENDENCY_FAILURE:
            return RemediationDecision(
                action=RemediationActionType.REPORT_DEPENDENCY_ISSUE,
                target_namespace=ns,
                target_workload=workload,
                reason=(
                    "Dependency failure diagnosed. Not restarting application pods as this "
                    "would not resolve external dependency outage."
                ),
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        if diagnosis.diagnosis_type == DiagnosisType.NODE_FAILURE:
            return RemediationDecision(
                action=RemediationActionType.REPORT_NODE_ISSUE,
                target_namespace=ns,
                target_workload=workload,
                reason=(
                    "Node failure diagnosed. Reporting condition; relying on Kubernetes node "
                    "controller and scheduler for workload reassignment."
                ),
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        if diagnosis.diagnosis_type == DiagnosisType.RESOURCE_PRESSURE:
            if not self.config.allow_scaling:
                return RemediationDecision(
                    action=RemediationActionType.NO_ACTION,
                    target_namespace=ns,
                    target_workload=workload,
                    reason="Resource pressure diagnosed, but scaling is disabled in policy config.",
                    dry_run=self.config.dry_run,
                    timestamp=now,
                )

            # Avoid fighting HPA if configured
            if self.config.check_hpa_exists and snapshot.deployment.has_hpa:
                return RemediationDecision(
                    action=RemediationActionType.NO_ACTION,
                    target_namespace=ns,
                    target_workload=workload,
                    reason="HPA is already configured for this deployment. Avoiding conflicting manual scaling.",
                    dry_run=self.config.dry_run,
                    timestamp=now,
                )

            current_replicas = snapshot.deployment.desired_replicas
            if current_replicas >= self.config.max_replicas:
                return RemediationDecision(
                    action=RemediationActionType.NO_ACTION,
                    target_namespace=ns,
                    target_workload=workload,
                    reason=f"Current replicas ({current_replicas}) already at max limit ({self.config.max_replicas}).",
                    dry_run=self.config.dry_run,
                    timestamp=now,
                )

            target_replicas = min(
                current_replicas + self.config.scaling_step,
                self.config.max_replicas,
            )
            return RemediationDecision(
                action=RemediationActionType.SCALE_UP,
                target_namespace=ns,
                target_workload=workload,
                target_replicas=target_replicas,
                reason=(
                    f"Scaling up deployment from {current_replicas} to {target_replicas} replicas "
                    f"to alleviate resource pressure."
                ),
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        if diagnosis.diagnosis_type in [
            DiagnosisType.POD_FAILURE,
            DiagnosisType.APPLICATION_DEGRADATION,
        ]:
            if not self.config.allow_pod_restart:
                return RemediationDecision(
                    action=RemediationActionType.NO_ACTION,
                    target_namespace=ns,
                    target_workload=workload,
                    reason="Pod restart is disabled in policy config.",
                    dry_run=self.config.dry_run,
                    timestamp=now,
                )

            # Select target pod
            target_pod = None
            if diagnosis.affected_pods:
                target_pod = diagnosis.affected_pods[0]
            elif snapshot.pods:
                target_pod = snapshot.pods[0].pod_name

            if not target_pod:
                return RemediationDecision(
                    action=RemediationActionType.NO_ACTION,
                    target_namespace=ns,
                    target_workload=workload,
                    reason="No pod found to restart for workload.",
                    dry_run=self.config.dry_run,
                    timestamp=now,
                )

            action_desc = (
                "pod failure"
                if diagnosis.diagnosis_type == DiagnosisType.POD_FAILURE
                else "application degradation"
            )
            return RemediationDecision(
                action=RemediationActionType.RESTART_POD,
                target_namespace=ns,
                target_workload=workload,
                target_pod=target_pod,
                reason=(
                    f"Initiating controlled replacement of pod '{target_pod}' due to {action_desc}. "
                    "Deployment controller will replace with healthy pod."
                ),
                dry_run=self.config.dry_run,
                timestamp=now,
            )

        # Default fallback
        return RemediationDecision(
            action=RemediationActionType.NO_ACTION,
            target_namespace=ns,
            target_workload=workload,
            reason="No applicable policy rule for diagnosis.",
            dry_run=self.config.dry_run,
            timestamp=now,
        )
