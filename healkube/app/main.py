"""
Demo Application for HealKube.
Exposes standard liveness/readiness probes, application endpoints,
Prometheus metrics, and controlled test-only fault injection hooks.
"""

import os
import time
import random
from typing import Optional
from fastapi import FastAPI, Response, status, HTTPException, Query
from pydantic import BaseModel
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

# Configuration
APP_NAME = os.getenv("APP_NAME", "demo-app")
ENABLE_FAULT_INJECTION = os.getenv("ENABLE_FAULT_INJECTION", "false").lower() == "true"

app = FastAPI(
    title="HealKube Demo Workload",
    description="Demonstration microservice for autonomous self-healing testing",
    version="1.0.0",
)

# Prometheus Metrics
REQUESTS_TOTAL = Counter(
    "healkube_demo_requests_total",
    "Total number of HTTP requests handled by the demo app",
    ["method", "endpoint", "status"],
)

REQUEST_DURATION_SECONDS = Histogram(
    "healkube_demo_request_duration_seconds",
    "HTTP request latency histogram in seconds",
    ["endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

ERRORS_TOTAL = Counter(
    "healkube_demo_errors_total",
    "Total number of application errors generated",
    ["endpoint", "error_code"],
)

CPU_UTILIZATION_PERCENT = Gauge(
    "healkube_demo_cpu_utilization_percent",
    "Current CPU utilization percentage of the demo workload",
)

# Internal Fault State (Thread-safe dict / primitive access)
fault_state = {
    "error_rate": 0.0,  # 0.0 to 1.0
    "extra_latency_seconds": 0.0,
    "simulate_dependency_down": False,
    "cpu_utilization_percent": 0.0,
    "expires_at": 0.0,
}


def _check_fault_expiration():
    now = time.time()
    if fault_state["expires_at"] > 0 and now > fault_state["expires_at"]:
        fault_state["error_rate"] = 0.0
        fault_state["extra_latency_seconds"] = 0.0
        fault_state["simulate_dependency_down"] = False
        fault_state["cpu_utilization_percent"] = 0.0
        fault_state["expires_at"] = 0.0
        CPU_UTILIZATION_PERCENT.set(0.0)


@app.get("/health", summary="Basic Liveness Probe")
def health_check():
    """
    Kubernetes Liveness Probe endpoint.
    Crucially, this remains 200 OK even during application-level degradation,
    demonstrating scenarios where native K8s liveness probes fail to detect issues.
    """
    return {"status": "alive", "app": APP_NAME, "timestamp": time.time()}


@app.get("/ready", summary="Readiness Probe")
def readiness_check(response: Response):
    """
    Kubernetes Readiness Probe endpoint.
    Checks application readiness.
    """
    return {"status": "ready", "app": APP_NAME}


@app.get("/dependency-health", summary="Upstream Dependency Health")
def dependency_health(response: Response):
    """
    Reports the health of external/upstream dependencies (e.g., database, external API).
    Used by HealKube diagnosis to distinguish application bugs from dependency outages.
    """
    _check_fault_expiration()
    if fault_state["simulate_dependency_down"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "error": "Upstream service database/cache is unreachable",
            "timestamp": time.time(),
        }
    return {
        "status": "healthy",
        "upstream": "all services nominal",
        "timestamp": time.time(),
    }


@app.get("/api/data", summary="Normal Workload Endpoint")
def get_data():
    """
    Main business transaction endpoint.
    Simulates real work, records latency and error metrics, and applies injected faults.
    """
    start_time = time.time()
    endpoint = "/api/data"
    _check_fault_expiration()

    # Apply injected latency if configured
    if fault_state["extra_latency_seconds"] > 0:
        time.sleep(fault_state["extra_latency_seconds"])

    # Apply injected error rate if configured
    if fault_state["error_rate"] > 0 and random.random() < fault_state["error_rate"]:
        duration = time.time() - start_time
        REQUEST_DURATION_SECONDS.labels(endpoint=endpoint).observe(duration)
        REQUESTS_TOTAL.labels(method="GET", endpoint=endpoint, status="500").inc()
        ERRORS_TOTAL.labels(endpoint=endpoint, error_code="500").inc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Simulated internal application error (sustained degradation)",
        )

    # Success path
    duration = time.time() - start_time
    REQUEST_DURATION_SECONDS.labels(endpoint=endpoint).observe(duration)
    REQUESTS_TOTAL.labels(method="GET", endpoint=endpoint, status="200").inc()

    return {
        "status": "success",
        "message": "Processed business request successfully",
        "latency_ms": round(duration * 1000, 2),
    }


@app.get("/metrics", summary="Prometheus Metrics Scrape Endpoint")
def metrics():
    """Returns standard Prometheus formatted metrics."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


# Fault Injection API (strictly restricted)
class FaultInjectionRequest(BaseModel):
    error_rate: float = 0.0
    extra_latency_seconds: float = 0.0
    simulate_dependency_down: bool = False
    cpu_utilization_percent: float = 0.0
    duration_seconds: float = 0.0


@app.post("/fault/inject", summary="Inject controlled faults for testing")
def inject_fault(req: FaultInjectionRequest):
    if not ENABLE_FAULT_INJECTION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fault injection is disabled in this environment. Set ENABLE_FAULT_INJECTION=true for demo/testing.",
        )
    fault_state["error_rate"] = max(0.0, min(1.0, req.error_rate))
    fault_state["extra_latency_seconds"] = max(0.0, req.extra_latency_seconds)
    fault_state["simulate_dependency_down"] = req.simulate_dependency_down
    fault_state["cpu_utilization_percent"] = max(0.0, min(100.0, req.cpu_utilization_percent))
    CPU_UTILIZATION_PERCENT.set(fault_state["cpu_utilization_percent"])
    if req.duration_seconds > 0:
        fault_state["expires_at"] = time.time() + req.duration_seconds
    else:
        fault_state["expires_at"] = 0.0

    return {
        "message": "Fault injected successfully",
        "state": fault_state,
    }


@app.post("/fault/reset", summary="Reset all faults to normal")
def reset_fault():
    if not ENABLE_FAULT_INJECTION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fault injection is disabled in this environment.",
        )
    fault_state["error_rate"] = 0.0
    fault_state["extra_latency_seconds"] = 0.0
    fault_state["simulate_dependency_down"] = False
    fault_state["cpu_utilization_percent"] = 0.0
    fault_state["expires_at"] = 0.0
    CPU_UTILIZATION_PERCENT.set(0.0)
    return {"message": "All faults cleared", "state": fault_state}


@app.get("/fault/status", summary="Get current fault configuration")
def fault_status():
    _check_fault_expiration()
    return {
        "enabled": ENABLE_FAULT_INJECTION,
        "state": fault_state,
    }
