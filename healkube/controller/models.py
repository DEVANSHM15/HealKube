"""
Data models and domain schemas for HealKube.
"""

import math
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class MetricStatus(str, Enum):
    AVAILABLE = "available"
    UNKNOWN = "unknown"
    ERROR = "error"


class MetricValue(BaseModel):
    """Encapsulates a metric reading, gracefully handling missing or unknown states."""
    value: Optional[float] = None
    status: MetricStatus = MetricStatus.UNKNOWN
    unit: str = ""
    error_message: Optional[str] = None

    @field_validator("value", mode="after")
    @classmethod
    def sanitize_float(cls, v):
        if v is not None and (math.isnan(v) or math.isinf(v)):
            return 0.0
        return v


class PodHealthState(BaseModel):
    """Detailed health status of an individual Kubernetes Pod."""
    pod_name: str
    namespace: str
    phase: str = "Unknown"  # Running, Pending, Succeeded, Failed, Unknown
    is_ready: bool = False
    restart_count: int = 0
    node_name: Optional[str] = None
    node_ready: bool = True
    deletion_timestamp: Optional[str] = None
    recent_events: List[str] = Field(default_factory=list)


class DeploymentHealthState(BaseModel):
    """Detailed health status of a managed Deployment."""
    name: str
    namespace: str
    desired_replicas: int = 0
    ready_replicas: int = 0
    available_replicas: int = 0
    has_hpa: bool = False


class ApplicationMetrics(BaseModel):
    """Application and infrastructure signals."""
    error_rate_percent: MetricValue = Field(default_factory=lambda: MetricValue(unit="%"))
    latency_p95_seconds: MetricValue = Field(default_factory=lambda: MetricValue(unit="s"))
    cpu_utilization_percent: MetricValue = Field(default_factory=lambda: MetricValue(unit="%"))
    memory_utilization_percent: MetricValue = Field(default_factory=lambda: MetricValue(unit="%"))
    dependency_healthy: MetricValue = Field(default_factory=lambda: MetricValue(unit="bool"))


class SystemSnapshot(BaseModel):
    """Complete observed system snapshot across K8s and telemetry at a point in time."""
    timestamp: float
    target_namespace: str
    target_deployment: str
    deployment: DeploymentHealthState
    pods: List[PodHealthState] = Field(default_factory=list)
    metrics: ApplicationMetrics = Field(default_factory=ApplicationMetrics)


class DiagnosisType(str, Enum):
    HEALTHY = "HEALTHY"
    POD_FAILURE = "POD_FAILURE"
    RESOURCE_PRESSURE = "RESOURCE_PRESSURE"
    APPLICATION_DEGRADATION = "APPLICATION_DEGRADATION"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    NODE_FAILURE = "NODE_FAILURE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DiagnosisResult(BaseModel):
    """Root cause classification with explainable evidence."""
    diagnosis_type: DiagnosisType
    affected_workload: str
    affected_pods: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    reason: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class RemediationActionType(str, Enum):
    NO_ACTION = "NO_ACTION"
    RESTART_POD = "RESTART_POD"
    SCALE_UP = "SCALE_UP"
    REPORT_DEPENDENCY_ISSUE = "REPORT_DEPENDENCY_ISSUE"
    REPORT_NODE_ISSUE = "REPORT_NODE_ISSUE"


class RemediationDecision(BaseModel):
    """Action selected by policy engine."""
    action: RemediationActionType
    target_namespace: str
    target_workload: str
    target_pod: Optional[str] = None
    target_replicas: Optional[int] = None
    reason: str
    dry_run: bool = True
    timestamp: float


class VerificationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class VerificationResult(BaseModel):
    """Outcome of post-remediation verification."""
    status: VerificationStatus
    checks_completed: int
    duration_seconds: float
    details: str


class AuditRecord(BaseModel):
    """Structured audit trail item."""
    event_id: str
    timestamp: str  # ISO-8601
    target_namespace: str
    target_workload: str
    diagnosis: DiagnosisResult
    decision: RemediationDecision
    action_executed: bool
    execution_error: Optional[str] = None
    verification: Optional[VerificationResult] = None
