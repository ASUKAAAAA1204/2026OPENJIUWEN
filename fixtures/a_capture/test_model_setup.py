"""模型接入的离线验收：不连接 DeepSeek、不消耗用户额度。"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from assurance_ledger.rail.deepseek_model import EvidenceDeepSeekClient
from assurance_ledger.rail.local_spike import run_live_spike
from assurance_ledger.rail.model_config import DeepSeekSettings, ModelConfigError, load_deepseek_settings


SOURCE = Path(__file__).resolve().parents[2] / "fixtures/rail_spike/market.csv"
TEST_KEY = "sk-offline-test-not-a-real-key"


def test_config_file_bom_environment_override_and_secret_not_in_summary(tmp_path):
    config = tmp_path / ".env"
    config.write_text(f"DEEPSEEK_API_KEY={TEST_KEY}\nDEEPSEEK_MODEL=deepseek-flash\n", encoding="utf-8-sig")
    settings = load_deepseek_settings(config, environ={"DEEPSEEK_MODEL": "deepseek-v4-pro"})
    assert settings.model == "deepseek-v4-pro"
    assert settings.api_key == TEST_KEY
    assert TEST_KEY not in repr(settings)
    assert TEST_KEY not in json.dumps(settings.public_summary())
    assert settings.public_summary()["api_key_configured"] is True


@pytest.mark.parametrize("values", [
    {},
    {"DEEPSEEK_API_KEY": ""},
    {"DEEPSEEK_API_KEY": "your_api_key_here"},
    {"DEEPSEEK_API_KEY": "not a key"},
    {"DEEPSEEK_BASE_URL": "https://example.com"},
    {"DEEPSEEK_MODEL": "unknown-model"},
    {"DEEPSEEK_TIMEOUT_SECONDS": "nan"},
    {"DEEPSEEK_MAX_TOKENS": "999999"},
])
def test_invalid_config_fails_without_exposing_values(tmp_path, values):
    environ = {} if not values else {"DEEPSEEK_API_KEY": TEST_KEY, **values}
    with pytest.raises(ModelConfigError) as captured:
        load_deepseek_settings(tmp_path / "absent.env", environ=environ)
    assert TEST_KEY not in str(captured.value)


def mock_deepseek(monkeypatch, *, usage_mode="complete", status_code=200, wrong_locator=False):
    requests = []

    def respond(request):
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == 1024
        assert body["tools"][0]["function"]["parameters"]["properties"]["company"]["enum"] == ["A"]
        if status_code != 200:
            return httpx.Response(status_code, json={"error": {"message": "offline rejection"}})
        tools = [message for message in body["messages"] if message["role"] == "tool"]
        if not tools:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-csv", "type": "function", "function": {
                    "name": "read_market_csv", "arguments": '{"company":"A"}'}}]}
            finish = "tool_calls"
        else:
            data = json.loads(tools[-1]["content"])
            assert "source_bytes_b64" not in data and "source_text" not in data
            assert data["values"]["pe"] == "12"
            locator = dict(data["locators"]["pe"])
            if wrong_locator:
                locator["row"] = 99
            message = {"role": "assistant", "content": json.dumps({
                "id": "c-001", "statement": "A 公司 PE=12，低于行业均值 20。",
                "locator": locator, "skill_version": "daily-csv-spike@0.1.0"}, ensure_ascii=False)}
            finish = "stop"
        result = {"id": f"completion-{len(requests)}", "object": "chat.completion", "created": 1,
                  "model": "deepseek-v4.1-flash-1017", "choices": [{
                      "index": 0, "message": message, "finish_reason": finish}]}
        if usage_mode != "absent":
            result["usage"] = {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}
            if usage_mode == "partial":
                del result["usage"]["completion_tokens"]
        return httpx.Response(200, json=result)

    def client(_self, timeout=None):
        return AsyncOpenAI(api_key=TEST_KEY, base_url="https://api.deepseek.com", max_retries=0,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False))

    monkeypatch.setattr(EvidenceDeepSeekClient, "_build_async_openai_client", client)
    return requests


def test_sdk_model_transport_and_evidence_pipeline(monkeypatch, tmp_path, caplog):
    requests = mock_deepseek(monkeypatch)
    report = asyncio.run(run_live_spike(SOURCE, tmp_path, settings=DeepSeekSettings(TEST_KEY)))
    assert report["status"] == "passed", report
    assert report["real_model_tokens"] == {"input": 200, "output": 60}
    assert report["mode"] == "live" and report["event_count"] == 3
    assert report["production_cost_cu"] is None
    assert len(requests) == 2
    assert TEST_KEY not in caplog.text
    output = Path(report["output_directory"])
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["extra"]["response"]["usage_metadata"]["model_name"] == "deepseek-v4.1-flash-1017"
    assert all(event["claim_id"] == "c-001" for event in events)
    for file in output.iterdir():
        assert TEST_KEY not in file.read_text(encoding="utf-8")


@pytest.mark.parametrize("usage_mode", ["absent", "partial"])
def test_missing_raw_usage_cannot_pass_as_zero(monkeypatch, tmp_path, usage_mode):
    mock_deepseek(monkeypatch, usage_mode=usage_mode)
    report = asyncio.run(run_live_spike(SOURCE, tmp_path, settings=DeepSeekSettings(TEST_KEY)))
    assert report["status"] == "failed"
    assert report["real_model_verified"] is False
    assert report["real_model_tokens"] is None
    events = [json.loads(line) for line in (Path(report["output_directory"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(event["extra"]["token_status"] == "missing" for event in events if event["kind"] == "model")


@pytest.mark.parametrize("status_code", [401, 402, 429])
def test_http_failure_has_actionable_error_and_no_retry(monkeypatch, tmp_path, status_code):
    requests = mock_deepseek(monkeypatch, status_code=status_code)
    report = asyncio.run(run_live_spike(SOURCE, tmp_path, settings=DeepSeekSettings(TEST_KEY)))
    assert report["status"] == "failed"
    assert report["claim_count"] == 0
    assert report["error"]["http_status"] == status_code
    assert len(requests) == 1
    assert TEST_KEY not in json.dumps(report)


def test_hallucinated_locator_cannot_pass(monkeypatch, tmp_path):
    mock_deepseek(monkeypatch, wrong_locator=True)
    report = asyncio.run(run_live_spike(SOURCE, tmp_path, settings=DeepSeekSettings(TEST_KEY)))
    assert report["status"] == "failed" and report["claim_count"] == 0


def test_missing_source_does_not_call_model(monkeypatch, tmp_path):
    requests = mock_deepseek(monkeypatch)
    report = asyncio.run(run_live_spike(tmp_path / "missing.csv", tmp_path, settings=DeepSeekSettings(TEST_KEY)))
    assert report["status"] == "failed"
    assert requests == []
