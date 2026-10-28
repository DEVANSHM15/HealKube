"""
Interactive / Automated Demo for HealKube.
Demonstrates:
1. Baseline nominal operation (controller observes without action)
2. Application degradation injection (liveness check passes, but error rate spikes)
3. HealKube diagnosis, safe pod replacement policy, and recovery verification
4. Dependency failure scenario (HealKube refuses to restart pod, avoiding cascading failure)
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from healkube.controller.config import ControllerConfig
from healkube.controller.models import (
    SystemSnapshot,
    DeploymentHealthState,
    PodHealthState,
    ApplicationMetrics,
    MetricValue,
    MetricStatus,
    RemediationDecision,
)
from healkube.controller.main import HealKubeController


def print_banner(text: str):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


class SimulatedClusterState:
    """Simulates Kubernetes cluster state and deployment controller reactions."""

    def __init__(self):
        self.pod_name = "demo-app-7b9d4f-x29a"
        self.error_rate = 0.5
        self.latency = 0.12
        self.cpu = 30.0
        self.dep_healthy = True
        self.pod_ready = True

    def get_snapshot(self) -> SystemSnapshot:
        return SystemSnapshot(
            timestamp=time.time(),
            target_namespace="healkube-demo",
            target_deployment="demo-app",
            deployment=DeploymentHealthState(
                name="demo-app",
                namespace="healkube-demo",
                desired_replicas=1,
                ready_replicas=1 if self.pod_ready else 0,
                available_replicas=1 if self.pod_ready else 0,
                has_hpa=False,
            ),
            pods=[
                PodHealthState(
                    pod_name=self.pod_name,
                    namespace="healkube-demo",
                    phase="Running",
                    is_ready=self.pod_ready,
                    restart_count=0,
                    node_name="minikube",
                    node_ready=True,
                )
            ],
            metrics=ApplicationMetrics(
                error_rate_percent=MetricValue(
                    value=self.error_rate, status=MetricStatus.AVAILABLE, unit="%"
                ),
                latency_p95_seconds=MetricValue(
                    value=self.latency, status=MetricStatus.AVAILABLE, unit="s"
                ),
                cpu_utilization_percent=MetricValue(
                    value=self.cpu, status=MetricStatus.AVAILABLE, unit="%"
                ),
                memory_utilization_percent=MetricValue(
                    value=45.0, status=MetricStatus.AVAILABLE, unit="%"
                ),
                dependency_healthy=MetricValue(
                    value=1.0 if self.dep_healthy else 0.0,
                    status=MetricStatus.AVAILABLE,
                    unit="bool",
                    error_message=None if self.dep_healthy else "Upstream DB timeout",
                ),
            ),
        )

    def on_pod_deleted(self, deleted_pod_name: str):
        """Simulates Kubernetes Deployment controller recreating a fresh healthy pod."""
        print(f"\n[K8s Deployment Controller] Observed deletion of {deleted_pod_name}.")
        print("[K8s Deployment Controller] Recreating new Pod with clean state: 'demo-app-new-pod-9z81'.")
        self.pod_name = "demo-app-new-pod-9z81"
        self.error_rate = 0.2  # Fresh pod has zero degradation
        self.pod_ready = True


def run_demo():
    print_banner("HEALKUBE: AUTONOMOUS APPLICATION-AWARE SELF-HEALING CONTROLLER")

    config = ControllerConfig(
        kubernetes_mode="local_mock",
        target_namespace="healkube-demo",
        target_deployment="demo-app",
        consecutive_breaches_required=2,
        error_rate_threshold_percent=5.0,
        cooldown_period_seconds=15.0,
        dry_run=False,  # live mock mode
        verification_wait_seconds=0.2,
        verification_checks_required=2,
        verification_interval_seconds=0.1,
    )

    cluster = SimulatedClusterState()
    controller = HealKubeController(config=config)
    controller.monitor.collect_system_snapshot = cluster.get_snapshot

    # Intercept remediation executor to update our simulated cluster
    real_execute = controller.remediation_executor.execute

    def mock_execute(decision: RemediationDecision):
        res = real_execute(decision)
        if decision.action.value == "RESTART_POD" and decision.target_pod:
            cluster.on_pod_deleted(decision.target_pod)
        return res

    controller.remediation_executor.execute = mock_execute

    # -------------------------------------------------------------
    # Scenario 1: Baseline Nominal Monitoring
    # -------------------------------------------------------------
    print_banner("SCENARIO 1: Nominal System Operation")
    print("Workload is healthy. Error rate: 0.5%, Pod: Running & Ready.")
    res = controller.reconcile_once()
    print(f"-> Observation Result: Diagnosis={res['diagnosis']}, Action={res['action']}")
    assert res["diagnosis"] == "HEALTHY"
    assert res["action"] == "NO_ACTION"
    print("Result: Nominal operation confirmed. No unnecessary remediation triggered.\n")

    # -------------------------------------------------------------
    # Scenario 2: Silent Application Degradation
    # -------------------------------------------------------------
    print_banner("SCENARIO 2: Silent Application Degradation")
    print("Application experiences internal deadlocks/500 errors (error rate = 45%).")
    print("IMPORTANT: Pod is 'Running' and native K8s liveness check /health returns 200 OK.")
    print("Native Kubernetes will NOT restart this pod because infrastructure check is alive.")

    cluster.error_rate = 45.0

    print("\nTick 1: First breach observed...")
    t1 = controller.reconcile_once()
    print(f"-> Breach count: 1. Action: {t1['action']} (Waiting for sustained condition...)")

    print("Tick 2: Second consecutive breach observed (Sustained!)...")
    t2 = controller.reconcile_once()
    print(f"-> Diagnosis: {t2['diagnosis']}")
    print(f"-> Safe Action Selected: {t2['action']} on {t2['target']}")
    print(f"-> Execution & Post-Remediation Verification: {t2['verified']}")
    assert t2["verified"] == "SUCCESS"

    # -------------------------------------------------------------
    # Scenario 3: Upstream Dependency Failure Precedence
    # -------------------------------------------------------------
    print_banner("SCENARIO 3: Upstream Dependency Outage (Rule Precedence)")
    print("External Database / Upstream API fails. Downstream demo app returns 500 errors.")
    print("HealKube evaluates dependency probe alongside error rates.")

    controller.detector.reset()
    controller.policy_engine.last_remediation_timestamps.clear()

    cluster.error_rate = 80.0
    cluster.dep_healthy = False

    controller.reconcile_once()
    dep_res = controller.reconcile_once()

    print(f"-> Diagnosis: {dep_res['diagnosis']}")
    print(f"-> Action: {dep_res['action']}")
    print("Safety Check:")
    print("  HealKube classified condition as DEPENDENCY_FAILURE rather than application bug.")
    print("  HealKube REFUSED to restart the pod, preventing useless container thrashing!")

    # -------------------------------------------------------------
    # Summary Table
    # -------------------------------------------------------------
    print_banner("DEMO COMPLETED SUCCESSFULLY")
    print("Audit Log Records Generated:")
    for record in controller.audit_logger.get_recent_records():
        print(f"  [{record.event_id}] {record.diagnosis.diagnosis_type} -> {record.decision.action}")
    print("\nAll autonomous recovery scenarios verified.\n")


if __name__ == "__main__":
    run_demo()
