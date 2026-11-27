"""框架 worker 通用入口

只通过内部 HTTP 调用中央；永不打开 SQLite。适配器不可用时显式退出 3，
不注册虚假能力。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import signal

from ..adapters.base import AdapterUnavailable, Denied, TurnRequest, load_adapter
from .protocol import CentralClient, CentralError, WorkerHooks, load_settings

HEARTBEAT_SECONDS = 5
CLAIM_RETRY_SECONDS = 1


async def _heartbeat(client: CentralClient, worker_id: str,
                     stop: asyncio.Event) -> None:
    failures = 0
    while not stop.is_set():
        try:
            await client.heartbeat(worker_id)
            failures = 0
        except (CentralError, OSError):
            failures += 1
            if failures >= 3:
                stop.set()
                return
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            pass


async def run(framework: str) -> int:
    try:
        adapter = load_adapter(framework)
    except (AdapterUnavailable, ImportError) as error:
        print(json.dumps({"event": "adapter_unavailable", "framework": framework,
                          "reason": str(error)}, ensure_ascii=False))
        return 3
    if adapter is None:
        module = importlib.import_module({
            "openJiuwen": "assurance_ledger.adapters.openjiuwen",
            "LangGraph": "assurance_ledger.adapters.langgraph",
            "OpenAI Agents SDK": "assurance_ledger.adapters.agents",
        }[framework])
        print(json.dumps({"event": "adapter_unavailable", "framework": framework,
                          "reason": getattr(module, "UNAVAILABLE_REASON", "未实现")},
                         ensure_ascii=False))
        return 3
    settings = load_settings()
    client = CentralClient(settings["CENTRAL_URL"],
                           bootstrap_token=settings["WORKER_BOOTSTRAP_TOKEN"])
    stop = asyncio.Event()

    def _stop(*_args) -> None:
        stop.set()

    try:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    except ValueError:
        pass
    try:
        registration = await client.register(
            framework, adapter.version,
            capabilities={"guarded_call": True, "version": "v27"})
        worker_id = registration["worker_id"]
        heartbeat = asyncio.create_task(_heartbeat(client, worker_id, stop))
        while not stop.is_set():
            try:
                turn = await client.claim(worker_id)
            except (CentralError, OSError):
                await asyncio.sleep(CLAIM_RETRY_SECONDS)
                continue
            if not turn:
                continue
            hooks = WorkerHooks(client, turn["turn_id"], turn["run_id"],
                                turn["segment"])
            request = TurnRequest(
                turn_id=turn["turn_id"], run_id=turn["run_id"],
                segment=turn["segment"], task=turn.get("task", ""),
                framework=turn.get("framework", framework),
                workspace_id=turn.get("workspace_id", ""),
                workspace_path=turn.get("workspace_path", ""),
                skill_id=turn.get("skill_id", ""),
                skill_version=turn.get("skill_version", ""),
                policy_version=turn.get("policy_version"),
                whitelist_version=turn.get("whitelist_version"),
                git_allowed_remotes=tuple(turn.get("git_allowed_remotes") or ()),
                git_allowed_branches=tuple(turn.get("git_allowed_branches") or ()),
                command_limits=turn.get("command_limits"))
            try:
                await hooks.turn_started()
                result = await adapter.run_turn(request, hooks)
                await client.finish(turn["turn_id"], result.outcome,
                                    result.run_state, result.message)
            except Denied as error:
                await client.finish(turn["turn_id"], "failed", "open",
                                    f"工具调用被拒绝：{error.reason}")
            except Exception as error:
                await client.finish(turn["turn_id"], "unknown", "open",
                                    f"适配器异常：{type(error).__name__}")
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TemisGuard 框架 worker")
    parser.add_argument("--framework", required=True,
                        choices=["openJiuwen", "LangGraph",
                                 "OpenAI Agents SDK"])
    args = parser.parse_args()
    return asyncio.run(run(args.framework))


if __name__ == "__main__":
    raise SystemExit(main())
