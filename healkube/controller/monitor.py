"""
Kubernetes and Telemetry Monitor for HealKube.
Collects Pod states, deployment status, Kubernetes events,
dependency health, and Prometheus application metrics into a unified SystemSnapshot.
"""

import time
import logging
from typing import List, Optional
import requests

from healkube.controller.config import ControllerConfig
from healkube.controller.models import (
    SystemSnapshot,
    DeploymentHealthState,
    PodHealthState,
    ApplicationMetrics,
    MetricValue,
    MetricStatus,
)
from healkube.controller.prometheus_client import PrometheusClient

logger = logging.getLogger(__name__)


class ClusterMonitor:
    """Collects system state from Kubernetes API, Prometheus, and dependency probes."""

    def __init__(
        self,
        config: ControllerConfig,
        prom_client: Optional[PrometheusClient] = None,
        k8s_core_api=None,
        k8s_apps_api=None,
        k8s_autoscaling_api=None,
    ):
        self.config = config
        self.prom_client = prom_client or PrometheusClient(
            base_url=config.prometheus_url,
            timeout_seconds=config.prometheus_query_timeout_seconds,
        )
        self.core_api = k8s_core_api
        self.apps_api = k8s_apps_api
        self.autoscaling_api = k8s_autoscaling_api

        if self.core_api is None or self.apps_api is None:
            self._init_k8s_client()

    def _init_k8s_client(self):
        """Initializes Kubernetes client based on configured mode."""
        if self.config.kubernetes_mode == "local_mock":
            logger.info("Operating in 'local_mock' Kubernetes mode (simulated cluster)")
            return

        try:
            from kubernetes import client, config as k8s_config

            if self.config.kubernetes_mode == "in_cluster":
                k8s_config.load_incluster_config()
                logger.info("Loaded in-cluster Kubernetes configuration")
            elif self.config.kubernetes_mode == "kubeconfig":
                k8s_config.load_kube_config(config_file=self.config.kubeconfig_path)
                logger.info("Loaded local kubeconfig")

            self.core_api = client.CoreV1Api()
            self.apps_api = client.AppsV1Api()
            self.autoscaling_api = client.AutoscalingV1Api()
        except Exception as e:
            logger.warning(
                "Could not initialize Kubernetes client (%s). Defaulting to mock mode: %s",
                self.config.kubernetes_mode,
                e,
            )

    def get_deployment_state(self, namespace: str, deployment_name: str) -> DeploymentHealthState:
        """Retrieves deployment replica count and checks for HPA existence."""
        if not self.apps_api:
            # Fallback for mock mode
            return DeploymentHealthState(
                name=deployment_name,
                namespace=namespace,
                desired_replicas=1,
                ready_replicas=1,
                available_replicas=1,
                has_hpa=False,
            )

        try:
            dep = self.apps_api.read_namespaced_deployment(
                name=deployment_name, namespace=namespace
            )
            desired = dep.spec.replicas or 0
            ready = dep.status.ready_replicas or 0
            available = dep.status.available_replicas or 0

            # Check if HPA is attached to deployment
            has_hpa = False
            if self.config.check_hpa_exists and self.autoscaling_api:
                try:
                    hpa_list = self.autoscaling_api.list_namespaced_horizontal_pod_autoscaler(
                        namespace=namespace
                    )
                    for hpa in hpa_list.items:
                        target = hpa.spec.scale_target_ref
                        if target.kind == "Deployment" and target.name == deployment_name:
                            has_hpa = True
                            break
                except Exception as e:
                    logger.debug("Failed to list HPAs in %s: %s", namespace, e)

            return DeploymentHealthState(
                name=deployment_name,
                namespace=namespace,
                desired_replicas=desired,
                ready_replicas=ready,
                available_replicas=available,
                has_hpa=has_hpa,
            )
        except Exception as e:
            logger.error("Error reading deployment %s/%s: %s", namespace, deployment_name, e)
            return DeploymentHealthState(
                name=deployment_name,
                namespace=namespace,
                desired_replicas=0,
                ready_replicas=0,
                available_replicas=0,
                has_hpa=False,
            )

    def get_pods_state(self, namespace: str, workload: str) -> List[PodHealthState]:
        """Collects pod statuses, ready condition, restart count, node info, and recent events."""
        if not self.core_api:
            return [
                PodHealthState(
                    pod_name=f"{workload}-mock-abc12",
                    namespace=namespace,
                    phase="Running",
                    is_ready=True,
                    restart_count=0,
                    node_name="mock-node-1",
                    node_ready=True,
                )
            ]

        try:
            pod_list = self.core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app={workload}",
            )
            # If label selector returns empty, fallback to prefix matching
            items = pod_list.items
            if not items:
                all_pods = self.core_api.list_namespaced_pod(namespace=namespace).items
                items = [p for p in all_pods if p.metadata.name.startswith(workload)]

            results: List[PodHealthState] = []
            for pod in items:
                name = pod.metadata.name
                phase = pod.status.phase or "Unknown"

                # Check Ready condition
                is_ready = False
                if pod.status.conditions:
                    for cond in pod.status.conditions:
                        if cond.type == "Ready" and cond.status == "True":
                            is_ready = True
                            break

                # Calculate cumulative restart count across all containers
                restarts = 0
                if pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        restarts += cs.restart_count

                # Node status check
                node_name = pod.spec.node_name
                node_ready = True
                if node_name:
                    try:
                        node = self.core_api.read_node_status(node_name)
                        for cond in node.status.conditions:
                            if cond.type == "Ready" and cond.status != "True":
                                node_ready = False
                                break
                    except Exception:
                        node_ready = True  # don't fail pod collection on node RBAC limitation

                # Recent events for pod
                recent_events: List[str] = []
                try:
                    events = self.core_api.list_namespaced_event(
                        namespace=namespace,
                        field_selector=f"involvedObject.name={name}",
                    )
                    for ev in events.items[-3:]:  # last 3 events
                        recent_events.append(f"{ev.reason}: {ev.message}")
                except Exception as e:
                    logger.debug("Failed to read events for pod %s: %s", name, e)

                deletion_ts = (
                    pod.metadata.deletion_timestamp.isoformat()
                    if pod.metadata.deletion_timestamp
                    else None
                )

                results.append(
                    PodHealthState(
                        pod_name=name,
                        namespace=namespace,
                        phase=phase,
                        is_ready=is_ready,
                        restart_count=restarts,
                        node_name=node_name,
                        node_ready=node_ready,
                        deletion_timestamp=deletion_ts,
                        recent_events=recent_events,
                    )
                )

            return results
        except Exception as e:
            logger.error("Error reading pods for workload %s/%s: %s", namespace, workload, e)
            return []

    def check_dependency_health(self) -> MetricValue:
        """Probes the configured upstream dependency endpoint."""
        if not self.config.dependency_check_enabled or not self.config.dependency_health_url:
            return MetricValue(
                value=None,
                status=MetricStatus.UNKNOWN,
                unit="bool",
                error_message="Dependency health check not configured or disabled",
            )

        try:
            resp = requests.get(
                self.config.dependency_health_url,
                timeout=self.config.dependency_timeout_seconds,
            )
            if resp.status_code == 200:
                return MetricValue(value=1.0, status=MetricStatus.AVAILABLE, unit="bool")
            else:
                return MetricValue(
                    value=0.0,
                    status=MetricStatus.AVAILABLE,
                    unit="bool",
                    error_message=f"Dependency returned HTTP {resp.status_code}",
                )
        except requests.exceptions.RequestException as e:
            return MetricValue(
                value=0.0,
                status=MetricStatus.AVAILABLE,
                unit="bool",
                error_message=f"Dependency check connection failed: {str(e)}",
            )

    def collect_system_snapshot(self) -> SystemSnapshot:
        """Produces a unified SystemSnapshot combining K8s and telemetry state."""
        ns = self.config.target_namespace
        deployment_name = self.config.target_deployment
        timestamp = time.time()

        deployment_state = self.get_deployment_state(ns, deployment_name)
        pods_state = self.get_pods_state(ns, deployment_name)

        error_rate = self.prom_client.get_error_rate_percent(ns, deployment_name)
        latency = self.prom_client.get_latency_p95_seconds(ns, deployment_name)
        cpu = self.prom_client.get_cpu_utilization_percent(ns, deployment_name)
        mem = self.prom_client.get_memory_utilization_percent(ns, deployment_name)
        dependency = self.check_dependency_health()

        metrics = ApplicationMetrics(
            error_rate_percent=error_rate,
            latency_p95_seconds=latency,
            cpu_utilization_percent=cpu,
            memory_utilization_percent=mem,
            dependency_healthy=dependency,
        )

        return SystemSnapshot(
            timestamp=timestamp,
            target_namespace=ns,
            target_deployment=deployment_name,
            deployment=deployment_state,
            pods=pods_state,
            metrics=metrics,
        )
