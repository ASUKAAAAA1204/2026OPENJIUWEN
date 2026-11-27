"""本机实测：账本事务、意图/结果落账与纯规则 Gate 延迟

不作为生产 SLA。--mode intent/result 走真实 record_intent / record_result，
不用原始事件追加冒充意图事务。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from assurance_ledger.api.app import create_app  # noqa: E402
from assurance_ledger.auth.users import create_user  # noqa: E402
from assurance_ledger.config import (  # noqa: E402
    PlatformConfig,
    SecurityConfig,
    ServerConfig,
    SkillConfig,
    StorageConfig,
    WorkspaceConfig,
)
from assurance_ledger.gate.engine import evaluate  # noqa: E402
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore  # noqa: E402
from assurance_ledger.ledger.writer import read_connection  # noqa: E402
from assurance_ledger.policy.table import PolicyTable, default_configuration  # noqa: E402
from assurance_ledger.runs import decisions, execution, lifecycle, turns  # noqa: E402
from assurance_ledger.workers.registry import (  # noqa: E402
    OPENJIUWEN_LOCK,
    register_worker,
)

MODES = ("all", "gate", "intent", "result")


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


def _summary(values: list[float], digits: int = 3) -> dict:
    if not values:
        return {"samples": 0, "p50": None, "p95": None, "max": None}
    return {"samples": len(values),
            "p50": round(statistics.median(values), digits),
            "p95": round(p95(values), digits),
            "max": round(max(values), digits)}


def _config(base: Path) -> PlatformConfig:
    return PlatformConfig(
        root=base,
        server=ServerConfig(public_origin="http://127.0.0.1",
                            shutdown_grace_seconds=0),
        storage=StorageConfig(
            database=base / "data/platform.db", outputs=base / "outputs",
            keys=base / "data/keys", backups=base / "data/backups",
            snapshots=base / "data/snapshots"),
        security=SecurityConfig(allowed_root=base / "workspaces",
                                session_secret="s" * 32,
                                worker_bootstrap_token="bench-token"),
        workspaces=(WorkspaceConfig("workspace", base / "workspaces/workspace"),),
        skills=(SkillConfig("code-assistant", "1.0.0"),),
    )


def _prepare(state, config):
    create_user(state.writer, username="bench", password="bench-password-123",
                display_name="Bench", role="platform_admin")
    worker = state.writer.call(lambda db: register_worker(
        db, config, framework="openJiuwen", version=OPENJIUWEN_LOCK, pid=None))
    with read_connection(config.database) as db:
        admin = dict(db.execute("SELECT * FROM users WHERE username='bench'")
                     .fetchone())
        context = lifecycle.run_context(db, config, admin)
    created = lifecycle.create_run(
        state.writer, state.store, config, user=admin,
        payload={"task_id": "bench-task", "title": "bench",
                 "workspace_id": "workspace", "framework": "openJiuwen",
                 "skill_id": "code-assistant", "skill_version": "1.0.0",
                 "expected_context_revision": context["revision"],
                 "request_id": "bench-create"},
        request_id="bench-create")
    run_id = created["snapshot"]["run"]["id"]
    with read_connection(config.database) as db:
        run = lifecycle.get_run(db, run_id)
        revision = execution.execution_state(db, run, 1)["revision"]
    execution.start_turn(
        state.writer, config, user=admin, run_id=run_id,
        payload={"expected_segment": 1, "expected_execution_revision": revision},
        request_id="bench-exec")
    claimed = execution.claim_turn(state.writer, config, worker=worker)
    turn_id = claimed["turn_id"]
    state.writer.call(lambda db: turns.append_turn_event(
        db, turn_id=turn_id, client_event_id=f"started:{turn_id}",
        event_type="turn_started",
        payload={"turn_id": turn_id, "run_id": run_id, "segment": 1}))
    return admin, worker, run_id, turn_id


def _intent(state, config, worker, turn_id: str, index: int,
            timings: list[float], results: list[float]) -> None:
    decision_id = f"DEC-BENCH-{index:06d}"
    started = time.perf_counter_ns()
    response = decisions.record_intent(
        state.writer, state.store, state.journal, state.record_store, config,
        worker=worker,
        payload={"turn_id": turn_id, "decision_id": decision_id,
                 "decision_class": "final_claim", "tool_name": "final_claim",
                 "args": {"claim": f"bench-{index}"}, "side_effect": True,
                 "summary": "bench"})
    timings.append((time.perf_counter_ns() - started) / 1e6)
    if results is None:
        return
    attempt_id = response.get("attempt_id")
    if not attempt_id:
        return
    started = time.perf_counter_ns()
    decisions.record_result(
        state.writer, state.store, state.journal, state.record_store, config,
        worker=worker, decision_id=decision_id,
        payload={"attempt_id": attempt_id, "status": "EXEC_FINISHED",
                 "summary": "bench", "evidence": {
                     "model_id": "bench", "model_params": "t=0",
                     "verdict": "completed"}, "output": {"ok": True}})
    results.append((time.perf_counter_ns() - started) / 1e6)


def main() -> int:
    parser = argparse.ArgumentParser(description="平台延迟本机实测")
    parser.add_argument("--mode", choices=MODES, default="all")
    parser.add_argument("--writes", type=int, default=200)
    parser.add_argument("--gates", type=int, default=2000)
    parser.add_argument("--samples", type=int, default=100,
                        help="intent/result 模式样本数")
    parser.add_argument("--output",
                        default=str(ROOT / "outputs/bench_platform.json"))
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="temis-bench-"))
    config = _config(base)
    config.prepare_directories()
    report: dict = {
        "mode": args.mode,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count() or 0,
            "note": "本机顺序写入，单写者；不代表生产 Linux/容器指标",
        },
    }
    app = create_app(config)
    with TestClient(app, client=("127.0.0.1", 50000)):
        state = app.state.platform
        if args.mode in ("all", "intent", "result"):
            try:
                admin, worker, run_id, turn_id = _prepare(state, config)
                intent_ms: list[float] = []
                result_ms: list[float] = []
                for index in range(args.samples):
                    _intent(state, config, worker, turn_id, index, intent_ms,
                            result_ms if args.mode in ("all", "result") else None)
                report["intent_persist_ms"] = _summary(intent_ms)
                if args.mode in ("all", "result"):
                    report["result_persist_ms"] = _summary(result_ms)
            except Exception as error:  # 证据不足时显式 not_run，不伪造数字
                report["intent_persist_ms"] = {
                    "samples": 0, "p50": None, "p95": None, "max": None,
                    "status": "not_run",
                    "reason": f"{type(error).__name__}: {error}"}
        if args.mode in ("all", "gate"):
            policy = PolicyTable(
                version="bench", description="bench",
                published_at="2026-01-01T00:00:00Z", published_by="bench",
                eligible=True, reason="bench", configuration=default_configuration())
            gates = []
            workspace = config.workspaces[0].path
            for _ in range(args.gates):
                started = time.perf_counter_ns()
                evaluate(policy, decision_class="file_write",
                         tool_name="write_file",
                         args={"path": "notes.txt", "content": "x"},
                         workspace=workspace)
                gates.append((time.perf_counter_ns() - started) / 1e6)
            report["gate_ms"] = _summary(gates, digits=4)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
