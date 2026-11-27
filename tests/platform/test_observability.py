from __future__ import annotations

import json
import logging

import pytest

from assurance_ledger.api.routers.metrics import authorized
from assurance_ledger.observability import otel
from assurance_ledger.observability.logging import JsonFormatter
from assurance_ledger.observability.metrics import Registry


def test_json_log_fields_and_redaction():
    formatter = JsonFormatter()
    record = logging.LogRecord("temisguard.test", logging.INFO, __file__, 1,
                               "http_request", None, None)
    record.request_id = "REQ-1"
    record.run_id = "RUN-1"
    record.latency_ms = 3.25
    record.fields = {"token": "secret-token", "args": {"path": "x"},
                     "status": "ok"}
    text = formatter.format(record)
    payload = json.loads(text)
    assert payload["event"] == "http_request"
    assert payload["component"] == "temisguard"
    assert payload["level"] == "INFO"
    assert payload["ts"].endswith("Z")
    assert payload["request_id"] == "REQ-1"
    assert payload["run_id"] == "RUN-1"
    assert payload["status"] == "ok"
    assert payload["token"] == "***"
    assert payload["args"] == "***"
    assert "secret-token" not in text


def test_metrics_increment_and_render():
    registry = Registry()
    counter = registry.counter("temisguard_worker_restarts_total", "重启")
    counter.inc({"framework": "openJiuwen"})
    counter.inc({"framework": "openJiuwen"})
    histogram = registry.histogram("temisguard_gate_ms", "Gate 耗时")
    histogram.observe(3, {"decision_class": "file_write", "gate": "ASK"})
    histogram.observe(60, {"decision_class": "file_write", "gate": "ASK"})
    gauge = registry.gauge("temisguard_writer_queue_depth", "队列深度")
    gauge.set(2)
    text = registry.render()
    assert ('temisguard_worker_restarts_total{framework="openJiuwen"} 2'
            in text)
    assert ('temisguard_gate_ms_bucket'
            '{decision_class="file_write",gate="ASK",le="5"} 1' in text)
    assert ('temisguard_gate_ms_bucket'
            '{decision_class="file_write",gate="ASK",le="100"} 2' in text)
    assert ('temisguard_gate_ms_bucket'
            '{decision_class="file_write",gate="ASK",le="+Inf"} 2' in text)
    assert 'temisguard_gate_ms_sum{decision_class="file_write",gate="ASK"} 63' in text
    assert 'temisguard_gate_ms_count{decision_class="file_write",gate="ASK"} 2' in text
    assert "temisguard_writer_queue_depth 2" in text
    assert "# TYPE temisguard_gate_ms histogram" in text


def test_metric_labels_are_enum_only():
    registry = Registry()
    counter = registry.counter("temisguard_worker_restarts_total", "重启")
    with pytest.raises(ValueError):
        counter.inc({"framework": "unknown-framework"})
    with pytest.raises(ValueError):
        counter.inc({"run_id": "RUN-1"})


def test_metrics_disabled_by_default(platform):
    response = platform.client.get("/metrics")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "METRICS_DISABLED"


def test_metrics_loopback_and_token_authorization(platform_factory):
    assert authorized("127.0.0.1", None, "token")
    assert not authorized("10.0.0.5", None, "token")
    assert authorized("10.0.0.5", "Bearer token", "token")
    assert not authorized("10.0.0.5", "Bearer wrong", "token")
    assert not authorized("10.0.0.5", "Bearer token", "")
    platform = platform_factory(
        server_overrides={"metrics_enabled": True, "metrics_token": "token"})
    response = platform.client.get("/metrics")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "temisguard_gate_ms" in body
    assert "temisguard_writer_queue_depth" in body
    assert "temisguard_unknown_outcomes_total" in body


def test_otel_off_does_not_change_outputs(platform, monkeypatch):
    monkeypatch.delenv("TEMIS_OTEL_ENABLED", raising=False)
    otel.reset()
    assert otel.enabled({}) is False
    assert otel.init_otel() is None
    with otel.span("gate.evaluate", gate="ALLOW") as current:
        assert current is None
    run_id = platform.create_run("otel-task")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-otel")
    intent = platform.intent(turn_id, "DEC-OTEL", "final_claim",
                             "final_claim", {"claim": "完成"})
    platform.result("DEC-OTEL", intent["attempt_id"])
    platform.finish(turn_id)
    first = platform.client.post(
        f"/api/v27/runs/{run_id}/export",
        json={"request_id": "otel-export-1", "format": "jsonl"},
        headers=platform.fresh_headers("otel-export-1"))
    second = platform.client.post(
        f"/api/v27/runs/{run_id}/export",
        json={"request_id": "otel-export-1", "format": "jsonl"},
        headers=platform.fresh_headers("otel-export-1"))
    assert first.status_code == 200 and second.status_code == 200
    assert first.content == second.content
