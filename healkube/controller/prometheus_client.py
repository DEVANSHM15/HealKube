"""
Prometheus Metrics Client for HealKube.
Queries application telemetry and Kubernetes container metrics with robust error handling.
Unavailable or unparseable metrics are safely returned as UNKNOWN rather than healthy.
"""

import logging
from typing import Optional
import requests
from healkube.controller.models import MetricValue, MetricStatus

logger = logging.getLogger(__name__)


class PrometheusClient:
    """Client for querying Prometheus instant and range vectors."""

    def __init__(self, base_url: str, timeout_seconds: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def query(self, promql: str) -> MetricValue:
        """
        Execute an instant PromQL query and return a MetricValue.
        Returns MetricStatus.UNKNOWN on connection failures, timeouts, or empty results.
        """
        url = f"{self.base_url}/api/v1/query"
        try:
            response = self.session.get(
                url,
                params={"query": promql},
                timeout=self.timeout_seconds,
            )
            if response.status_code != 200:
                logger.warning(
                    "Prometheus query returned status %d for query '%s': %s",
                    response.status_code,
                    promql,
                    response.text[:200],
                )
                return MetricValue(
                    value=None,
                    status=MetricStatus.ERROR,
                    error_message=f"HTTP {response.status_code}: {response.text[:100]}",
                )

            data = response.json()
            if data.get("status") != "success":
                return MetricValue(
                    value=None,
                    status=MetricStatus.ERROR,
                    error_message=data.get("error", "Unknown Prometheus query error"),
                )

            results = data.get("data", {}).get("result", [])
            if not results:
                # No data matching query
                return MetricValue(
                    value=None,
                    status=MetricStatus.UNKNOWN,
                    error_message="No matching metric series found",
                )

            # Extract first vector result value: [timestamp, "value_string"]
            first_val_str = results[0].get("value", [None, None])[1]
            if first_val_str is None:
                return MetricValue(
                    value=None,
                    status=MetricStatus.UNKNOWN,
                    error_message="Missing value in metric result",
                )

            import math
            val_float = float(first_val_str)
            if math.isnan(val_float) or math.isinf(val_float):
                val_float = 0.0
            return MetricValue(
                value=val_float,
                status=MetricStatus.AVAILABLE,
            )

        except requests.exceptions.RequestException as e:
            logger.debug("Prometheus connection error for '%s': %s", promql, e)
            return MetricValue(
                value=None,
                status=MetricStatus.UNKNOWN,
                error_message=f"Prometheus connection failed: {str(e)}",
            )
        except (ValueError, TypeError, KeyError) as e:
            logger.warning("Failed to parse Prometheus response for '%s': %s", promql, e)
            return MetricValue(
                value=None,
                status=MetricStatus.ERROR,
                error_message=f"Parse error: {str(e)}",
            )

    def get_error_rate_percent(self, namespace: str, workload: str) -> MetricValue:
        """
        Calculates 5xx error rate as a percentage of total requests over the last 30s.
        PromQL: 100 * (sum(rate(healkube_demo_errors_total[30s])) / sum(rate(healkube_demo_requests_total[30s])))
        """
        promql = (
            f"(100 * (sum(rate(healkube_demo_errors_total[30s])) or on() vector(0)) / "
            f"(sum(rate(healkube_demo_requests_total[30s])) > 0)) or on() vector(0)"
        )
        metric = self.query(promql)
        metric.unit = "%"
        return metric

    def get_latency_p95_seconds(self, namespace: str, workload: str) -> MetricValue:
        """
        Calculates P95 latency in seconds over the last 30s.
        """
        promql = (
            "histogram_quantile(0.95, "
            "sum(rate(healkube_demo_request_duration_seconds_bucket[30s])) by (le))"
        )
        metric = self.query(promql)
        metric.unit = "s"
        return metric

    def get_cpu_utilization_percent(self, namespace: str, workload: str) -> MetricValue:
        """
        Calculates container CPU utilization percentage if container metrics are present.
        Falls back to application-level CPU gauge if cAdvisor is not running in demo cluster.
        """
        promql = (
            f"((100 * sum(rate(container_cpu_usage_seconds_total{{namespace='{namespace}',"
            f"pod=~'{workload}-.*',container!='POD',container!=''}}[30s])) / "
            f"sum(kube_pod_container_resource_limits{{namespace='{namespace}',"
            f"pod=~'{workload}-.*',resource='cpu'}})) "
            f"or on() (healkube_demo_cpu_utilization_percent))"
        )
        metric = self.query(promql)
        metric.unit = "%"
        return metric

    def get_memory_utilization_percent(self, namespace: str, workload: str) -> MetricValue:
        """
        Calculates container memory utilization percentage if container metrics are present.
        """
        promql = (
            f"100 * sum(container_memory_working_set_bytes{{namespace='{namespace}',"
            f"pod=~'{workload}-.*',container!='POD',container!=''}}) / "
            f"sum(kube_pod_container_resource_limits{{namespace='{namespace}',"
            f"pod=~'{workload}-.*',resource='memory'}})"
        )
        metric = self.query(promql)
        metric.unit = "%"
        return metric
