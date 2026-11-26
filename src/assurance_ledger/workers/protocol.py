"""worker ↔ 中央内部 HTTP 客户端与 guarded_call 钩子"""
from __future__ import annotations

import os
import secrets
from typing import Any

import httpx

from ..adapters.base import Denied, IntentOutcome, TurnHooks
from ..ledger.canonical import digest_json


class CentralError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class CentralClient:
    def __init__(self, base_url: str, *, token: str | None = None,
                 bootstrap_token: str | None = None,
                 timeout: float = 40.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.bootstrap_token = bootstrap_token
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport,
            timeout=httpx.Timeout(timeout, connect=5.0))

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"X-Worker-Token": self.token}
        return {}

    async def _request(self, method: str, path: str,
                       **kwargs) -> Any:
        response = await self._client.request(method, path,
                                              headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            try:
                body = response.json()
                error = body.get("error", {})
                raise CentralError(response.status_code,
                                   error.get("code", "INTERNAL_ERROR"),
                                   error.get("message", response.text))
            except ValueError as error:
                raise CentralError(response.status_code, "INTERNAL_ERROR",
                                   response.text) from error
        if response.status_code == 204:
            return None
        return response.json()

    async def register(self, framework: str, version: str,
                       capabilities: dict[str, Any] | None = None) -> dict:
        headers = {}
        if self.bootstrap_token:
            headers["X-Worker-Bootstrap-Token"] = self.bootstrap_token
        response = await self._client.post(
            "/internal/v1/workers/register",
            json={"framework": framework, "version": version,
                  "pid": os.getpid(), "capabilities": capabilities or {}},
            headers=headers)
        if response.status_code >= 400:
            raise CentralError(response.status_code, "REGISTER_FAILED",
                               response.text)
        data = response.json()
        self.token = data["token"]
        return data

    async def heartbeat(self, worker_id: str) -> dict:
        return await self._request(
            "POST", f"/internal/v1/workers/{worker_id}/heartbeat",
            json={"worker_id": worker_id})

    async def claim(self, worker_id: str) -> dict | None:
        data = await self._request(
            "POST", "/internal/v1/turns/claim",
            json={"worker_id": worker_id})
        return data.get("turn") if isinstance(data, dict) and "turn" in data else data

    async def event(self, turn_id: str, client_event_id: str,
                    event_type: str, payload: dict[str, Any]) -> dict:
        return await self._request(
            "POST", f"/internal/v1/turns/{turn_id}/events",
            json={"client_event_id": client_event_id, "type": event_type,
                  "payload": payload})

    async def finish(self, turn_id: str, outcome: str,
                     run_state: str = "open", message: str = "") -> dict:
        return await self._request(
            "POST", f"/internal/v1/turns/{turn_id}/finish",
            json={"outcome": outcome, "run_state": run_state,
                  "message": message})

    async def intent(self, body: dict[str, Any]) -> dict:
        return await self._request("POST", "/internal/v1/decisions/intent",
                                   json=body)

    async def result(self, decision_id: str, body: dict[str, Any]) -> dict:
        return await self._request(
            "POST", f"/internal/v1/decisions/{decision_id}/result", json=body)

    async def wait_approval(self, approval_id: str, timeout: int = 25) -> dict:
        return await self._request(
            "GET", f"/internal/v1/approvals/{approval_id}/wait",
            params={"timeout": timeout})

    async def policies(self) -> dict:
        return await self._request("GET", "/internal/v1/policies/current")


class WorkerHooks(TurnHooks):
    def __init__(self, client: CentralClient, turn_id: str,
                 run_id: str, segment: int):
        self.client = client
        self.turn_id = turn_id
        self.run_id = run_id
        self.segment = segment
        self.turn_started_sent = False

    async def turn_started(self) -> None:
        if self.turn_started_sent:
            return
        await self.client.event(
            self.turn_id, f"started:{self.turn_id}", "turn_started",
            {"turn_id": self.turn_id, "run_id": self.run_id,
             "segment": self.segment})
        self.turn_started_sent = True

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        body = dict(payload)
        body.setdefault("turn_id", self.turn_id)
        await self.client.event(
            self.turn_id,
            payload.get("client_event_id") or f"{event_type}:{secrets.token_hex(8)}",
            event_type, body)

    async def guarded_call(self, tool_name: str, args: dict[str, Any],
                           decision_class: str,
                           decision_id: str | None = None) -> IntentOutcome:
        decision = decision_id or "DEC-" + secrets.token_hex(8).upper()
        response = await self.client.intent({
            "turn_id": self.turn_id, "decision_id": decision,
            "decision_class": decision_class, "tool_name": tool_name,
            "args": args, "side_effect": True,
            "summary": f"{decision_class}/{tool_name}"})
        gate = response.get("gate")
        digest = digest_json(args)
        attempt = response.get("attempt_id")
        if response.get("decision_id") != decision or response.get("args_digest") != digest or not attempt:
            raise CentralError(409, "INTENT_BINDING", "意图回执与本次工具调用不一致")
        if response.get("replayed"):
            raise Denied(decision, "已有决策回执，请核对原执行结果")
        approval_id = response.get("approval_id")
        effective = args
        if gate == "ASK" and approval_id:
            while True:
                result = await self.client.wait_approval(approval_id)
                approval = result.get("approval") or {}
                if (result.get("decision_id") != decision or result.get("args_digest") != digest
                        or approval.get("approval_id") != approval_id or approval.get("run_id") != self.run_id
                        or approval.get("decision_id") != decision or approval.get("args_digest") != digest):
                    raise CentralError(409, "APPROVAL_BINDING", "审批回执归属或参数不匹配")
                if result.get("status") != "pending":
                    break
            answer = approval.get("response") or {}
            if (result.get("status") != "approved" or result.get("execute") is not True
                    or answer.get("approve_result") != "approve" or answer.get("approved_args_digest") != digest):
                raise Denied(decision, "审批未批准或已过期")
        elif response.get("execute") is True and gate in ("ALLOW", "REWRITE", "NONE"):
            if gate == "REWRITE":
                effective = response.get("args")
                if (response.get("final_gate") != "ALLOW" or not isinstance(effective, dict)
                        or digest_json(effective) != response.get("args_after")):
                    raise CentralError(409, "REWRITE_BINDING", "改写参数与复判回执不一致")
            elif response.get("final_gate") != gate:
                raise CentralError(409, "INTENT_BINDING", "最终判定与原始判定不一致")
        else:
            raise Denied(decision, response.get("gate_reason") or "Gate 拒绝执行")
        return IntentOutcome(decision, attempt, gate, True, effective, approval_id, digest)

    async def report_result(self, decision_id: str, attempt_id: str,
                            status: str, summary: str,
                            evidence: dict[str, Any] | None = None,
                            output: Any = None) -> dict:
        return await self.client.result(decision_id, {
            "attempt_id": attempt_id, "status": status, "summary": summary,
            "evidence": evidence, "output": output})

    def check_cancel(self) -> bool:
        return False


def load_settings() -> dict[str, str]:
    required = ("CENTRAL_URL", "WORKER_BOOTSTRAP_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"缺少环境变量：{', '.join(missing)}")
    return {name: os.environ[name] for name in os.environ
            if name.startswith(("MODEL_", "CENTRAL_", "WORKER_", "RUN_"))}


__all__ = ["CentralClient", "CentralError", "WorkerHooks", "load_settings"]
