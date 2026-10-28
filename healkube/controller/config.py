"""
Configuration management for HealKube controller using Pydantic Settings.
"""

from typing import List, Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ControllerConfig(BaseSettings):
    """Configuration settings for HealKube controller."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Kubernetes Cluster Access
    kubernetes_mode: Literal["in_cluster", "kubeconfig", "local_mock"] = Field(
        default="local_mock",
        description="Kubernetes connection mode: in_cluster, kubeconfig, or local_mock",
    )
    kubeconfig_path: Optional[str] = Field(
        default=None,
        description="Optional path to kubeconfig file when using kubeconfig mode",
    )

    # Workload Scoping & Allowlists
    target_namespace: str = Field(
        default="healkube-demo",
        description="Kubernetes namespace to monitor",
    )
    target_deployment: str = Field(
        default="demo-app",
        description="Target deployment name to monitor and manage",
    )
    namespace_allowlist: List[str] = Field(
        default=["healkube-demo"],
        description="Allowed namespaces for any remediation mutation",
    )
    workload_allowlist: List[str] = Field(
        default=["demo-app"],
        description="Allowed deployment/workload names for any remediation mutation",
    )

    # Prometheus Monitoring
    prometheus_url: str = Field(
        default="http://localhost:9090",
        description="Base URL of Prometheus server",
    )
    prometheus_query_timeout_seconds: float = Field(
        default=5.0,
        description="Timeout for Prometheus queries in seconds",
    )

    # Monitoring & Polling
    poll_interval_seconds: float = Field(
        default=5.0,
        description="Polling loop sleep interval in seconds",
    )

    # Anomaly Detection Thresholds
    consecutive_breaches_required: int = Field(
        default=3,
        description="Number of consecutive anomaly observations before triggering remediation",
    )
    error_rate_threshold_percent: float = Field(
        default=5.0,
        description="5xx HTTP error rate percentage threshold",
    )
    latency_p95_threshold_seconds: float = Field(
        default=1.0,
        description="P95 latency threshold in seconds",
    )
    cpu_utilization_threshold_percent: float = Field(
        default=85.0,
        description="CPU utilization percentage threshold",
    )
    memory_utilization_threshold_percent: float = Field(
        default=90.0,
        description="Memory utilization percentage threshold",
    )

    # Dependency Health Check
    dependency_check_enabled: bool = Field(
        default=True,
        description="Whether dependency health checks are active",
    )
    dependency_health_url: Optional[str] = Field(
        default=None,
        description="HTTP endpoint to verify upstream/downstream dependency health",
    )
    dependency_timeout_seconds: float = Field(
        default=3.0,
        description="Timeout for dependency health HTTP check",
    )

    # Policy & Remediation Safeguards
    dry_run: bool = Field(
        default=True,
        description="Dry run mode: logs and audits actions without executing mutations on K8s",
    )
    cooldown_period_seconds: float = Field(
        default=60.0,
        description="Minimum seconds required between remediation actions on the same workload",
    )
    allow_pod_restart: bool = Field(
        default=True,
        description="Permit deleting unhealthy pods for managed deployments",
    )
    allow_scaling: bool = Field(
        default=True,
        description="Permit horizontal replica scaling under resource pressure",
    )
    min_replicas: int = Field(
        default=1,
        description="Lower bound for horizontal scaling",
    )
    max_replicas: int = Field(
        default=5,
        description="Upper bound for horizontal scaling",
    )
    scaling_step: int = Field(
        default=1,
        description="Number of replicas to add per scaling event",
    )
    check_hpa_exists: bool = Field(
        default=True,
        description="Check if an HPA already controls the deployment to avoid conflicting actions",
    )

    # Recovery Verification
    verification_wait_seconds: float = Field(
        default=10.0,
        description="Stabilization delay after remediation before verification begins",
    )
    verification_checks_required: int = Field(
        default=3,
        description="Number of consecutive healthy checks required to declare recovery",
    )
    verification_interval_seconds: float = Field(
        default=5.0,
        description="Interval between verification health checks",
    )
    verification_timeout_seconds: float = Field(
        default=60.0,
        description="Maximum seconds to wait before marking verification timed out",
    )

    # Audit & Status Server
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    audit_log_file: str = Field(
        default="audit_events.jsonl",
        description="File path for structured JSONL audit logs",
    )
    status_server_port: int = Field(
        default=8080,
        description="Port for optional controller status HTTP endpoint",
    )
    enable_status_server: bool = Field(
        default=True,
        description="Whether to run the lightweight status server",
    )


def get_config() -> ControllerConfig:
    """Returns an instance of ControllerConfig."""
    return ControllerConfig()
