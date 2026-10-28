"""
Tests for Prometheus client and graceful handling of missing/unknown metrics.
"""

from unittest.mock import MagicMock
import requests
from healkube.controller.prometheus_client import PrometheusClient
from healkube.controller.models import MetricStatus


def test_prometheus_query_success(monkeypatch):
    client = PrometheusClient(base_url="http://prometheus:9090")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {}, "value": [1600000000, "12.34"]}
            ],
        },
    }

    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: mock_resp)

    res = client.query("up")
    assert res.status == MetricStatus.AVAILABLE
    assert res.value == 12.34
    assert res.error_message is None


def test_prometheus_query_empty_results(monkeypatch):
    client = PrometheusClient(base_url="http://prometheus:9090")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": []},
    }

    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: mock_resp)

    res = client.query("non_existent_metric")
    assert res.status == MetricStatus.UNKNOWN
    assert res.value is None
    assert "No matching metric series" in (res.error_message or "")


def test_prometheus_query_connection_failure(monkeypatch):
    client = PrometheusClient(base_url="http://invalid-prom:9090")

    def mock_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(client.session, "get", mock_get)

    res = client.query("up")
    # Must be marked UNKNOWN safely rather than crashing or claiming healthy
    assert res.status == MetricStatus.UNKNOWN
    assert res.value is None
    assert "connection failed" in (res.error_message or "").lower()


def test_prometheus_query_server_error(monkeypatch):
    client = PrometheusClient(base_url="http://prometheus:9090")

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = "Service Unavailable"

    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: mock_resp)

    res = client.query("up")
    assert res.status == MetricStatus.ERROR
    assert res.value is None
    assert "503" in (res.error_message or "")
