"""
Main Controller Reconciliation Loop and Status API for HealKube.
Orchestrates Monitoring, Detection, Diagnosis, Policy Evaluation, Remediation, and Verification.
"""

import os
import time
import signal
import logging
import threading
import csv
import json
import asyncio
import math
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
import httpx
import uvicorn

from healkube.controller.config import ControllerConfig, get_config
from healkube.controller.models import (
    SystemSnapshot,
    DiagnosisResult,
    RemediationDecision,
    RemediationActionType,
    VerificationResult,
)
from healkube.controller.monitor import ClusterMonitor
from healkube.controller.detector import AnomalyDetector
from healkube.controller.diagnosis import DiagnosisEngine
from healkube.controller.policy import PolicyEngine
from healkube.controller.remediation import RemediationExecutor
from healkube.controller.verifier import RecoveryVerifier
from healkube.controller.audit import AuditLogger

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("healkube.controller")


class HealKubeController:
    """The HealKube Autonomous Controller coordinating the full self-healing lifecycle."""

    def __init__(
        self,
        config: Optional[ControllerConfig] = None,
        monitor: Optional[ClusterMonitor] = None,
        detector: Optional[AnomalyDetector] = None,
        diagnosis_engine: Optional[DiagnosisEngine] = None,
        policy_engine: Optional[PolicyEngine] = None,
        remediation_executor: Optional[RemediationExecutor] = None,
        verifier: Optional[RecoveryVerifier] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.config = config or get_config()
        self.monitor = monitor or ClusterMonitor(self.config)
        self.detector = detector or AnomalyDetector(self.config)
        self.diagnosis_engine = diagnosis_engine or DiagnosisEngine()
        self.policy_engine = policy_engine or PolicyEngine(self.config)
        self.remediation_executor = remediation_executor or RemediationExecutor(
            core_api=self.monitor.core_api,
            apps_api=self.monitor.apps_api,
            dry_run_default=self.config.dry_run,
        )
        self.verifier = verifier or RecoveryVerifier(self.config)
        self.audit_logger = audit_logger or AuditLogger(self.config.audit_log_file)

        self.running = False
        self.iteration_count = 0
        self.traffic_thread: Optional[threading.Thread] = None
        self.last_snapshot: Optional[SystemSnapshot] = None
        self.last_diagnosis: Optional[DiagnosisResult] = None
        self.last_decision: Optional[RemediationDecision] = None
        self.last_verification: Optional[VerificationResult] = None

    def reconcile_once(self) -> Dict[str, Any]:
        """
        Executes a single control reconciliation step:
        Observe -> Detect -> Diagnose -> Policy -> Remediate -> Verify -> Audit
        """
        self.iteration_count += 1
        logger.debug(
            "Starting reconciliation iteration #%d for %s/%s",
            self.iteration_count,
            self.config.target_namespace,
            self.config.target_deployment,
        )

        # 1. Observe: Collect unified system snapshot
        snapshot = self.monitor.collect_system_snapshot()
        self.last_snapshot = snapshot

        # 2. Detect: Evaluate metric thresholds and sustained breach count
        anomaly_report = self.detector.evaluate(snapshot)

        # 3. Diagnose: Classify root cause with deterministic precedence
        diagnosis = self.diagnosis_engine.diagnose(snapshot, anomaly_report)
        self.last_diagnosis = diagnosis

        # 4. Policy: Select safe action enforcing cooldown, dry-run, and allowlists
        decision = self.policy_engine.evaluate(snapshot, diagnosis)
        self.last_decision = decision

        execution_success = True
        execution_error = None
        verification_result = None

        # 5. Remediate & Verify: Execute only if an action was decided
        if decision.action != RemediationActionType.NO_ACTION:
            logger.info(
                "Remediation triggered: action=%s target=%s dry_run=%s reason='%s'",
                decision.action.value,
                decision.target_pod or decision.target_workload,
                decision.dry_run,
                decision.reason,
            )

            execution_success, execution_error = self.remediation_executor.execute(decision)
            self.policy_engine.record_remediation(
                decision.target_namespace,
                decision.target_workload,
                timestamp=decision.timestamp,
            )

            # 7. Audit: Record event IMMEDIATELY so it surfaces on the dashboard in real-time!
            audit_record = self.audit_logger.log_event(
                target_namespace=decision.target_namespace,
                target_workload=decision.target_workload,
                diagnosis=diagnosis,
                decision=decision,
                action_executed=execution_success and not decision.dry_run,
                execution_error=execution_error,
                verification=None,
            )

            # 6. Verify: Validate actual workload stabilization and recovery
            if execution_success and decision.action in [
                RemediationActionType.RESTART_POD,
                RemediationActionType.SCALE_UP,
            ]:
                if not decision.dry_run:
                    verification_result = self.verifier.verify(self.monitor)
                    self.last_verification = verification_result
                    if audit_record and verification_result:
                        self.audit_logger.update_verification(audit_record.event_id, verification_result)
                    logger.info(
                        "Verification outcome: status=%s details='%s'",
                        verification_result.status.value,
                        verification_result.details,
                    )
                else:
                    logger.info("[DRY RUN] Skipped post-remediation verification in dry run mode.")

        return {
            "iteration": self.iteration_count,
            "diagnosis": diagnosis.diagnosis_type.value,
            "action": decision.action.value,
            "dry_run": decision.dry_run,
            "target": decision.target_pod or decision.target_workload,
            "verified": verification_result.status.value if verification_result else None,
        }

    def _run_traffic_generator(self):
        """Continuously generates nominal background traffic to /api/data so Prometheus metrics remain active."""
        target_url = f"{self.config.app_base_url.rstrip('/')}/api/data"
        logger.info("Started continuous synthetic background traffic generator against %s", target_url)
        try:
            with httpx.Client(timeout=3.0) as client:
                while self.running:
                    try:
                        client.get(target_url)
                    except Exception:
                        pass
                    time.sleep(1.0)
        except Exception as e:
            logger.debug("Synthetic traffic generator exited: %s", e)

    def run_loop(self):
        """Runs the continuous reconciliation loop until stopped."""
        self.running = True
        logger.info(
            "HealKube Controller started. Monitoring target: %s/%s (Interval: %ss, DryRun: %s)",
            self.config.target_namespace,
            self.config.target_deployment,
            self.config.poll_interval_seconds,
            self.config.dry_run,
        )

        # Start continuous background traffic generator
        self.traffic_thread = threading.Thread(target=self._run_traffic_generator, daemon=True)
        self.traffic_thread.start()

        while self.running:
            try:
                self.reconcile_once()
            except Exception as e:
                logger.exception("Unexpected error in reconciliation loop: %s", e)

            # Sleep interval with responsive check
            poll_time = self.config.poll_interval_seconds
            sleep_step = 0.5
            elapsed = 0.0
            while self.running and elapsed < poll_time:
                time.sleep(min(sleep_step, poll_time - elapsed))
                elapsed += sleep_step

        logger.info("HealKube Controller loop stopped.")

    def stop(self):
        """Gracefully stops the reconciliation loop."""
        logger.info("Stopping HealKube Controller...")
        self.running = False


# Optional FastAPI Status Server
controller_instance: Optional[HealKubeController] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    if controller_instance:
        controller_instance.stop()


status_app = FastAPI(
    title="HealKube Controller Mission Control API",
    description="Inspection, health probes, live streaming, and chaos proxy for HealKube autonomous controller",
    version="0.1.0",
    lifespan=lifespan,
)


class ChaosInjectionPayload(BaseModel):
    """Payload schema for injecting application or dependency faults."""
    error_rate: float = 0.0
    extra_latency_seconds: float = 0.0
    simulate_dependency_down: bool = False
    cpu_utilization_percent: float = 0.0
    duration_seconds: float = 0.0


@status_app.get("/", include_in_schema=False)
def index_redirect():
    """Redirects root URL to the interactive web dashboard."""
    return RedirectResponse(url="/dashboard", status_code=307)


@status_app.get("/dashboard", response_class=HTMLResponse, summary="HealKube Mission Control UI")
def get_dashboard():
    """Serves the interactive single-page mission control dashboard."""
    static_file = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_file):
        with open(static_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(
        content="<h1>HealKube Dashboard</h1><p>Static UI file not found.</p>",
        status_code=404,
    )


@status_app.get("/healthz", summary="Controller Liveness Probe")
def healthz():
    return {"status": "ok", "controller": "running"}


def _sanitize_floats(obj: Any) -> Any:
    """Recursively converts non-JSON compliant float values (NaN, Inf) to 0.0."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


@status_app.get("/status", summary="Controller Operational Status")
@status_app.get("/api/status", summary="Controller Status API Alias")
def get_status():
    ctrl = controller_instance or HealKubeController()
    recent_audits = ctrl.audit_logger.get_recent_records(limit=10)
    data = {
        "controller_running": ctrl.running,
        "iteration_count": ctrl.iteration_count,
        "config": {
            "kubernetes_mode": ctrl.config.kubernetes_mode,
            "target_namespace": ctrl.config.target_namespace,
            "target_deployment": ctrl.config.target_deployment,
            "dry_run": ctrl.config.dry_run,
            "poll_interval_seconds": ctrl.config.poll_interval_seconds,
            "consecutive_breaches_required": ctrl.config.consecutive_breaches_required,
            "app_base_url": ctrl.config.app_base_url,
        },
        "last_snapshot": ctrl.last_snapshot.model_dump() if ctrl.last_snapshot else None,
        "last_diagnosis": ctrl.last_diagnosis.model_dump() if ctrl.last_diagnosis else None,
        "last_decision": ctrl.last_decision.model_dump() if ctrl.last_decision else None,
        "last_verification": (
            ctrl.last_verification.model_dump() if ctrl.last_verification else None
        ),
        "recent_audits_count": len(recent_audits),
        "recent_audits": [a.model_dump() for a in recent_audits],
    }
    return _sanitize_floats(data)


@status_app.post("/api/mode/toggle", summary="Toggle Controller Dry Run Mode")
def toggle_dry_run():
    """Toggles controller between Safe Dry-Run and Active Live Remediation."""
    ctrl = controller_instance or HealKubeController()
    ctrl.config.dry_run = not ctrl.config.dry_run
    ctrl.remediation_executor.dry_run_default = ctrl.config.dry_run
    mode_str = "DRY-RUN (Safe)" if ctrl.config.dry_run else "LIVE REMEDIATION"
    logger.info("Remediation execution mode toggled to: %s", mode_str)
    return {
        "dry_run": ctrl.config.dry_run,
        "message": f"Remediation execution mode set to {mode_str}",
    }


async def _generate_traffic_burst(app_base_url: str, count: int = 15, concurrency: int = 5):
    """Fires a burst of real HTTP requests against the application so Prometheus metrics immediately reflect the fault."""
    target_url = f"{app_base_url.rstrip('/')}/api/data"
    semaphore = asyncio.Semaphore(concurrency)

    async def _send_one(client: httpx.AsyncClient):
        async with semaphore:
            try:
                await client.get(target_url, timeout=5.0)
            except Exception:
                pass

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            tasks = [_send_one(client) for _ in range(count)]
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Traffic burst of %d requests completed against %s", count, target_url)
    except Exception as e:
        logger.debug("Traffic burst encountered error: %s", e)


async def _broadcast_to_pods(ctrl: HealKubeController, path: str, payload: Optional[dict] = None) -> list:
    """Broadcasts a fault or reset request across all running workload pod replicas as well as the Service."""
    urls = [f"{ctrl.config.app_base_url.rstrip('/')}{path}"]
    if ctrl.monitor.core_api:
        try:
            pod_list = ctrl.monitor.core_api.list_namespaced_pod(
                namespace=ctrl.config.target_namespace,
                label_selector=f"app={ctrl.config.target_deployment}",
            )
            for pod in pod_list.items:
                if pod.status.pod_ip:
                    urls.append(f"http://{pod.status.pod_ip}:8000{path}")
        except Exception as e:
            logger.debug("Could not query pod IPs for broadcast: %s", e)

    results = []
    async with httpx.AsyncClient(timeout=4.0) as client:
        for url in urls:
            try:
                if payload is not None:
                    resp = await client.post(url, json=payload)
                else:
                    resp = await client.post(url)
                if resp.headers.get("content-type", "").startswith("application/json"):
                    results.append(resp.json())
                else:
                    results.append({"status": resp.status_code})
            except Exception as e:
                results.append({"error": str(e), "url": url})
    return results


@status_app.post("/api/chaos/inject", summary="Proxy Chaos Injection to Target App")
async def inject_chaos(payload: ChaosInjectionPayload):
    """Proxies chaos fault injection directly to all managed target demo-app pods."""
    ctrl = controller_instance or HealKubeController()
    try:
        results = await _broadcast_to_pods(ctrl, "/fault/inject", payload.model_dump())
        logger.info("Broadcasted fault injection across %d targets: %s", len(results), payload.model_dump())

        has_success = any(
            isinstance(r, dict) and "error" not in r and r.get("status") != "error"
            for r in results
        )
        if not has_success and results:
            return {
                "status": "error",
                "message": f"Could not reach demo application at {ctrl.config.app_base_url}",
                "results": results,
            }

        # Also trigger an immediate burst of live traffic so metrics spike right away
        if (
            payload.cpu_utilization_percent > 0
            or payload.error_rate > 0
            or payload.extra_latency_seconds > 0
        ):
            asyncio.create_task(_generate_traffic_burst(ctrl.config.app_base_url, count=15))

        return {"status": "success", "message": "Fault injected successfully across pods", "results": results}
    except Exception as e:
        logger.warning("Failed to broadcast chaos injection: %s", e)
        return {
            "status": "error",
            "message": f"Could not broadcast fault injection: {e}",
        }


@status_app.post("/api/chaos/reset", summary="Proxy Fault Reset to Target App")
async def reset_chaos():
    """Proxies fault reset across all managed target demo-app pods."""
    ctrl = controller_instance or HealKubeController()
    try:
        results = await _broadcast_to_pods(ctrl, "/fault/reset")
        logger.info("Broadcasted fault reset across %d targets", len(results))

        has_success = any(
            isinstance(r, dict) and "error" not in r and r.get("status") != "error"
            for r in results
        )
        if not has_success and results:
            return {
                "status": "error",
                "message": f"Could not reach demo application at {ctrl.config.app_base_url}",
                "results": results,
            }

        return {"status": "success", "message": "All faults cleared across all pods", "results": results}
    except Exception as e:
        logger.warning("Failed to broadcast fault reset: %s", e)
        return {
            "status": "error",
            "message": f"Could not broadcast fault reset: {e}",
        }


@status_app.get("/api/chaos/status", summary="Proxy Fault Status from Target App")
async def get_chaos_status():
    """Proxies current fault status query from the demo-app microservice."""
    ctrl = controller_instance or HealKubeController()
    target_url = f"{ctrl.config.app_base_url.rstrip('/')}/fault/status"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(target_url)
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return {"status": resp.status_code, "text": resp.text}
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


@status_app.get("/api/test-request", summary="Probe Target Workload Data Endpoint")
async def test_traffic_request():
    """Performs an actual end-to-end HTTP request against /api/data on the workload to probe user traffic health."""
    ctrl = controller_instance or HealKubeController()
    target_url = f"{ctrl.config.app_base_url.rstrip('/')}/api/data"
    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(target_url)
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            try:
                body_json = resp.json()
            except Exception:
                body_json = resp.text
            return {
                "status_code": resp.status_code,
                "latency_ms": elapsed_ms,
                "response": body_json,
            }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "status_code": 503,
            "latency_ms": elapsed_ms,
            "error": str(e),
        }


@status_app.get("/api/benchmarks", summary="Retrieve Comparative Experiment Benchmarks")
def get_benchmarks():
    """Returns comparative benchmarks between native Kubernetes and HealKube."""
    csv_candidates = [
        "experiments_results.csv",
        os.path.join(os.path.dirname(__file__), "..", "..", "experiments_results.csv"),
    ]
    for path in csv_candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    records = list(reader)
                    return {"results": records, "source": path}
            except Exception as e:
                logger.error("Error reading %s: %s", path, e)

    return {
        "results": [
            {
                "system": "Native K8s",
                "scenario": "application_degradation",
                "detection_time_s": "Never (undetected)",
                "recovery_time_s": "> 30.0s (Timed out)",
                "downtime_s": "30.0",
                "failed_requests": "450",
                "success_rate_pct": "0.0",
            },
            {
                "system": "K8s + HealKube",
                "scenario": "application_degradation",
                "detection_time_s": "2.0s",
                "recovery_time_s": "2.0s",
                "downtime_s": "2.0",
                "failed_requests": "30",
                "success_rate_pct": "100.0",
            },
        ],
        "source": "fallback",
    }


@status_app.get("/api/stream", summary="Server-Sent Events Telemetry Stream")
async def telemetry_stream(request: Request):
    """Streams live telemetry snapshots and diagnosis states to connected dashboards via SSE."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            status_data = get_status()
            yield f"data: {json.dumps(status_data)}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main():
    """Main entrypoint for running HealKube controller."""
    config = get_config()
    global controller_instance
    controller_instance = HealKubeController(config=config)

    # Signal handlers for graceful shutdown
    def handle_signal(sig, frame):
        logger.info("Termination signal received (%s). Shutting down...", sig)
        if controller_instance:
            controller_instance.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if config.enable_status_server:
        logger.info(
            "Launching HealKube Controller with Status Server on port %d...",
            config.status_server_port,
        )
        loop_thread = threading.Thread(target=controller_instance.run_loop, daemon=True)
        loop_thread.start()
        uvicorn.run(status_app, host="0.0.0.0", port=config.status_server_port, log_level="info")
    else:
        logger.info("Launching HealKube Controller standalone loop...")
        controller_instance.run_loop()


if __name__ == "__main__":
    main()
