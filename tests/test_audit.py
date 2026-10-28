"""
Tests for AuditLogger structured logging.
"""

import os
import json
from healkube.controller.audit import AuditLogger
from healkube.controller.models import (
    DiagnosisResult,
    DiagnosisType,
    RemediationDecision,
    RemediationActionType,
    VerificationResult,
    VerificationStatus,
)


def test_audit_logging_and_file_persistence(tmp_path):
    log_file = tmp_path / "test_audit.jsonl"
    logger = AuditLogger(str(log_file))

    diagnosis = DiagnosisResult(
        diagnosis_type=DiagnosisType.APPLICATION_DEGRADATION,
        affected_workload="test-app",
        reason="High 5xx error rate",
    )
    decision = RemediationDecision(
        action=RemediationActionType.RESTART_POD,
        target_namespace="test-ns",
        target_workload="test-app",
        target_pod="pod-1",
        reason="Triggered restart",
        dry_run=True,
        timestamp=1000.0,
    )
    verification = VerificationResult(
        status=VerificationStatus.SUCCESS,
        checks_completed=3,
        duration_seconds=15.0,
        details="Stabilized",
    )

    record = logger.log_event(
        target_namespace="test-ns",
        target_workload="test-app",
        diagnosis=diagnosis,
        decision=decision,
        action_executed=False,
        verification=verification,
    )

    assert record.event_id.startswith("evt-")
    assert len(logger.get_recent_records()) == 1

    # Verify JSONL file exists and is parseable
    assert os.path.exists(log_file)
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event_id"] == record.event_id
        assert data["diagnosis"]["diagnosis_type"] == "APPLICATION_DEGRADATION"
        assert data["decision"]["action"] == "RESTART_POD"
