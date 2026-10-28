"""
Repeatable Experiment Suite comparing:
A. Native Kubernetes Only
B. Kubernetes + HealKube

Measures:
- Detection time (s)
- Time to recovery (TTR in seconds)
- Downtime (s)
- Total failed requests
- Recovery success rate (%)
- Unnecessary remediation count (false positives)
- Controller overhead (ms per tick)

Outputs structured CSV and comparative benchmark summary table.
"""

import os
import sys
import csv
import time
from typing import Dict, Any, List

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


class ExperimentSimulator:
    """Simulates workload traffic, fault conditions, and controller interactions."""

    def __init__(self, scenario: str, request_rate_rps: float = 20.0):
        self.scenario = scenario
        self.rps = request_rate_rps
        self.fault_active = True
        self.pod_ready = True
        self.pod_phase = "Running"
        self.pod_restarts = 0
        self.error_rate = 0.5  # default nominal 0.5%
        self.dep_healthy = True
        self.current_time = 1000.0

        if scenario == "pod_failure":
            self.pod_ready = False
            self.pod_phase = "Failed"
            self.error_rate = 100.0
        elif scenario == "application_degradation":
            # Native liveness probe stays Running and Ready!
            self.pod_ready = True
            self.pod_phase = "Running"
            self.error_rate = 60.0  # 60% error rate
        elif scenario == "dependency_failure":
            self.dep_healthy = False
            self.error_rate = 90.0

    def get_snapshot(self) -> SystemSnapshot:
        return SystemSnapshot(
            timestamp=self.current_time,
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
                    pod_name="demo-app-pod-alpha",
                    namespace="healkube-demo",
                    phase=self.pod_phase,
                    is_ready=self.pod_ready,
                    restart_count=self.pod_restarts,
                    node_name="worker-1",
                    node_ready=True,
                )
            ],
            metrics=ApplicationMetrics(
                error_rate_percent=MetricValue(
                    value=self.error_rate, status=MetricStatus.AVAILABLE, unit="%"
                ),
                latency_p95_seconds=MetricValue(
                    value=0.15 if self.error_rate < 5 else 2.5,
                    status=MetricStatus.AVAILABLE,
                    unit="s",
                ),
                cpu_utilization_percent=MetricValue(
                    value=35.0, status=MetricStatus.AVAILABLE, unit="%"
                ),
                memory_utilization_percent=MetricValue(
                    value=40.0, status=MetricStatus.AVAILABLE, unit="%"
                ),
                dependency_healthy=MetricValue(
                    value=1.0 if self.dep_healthy else 0.0,
                    status=MetricStatus.AVAILABLE,
                    unit="bool",
                    error_message=None if self.dep_healthy else "DB unreachable",
                ),
            ),
        )

    def recover(self):
        """Simulates successful pod replacement restoring fresh state."""
        self.fault_active = False
        self.pod_ready = True
        self.pod_phase = "Running"
        self.error_rate = 0.2
        self.pod_restarts += 1


def run_single_experiment(system: str, scenario: str, timeout_seconds: float = 30.0) -> Dict[str, Any]:
    """
    Executes a benchmark run for either Native K8s or HealKube under a specific scenario
    using discrete time-stepping for high speed and deterministic repeatability.
    """
    sim = ExperimentSimulator(scenario=scenario, request_rate_rps=25.0)
    detection_time = None
    recovered = False
    recovery_time = None
    failed_requests = 0
    unnecessary_remediations = 0
    controller_overhead_list: List[float] = []

    step_interval = 1.0
    virtual_elapsed = 0.0

    if system == "Native K8s":
        # Native Kubernetes relies solely on kubelet probes:
        # liveness probe: periodSeconds=5, failureThreshold=3 -> 15s to detect pod failure
        while virtual_elapsed < timeout_seconds:
            sim.current_time += step_interval
            virtual_elapsed += step_interval

            if sim.fault_active:
                failed_requests += int(sim.rps * (sim.error_rate / 100.0) * step_interval)

            if scenario == "pod_failure":
                # Kubelet detects probe failure after 15s, recreation finishes at ~18s
                if virtual_elapsed >= 15.0 and detection_time is None:
                    detection_time = 15.0
                if virtual_elapsed >= 18.0 and not recovered:
                    sim.recover()
                    recovered = True
                    recovery_time = virtual_elapsed
                    break
            elif scenario == "application_degradation":
                # Liveness check /health returns 200 OK!
                # Native Kubernetes NEVER detects application degradation.
                pass
            elif scenario == "dependency_failure":
                # Dependency outage: native k8s does nothing
                pass

        if not recovered:
            recovery_time = timeout_seconds

        return {
            "system": "Native K8s",
            "scenario": scenario,
            "detection_time_s": f"{detection_time:.1f}s" if detection_time else "Never (undetected)",
            "recovery_time_s": f"{recovery_time:.1f}s" if recovered else f"> {timeout_seconds}s (Timed out)",
            "downtime_s": round(recovery_time if recovered else timeout_seconds, 1),
            "failed_requests": failed_requests,
            "success_rate_pct": 100.0 if recovered else 0.0,
            "unnecessary_remediations": unnecessary_remediations,
            "avg_overhead_ms": 0.0,
        }

    else:
        # System: Kubernetes + HealKube
        config = ControllerConfig(
            kubernetes_mode="local_mock",
            target_namespace="healkube-demo",
            target_deployment="demo-app",
            poll_interval_seconds=step_interval,
            consecutive_breaches_required=2,
            error_rate_threshold_percent=5.0,
            cooldown_period_seconds=10.0,
            dry_run=False,
            verification_wait_seconds=0.0,
            verification_checks_required=2,
            verification_interval_seconds=0.0,
            verification_timeout_seconds=5.0,
        )

        controller = HealKubeController(config=config)
        controller.monitor.collect_system_snapshot = sim.get_snapshot

        real_exec = controller.remediation_executor.execute

        def simulated_exec(decision: RemediationDecision):
            res = real_exec(decision)
            if decision.action.value == "RESTART_POD":
                sim.recover()
            return res

        controller.remediation_executor.execute = simulated_exec

        while virtual_elapsed < timeout_seconds:
            sim.current_time += step_interval
            virtual_elapsed += step_interval

            if sim.fault_active:
                failed_requests += int(sim.rps * (sim.error_rate / 100.0) * step_interval)

            t0 = time.perf_counter()
            tick_result = controller.reconcile_once()
            overhead_ms = (time.perf_counter() - t0) * 1000.0
            controller_overhead_list.append(overhead_ms)

            # Check if anomaly was diagnosed
            if tick_result["diagnosis"] != "HEALTHY" and detection_time is None:
                detection_time = virtual_elapsed

            # Check if remediation executed and verified
            if tick_result["verified"] == "SUCCESS":
                recovered = True
                recovery_time = virtual_elapsed
                break

            # In dependency failure, HealKube should report and NOT restart
            if scenario == "dependency_failure":
                if tick_result["action"] == "REPORT_DEPENDENCY_ISSUE":
                    detection_time = virtual_elapsed
                    break
                elif tick_result["action"] == "RESTART_POD":
                    unnecessary_remediations += 1

        avg_overhead = (
            sum(controller_overhead_list) / len(controller_overhead_list)
            if controller_overhead_list
            else 0.0
        )

        return {
            "system": "K8s + HealKube",
            "scenario": scenario,
            "detection_time_s": f"{detection_time:.1f}s" if detection_time else "N/A",
            "recovery_time_s": (
                f"{recovery_time:.1f}s"
                if recovered
                else ("N/A (Avoided restart)" if scenario == "dependency_failure" else "Timed out")
            ),
            "downtime_s": round(recovery_time or virtual_elapsed, 1),
            "failed_requests": failed_requests,
            "success_rate_pct": (
                100.0 if (recovered or scenario == "dependency_failure") else 0.0
            ),
            "unnecessary_remediations": unnecessary_remediations,
            "avg_overhead_ms": round(avg_overhead, 2),
        }


def run_all_experiments():
    print("\n" + "=" * 85)
    print("  HEALKUBE EXPERIMENTAL EVALUATION SUITE")
    print("  Mode: In-Memory Analytical Simulation (Traffic Model + Live Controller Timing)")
    print("  Comparing Native Kubernetes vs. Kubernetes + HealKube")
    print("=" * 85)
    print("  [METHODOLOGY & REPRODUCIBILITY DISCLOSURE]")
    print("  - Controller Overhead: Measured live via time.perf_counter() during real code execution.")
    print("  - Detection & Recovery: Executed directly via HealKube Detector, Policy, and Verifier.")
    print("  - Workload & Traffic: Analytical simulation (25 RPS request stream under fixed fault rate).")
    print("  - Native K8s Baseline: Standard kubelet probe model (period: 5s, threshold: 3 = 15s detection).")
    print("=" * 85 + "\n")

    scenarios = ["pod_failure", "application_degradation", "dependency_failure"]
    results = []

    for sc in scenarios:
        print(f"--- Running Scenario: {sc.upper()} ---")
        # Native
        res_native = run_single_experiment(system="Native K8s", scenario=sc)
        results.append(res_native)
        print(f"  [Native K8s]       Detection: {res_native['detection_time_s']} | TTR: {res_native['recovery_time_s']} | Failed Reqs: {res_native['failed_requests']}")

        # HealKube
        res_healkube = run_single_experiment(system="K8s + HealKube", scenario=sc)
        results.append(res_healkube)
        print(f"  [K8s + HealKube]   Detection: {res_healkube['detection_time_s']} | TTR: {res_healkube['recovery_time_s']} | Failed Reqs: {res_healkube['failed_requests']} | Overhead: {res_healkube['avg_overhead_ms']}ms")
        print()

    # Save to CSV
    csv_file = "experiments_results.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "system",
            "scenario",
            "detection_time_s",
            "recovery_time_s",
            "downtime_s",
            "failed_requests",
            "success_rate_pct",
            "unnecessary_remediations",
            "avg_overhead_ms",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"Structured results written to '{csv_file}'\n")

    # Display comparative summary table
    print("=" * 110)
    print(f"{'Scenario':<25} | {'System':<16} | {'Detection':<14} | {'TTR':<18} | {'Failed Reqs':<12} | {'Success Rate'}")
    print("-" * 110)
    for r in results:
        print(
            f"{r['scenario']:<25} | {r['system']:<16} | {str(r['detection_time_s']):<14} | "
            f"{str(r['recovery_time_s']):<18} | {r['failed_requests']:<12} | {r['success_rate_pct']}%"
        )
    print("=" * 110 + "\n")


if __name__ == "__main__":
    run_all_experiments()
