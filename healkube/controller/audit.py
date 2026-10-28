"""
Audit Logger for HealKube.
Records structured JSONL entries for every autonomous healing event,
including diagnosis evidence, policy decision, execution status, and verification outcome.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from healkube.controller.models import (
    AuditRecord,
    DiagnosisResult,
    RemediationDecision,
    VerificationResult,
)

logger = logging.getLogger(__name__)


class AuditLogger:
    """Manages append-only JSONL audit logs for controller activities."""

    def __init__(self, log_filepath: str = "audit_events.jsonl"):
        self.log_filepath = log_filepath
        self.memory_history: List[AuditRecord] = []

    def log_event(
        self,
        target_namespace: str,
        target_workload: str,
        diagnosis: DiagnosisResult,
        decision: RemediationDecision,
        action_executed: bool,
        execution_error: Optional[str] = None,
        verification: Optional[VerificationResult] = None,
    ) -> AuditRecord:
        """Creates, persists, and returns an AuditRecord."""
        record = AuditRecord(
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            target_namespace=target_namespace,
            target_workload=target_workload,
            diagnosis=diagnosis,
            decision=decision,
            action_executed=action_executed,
            execution_error=execution_error,
            verification=verification,
        )

        self.memory_history.append(record)
        if len(self.memory_history) > 500:
            self.memory_history.pop(0)

        try:
            line = record.model_dump_json()
            with open(self.log_filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            logger.info("Audit record logged: %s (%s)", record.event_id, decision.action.value)
        except Exception as e:
            logger.error("Failed to write audit record to %s: %s", self.log_filepath, e)

        return record

    def get_recent_records(self, limit: int = 50) -> List[AuditRecord]:
        """Returns the most recent audit records."""
        return self.memory_history[-limit:]
