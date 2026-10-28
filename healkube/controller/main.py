"""
Main Controller Reconciliation Loop and Status API for HealKube.
Orchestrates Monitoring, Detection, Diagnosis, Policy Evaluation, Remediation, and Verification.
"""

import os
import time
import signal
import logging
import threading
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI
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

            # 6. Verify: Validate actual workload stabilization and recovery
            if execution_success and decision.action in [
                RemediationActionType.RESTART_POD,
                RemediationActionType.SCALE_UP,
            ]:
                if not decision.dry_run:
                    verification_result = self.verifier.verify(self.monitor)
                    self.last_verification = verification_result
                    logger.info(
                        "Verification outcome: status=%s details='%s'",
                        verification_result.status.value,
                        verification_result.details,
                    )
                else:
                    logger.info("[DRY RUN] Skipped post-remediation verification in dry run mode.")

            # 7. Audit: Record full event trail
            self.audit_logger.log_event(
                target_namespace=decision.target_namespace,
                target_workload=decision.target_workload,
                diagnosis=diagnosis,
                decision=decision,
                action_executed=execution_success and not decision.dry_run,
                execution_error=execution_error,
                verification=verification_result,
            )

        return {
            "iteration": self.iteration_count,
            "diagnosis": diagnosis.diagnosis_type.value,
            "action": decision.action.value,
            "dry_run": decision.dry_run,
            "target": decision.target_pod or decision.target_workload,
            "verified": verification_result.status.value if verification_result else None,
        }

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
    # Startup: Start controller background thread if running via uvicorn
    global controller_instance
    if controller_instance is None:
        controller_instance = HealKubeController()
        loop_thread = threading.Thread(target=controller_instance.run_loop, daemon=True)
        loop_thread.start()
    yield
    # Shutdown
    if controller_instance:
        controller_instance.stop()


status_app = FastAPI(
    title="HealKube Controller Status API",
    description="Inspection and health probes for HealKube autonomous controller",
    version="0.1.0",
    lifespan=lifespan,
)


@status_app.get("/healthz", summary="Controller Liveness Probe")
def healthz():
    return {"status": "ok", "controller": "running"}


@status_app.get("/status", summary="Controller Operational Status")
def get_status():
    ctrl = controller_instance or HealKubeController()
    recent_audits = ctrl.audit_logger.get_recent_records(limit=10)
    return {
        "controller_running": ctrl.running,
        "iteration_count": ctrl.iteration_count,
        "config": {
            "kubernetes_mode": ctrl.config.kubernetes_mode,
            "target_namespace": ctrl.config.target_namespace,
            "target_deployment": ctrl.config.target_deployment,
            "dry_run": ctrl.config.dry_run,
            "poll_interval_seconds": ctrl.config.poll_interval_seconds,
            "consecutive_breaches_required": ctrl.config.consecutive_breaches_required,
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
        uvicorn.run(status_app, host="0.0.0.0", port=config.status_server_port, log_level="info")
    else:
        logger.info("Launching HealKube Controller standalone loop...")
        controller_instance.run_loop()


if __name__ == "__main__":
    main()
