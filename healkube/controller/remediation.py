"""
Remediation Executor for HealKube.
Safely executes actions (pod replacement, horizontal scaling) via Kubernetes API
with strict dry-run enforcement and comprehensive error handling.
"""

import logging
from typing import Optional, Tuple
from healkube.controller.models import RemediationDecision, RemediationActionType

logger = logging.getLogger(__name__)


class RemediationExecutor:
    """Executes remediation decisions on the Kubernetes cluster."""

    def __init__(self, core_api=None, apps_api=None):
        self.core_api = core_api
        self.apps_api = apps_api

    def execute(self, decision: RemediationDecision) -> Tuple[bool, Optional[str]]:
        """
        Executes the specified remediation decision.
        Returns: (success: bool, error_message: Optional[str])
        """
        ns = decision.target_namespace
        workload = decision.target_workload

        if decision.action == RemediationActionType.NO_ACTION:
            logger.debug("Remediation: NO_ACTION for %s/%s (%s)", ns, workload, decision.reason)
            return True, None

        if decision.action in [
            RemediationActionType.REPORT_DEPENDENCY_ISSUE,
            RemediationActionType.REPORT_NODE_ISSUE,
        ]:
            logger.warning("Remediation Notice [%s]: %s", decision.action.value, decision.reason)
            return True, None

        # Check Dry Run
        if decision.dry_run:
            logger.info(
                "[DRY RUN] Would execute %s for %s/%s. Reason: %s",
                decision.action.value,
                ns,
                workload,
                decision.reason,
            )
            return True, None

        # Live Execution: Pod Replacement
        if decision.action == RemediationActionType.RESTART_POD:
            pod_name = decision.target_pod
            if not pod_name:
                err = f"Cannot execute RESTART_POD: No target pod specified for {ns}/{workload}"
                logger.error(err)
                return False, err

            logger.warning(
                "EXECUTING REMEDIATION: Deleting pod %s in namespace %s for deployment %s",
                pod_name,
                ns,
                workload,
            )
            if not self.core_api:
                logger.info("Mock API: Simulated deletion of pod %s", pod_name)
                return True, None

            try:
                self.core_api.delete_namespaced_pod(
                    name=pod_name,
                    namespace=ns,
                    grace_period_seconds=10,
                )
                logger.info("Successfully deleted pod %s in namespace %s", pod_name, ns)
                return True, None
            except Exception as e:
                err = f"Failed to delete pod {pod_name} in {ns}: {str(e)}"
                logger.error(err)
                return False, err

        # Live Execution: Deployment Scaling
        if decision.action == RemediationActionType.SCALE_UP:
            target_replicas = decision.target_replicas
            if target_replicas is None:
                err = f"Cannot execute SCALE_UP: target_replicas not specified for {ns}/{workload}"
                logger.error(err)
                return False, err

            logger.warning(
                "EXECUTING REMEDIATION: Scaling deployment %s in namespace %s to %d replicas",
                workload,
                ns,
                target_replicas,
            )
            if not self.apps_api:
                logger.info(
                    "Mock API: Simulated scaling deployment %s to %d replicas",
                    workload,
                    target_replicas,
                )
                return True, None

            try:
                # Patch deployment scale subresource
                patch_body = {"spec": {"replicas": target_replicas}}
                self.apps_api.patch_namespaced_deployment_scale(
                    name=workload,
                    namespace=ns,
                    body=patch_body,
                )
                logger.info(
                    "Successfully scaled deployment %s in %s to %d replicas",
                    workload,
                    ns,
                    target_replicas,
                )
                return True, None
            except Exception as e:
                err = f"Failed to scale deployment {workload} in {ns}: {str(e)}"
                logger.error(err)
                return False, err

        err = f"Unknown remediation action type: {decision.action}"
        logger.error(err)
        return False, err
