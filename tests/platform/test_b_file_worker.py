"""Regression checks for binding real SDK calls to one central execution permission."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from assurance_ledger.adapters.base import Denied
from assurance_ledger.adapters.file_turn import ModelTurnError, parse_step
from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.workers.protocol import CentralError, WorkerHooks


def test_model_requires_exactly_one_registered_file_call():
    action = SimpleNamespace(type="function", id="call_x", function=SimpleNamespace(
        name="write_file", arguments='{"path":"memo.txt","content":"ok"}'))
    message = SimpleNamespace(content=None, tool_calls=[action])
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    reply = SimpleNamespace(choices=[choice], model="fixture")
    assert parse_step(reply).arguments == {"path": "memo.txt", "content": "ok"}
    message.tool_calls.append(action)
    with pytest.raises(ModelTurnError):
        parse_step(reply)
    message.tool_calls.pop()
    action.function.arguments = '{"path":"one","path":"two","content":"ok"}'
    with pytest.raises(ModelTurnError):
        parse_step(reply)


def test_ask_continues_after_an_empty_poll_and_binds_the_approved_arguments():
    args = {"path": "memo.txt", "content": "hello"}
    digest = digest_json(args)
    intent = {"gate": "ASK", "decision_id": "d", "attempt_id": "a", "args_digest": digest, "approval_id": "p"}
    item = {"approval_id": "p", "decision_id": "d", "run_id": "r", "args_digest": digest}
    pending = {"status": "pending", "decision_id": "d", "args_digest": digest, "approval": item, "execute": False}
    approved = {**pending, "status": "approved", "execute": True,
                "approval": {**item, "response": {"approve_result": "approve", "approved_args_digest": digest}}}
    client = SimpleNamespace(intent=AsyncMock(return_value=intent),
                             wait_approval=AsyncMock(side_effect=[pending, approved]))
    hooks = WorkerHooks(client, "turn", "r", 1)
    outcome = asyncio.run(hooks.guarded_call("write_file", args, "file_write", decision_id="d"))
    assert (outcome.execute, outcome.args, outcome.attempt_id, client.wait_approval.await_count) == (True, args, "a", 2)
    client.wait_approval = AsyncMock(return_value={**approved, "approval": {**approved["approval"], "run_id": "other"}})
    with pytest.raises(CentralError, match="审批回执"):
        asyncio.run(hooks.guarded_call("write_file", args, "file_write", decision_id="d"))


def test_rewrite_uses_verified_effective_arguments_and_replay_does_not_execute():
    args, after = {"path": "before"}, {"path": "after"}
    response = {"gate": "REWRITE", "final_gate": "ALLOW", "execute": True, "attempt_id": "a",
                "decision_id": "d", "args_digest": digest_json(args), "args_after": digest_json(after), "args": after}
    client = SimpleNamespace(intent=AsyncMock(return_value=response))
    hooks = WorkerHooks(client, "turn", "r", 1)
    call = lambda: asyncio.run(hooks.guarded_call("write_file", args, "file_write", decision_id="d"))
    assert call().args == after
    response["args"] = args
    with pytest.raises(CentralError, match="改写参数"):
        call()
    response["replayed"] = True
    with pytest.raises(Denied, match="已有决策"):
        call()


def test_repeating_a_persisted_intent_only_returns_a_receipt(platform):
    run = platform.create_run("b-intent-once")
    turn_id, _ = platform.begin(run["snapshot"]["run"]["id"])
    args = (turn_id, "DEC-ONCE", "final_claim", "final_claim", {"claim": "synthetic"})
    first = platform.intent(*args)
    retry = platform.intent(*args)
    assert first["execute"] and not first["replayed"]
    assert retry["replayed"] and not retry["execute"]
    assert retry["attempt_id"] == first["attempt_id"]


def test_repeating_pending_approval_keeps_one_request(platform):
    run_id = platform.create_run("b-pending-once")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id)
    call = (turn_id, "DEC-PENDING", "file_write", "write_file", {"path": "memo", "content": "ok"})
    initial = platform.intent(*call)
    again = platform.intent(*call)
    assert not initial["execute"] and not again["execute"] and again["replayed"]
    assert initial["approval_id"] == again["approval_id"]
    assert len(platform.client.get("/api/v27/approvals").json()) == 1
