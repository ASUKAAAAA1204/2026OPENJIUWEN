"""B's M1 consumer check against the published SQLite interfaces and a real registered SDK tool."""
import argparse
import asyncio
import hashlib
import json
import subprocess
from contextlib import chdir
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from assurance_ledger.contracts.transitions import TransitionError
from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.ledger.snapshots import SnapshotError, SnapshotStore
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.task_registry import TaskRegistry

from adapters import REQUESTED, TOOL
from controlled import ACTIVE_RUN, APPROVED, BusinessRun, WriteTrial
from lifecycle import invoke
from run import ROOT


class RecheckRejected(ValueError):
    """The proposed execution argument differs from the rechecked argument."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def register(registry, run):
    event_id = run["run_id"] + ":created"
    args = (event_id, run["task_id"], run["run_id"], run["parent_run_id"])
    first = registry.create(*args)
    require(registry.create(*args) == first, "register retry changed the event")
    check_run(registry, run)


def check_run(registry, expected):
    stored = registry.read(expected["run_id"])
    require(stored and all(stored[k] == expected[k] for k in ("task_id", "run_id", "segment", "parent_run_id")),
            "stored identity differs from SDK observation")


def replay_identities(matrix, store):
    require(matrix["format"] == "b-m0-control-points-v3" and matrix["preflight_passed"]
            and matrix["executions_passed"] == 108, "supply the complete passing S01–S12 matrix")
    registry = TaskRegistry(store)
    counts = {"concurrent_tasks": 0, "resumes": 0, "parent_child_pairs": 0}
    combinations = set()
    for engine in matrix["frameworks"]:
        for row in engine["trials"]:
            case = row["scenario"]
            if case not in ("S09", "S10", "S11"):
                continue
            identity = (engine["framework"], row["repeat"], case)
            require(identity not in combinations and row["passed"], "invalid or duplicate SDK trial")
            combinations.add(identity)
            if case == "S09":
                require(len(row["observations"]) == row["peak_in_tool"] == 10, "ten concurrent tasks required")
                for observation in row["observations"]:
                    run = observation["expected_run"]
                    require(observation["contexts"] == [{**run, "target": observation["requested"][0],
                                                         "call_id": observation["call_id"]}], "mixed task context")
                    register(registry, run)
                    counts["concurrent_tasks"] += 1
            elif case == "S10":
                pause, = row["paused"]
                require(pause["native_id"] and pause["sdk_call_id"] == row["call_id"], "missing SDK pause binding")
                run = pause["run"]
                register(registry, run)
                registry.resume(run["run_id"] + ":resume", run["run_id"], run["segment"])
                check_run(registry, row["resumed_run"])
                require(run["run_id"] == row["resumed_run"]["run_id"], "resume changed the run")
                counts["resumes"] += 1
            else:
                require(row["child"]["parent_run_id"] == row["parent"]["run_id"]
                        and row["child"]["run_id"] != row["parent"]["run_id"], "invalid child relation")
                register(registry, row["parent"])
                register(registry, row["child"])
                counts["parent_child_pairs"] += 1
    expected = {(f, n, s) for f in ("openjiuwen", "langgraph", "agents") for n in (1, 2, 3)
                for s in ("S09", "S10", "S11")}
    require(combinations == expected and len(store.read_all()) == 126, "unexpected identity rows or registry events")
    return {**counts, "scenarios_replayed": len(combinations), "registry_events_after_idempotent_retries": 126}


async def journal_case(name, directory, store):
    directory.mkdir()
    run = BusinessRun()
    TaskRegistry(store).create(run.run_id + ":created", run.task_id, run.run_id)
    trial = WriteTrial(directory, "ALLOW")
    decision_id = "decision-" + uuid4().hex
    journal = DecisionJournal(store, Redactor("probe-v1"))
    snapshot = SnapshotStore(store).capture({"tool": TOOL, "allowed_target": APPROVED}, journal.redactor)
    actual = APPROVED if name == "rewrite" else REQUESTED
    original_digest = digest_json({"target": REQUESTED})
    actual_digest = digest_json({"target": actual})
    events_before_effect = []
    expected_errors = {"missing_snapshot": SnapshotError, "stale_segment": TransitionError,
                       "deny": TransitionError, "rewrite_mismatch": RecheckRejected}
    blocked_by = None

    def history():
        # Each read opens another SQLite connection, including the read from inside the actual tool.
        return [r["body"]["event"] for r in store.read_all() if r["body"].get("decision_id") == decision_id]

    def write():
        events_before_effect.extend(e["type"] for e in history())
        return trial.write_file(actual)

    async def boundary(target):
        nonlocal blocked_by
        owner = ACTIVE_RUN.get()
        require(owner is run and target == REQUESTED, "SDK callback did not supply the bound run/argument")
        gate = {"rewrite": "REWRITE", "rewrite_mismatch": "REWRITE", "deny": "DENY"}.get(name, "ALLOW")
        intent = {"decision_id": decision_id, "run_id": owner.run_id, "segment": owner.segment,
                  "class": "file_write", "tool_name": TOOL, "args_digest": original_digest,
                  "args_after": digest_json({"target": APPROVED}) if gate == "REWRITE" else None,
                  "whitelist_version": "probe-v1", "policy_version": "probe-v1", "rule_snapshot_ref": snapshot,
                  "gate": gate, "gate_reason": "controlled test", "gate_latency_ms": 0, "exec_policy": "normal",
                  "status": {"ALLOW": "ALLOWED", "REWRITE": "REWRITTEN", "DENY": "DENIED"}[gate]}
        if name == "missing_snapshot":
            intent["rule_snapshot_ref"] = "sha256:" + "0" * 64
        if name == "stale_segment":
            TaskRegistry(store).resume(run.run_id + ":resumed", run.run_id, 1)
        try:
            journal.record_intent(trial.call_id + ":intent", intent, recorded_at=datetime.now(timezone.utc).isoformat())
            if gate == "REWRITE":
                rule = SnapshotStore(store).read(snapshot)["value"]
                if not (rule["tool"] == TOOL and rule["allowed_target"] == actual and intent["args_after"] == actual_digest):
                    raise RecheckRejected("rewritten arguments failed recheck")
                journal.append(trial.call_id + ":recheck", decision_id,
                               {"type": "REWRITE_VALIDATED", "gate": "ALLOW", "args_digest": actual_digest})
            return journal.execute_once(decision_id, trial.call_id, write, model_id="scripted-probe")
        except (SnapshotError, TransitionError, RecheckRejected) as error:
            if type(error) is not expected_errors.get(name):
                raise
            blocked_by = type(error).__name__
            return f"blocked:{trial.call_id}"

    trial.operation = boundary
    replies = await invoke("openjiuwen", trial, run)
    require(len(trial.terminals) == 1 and replies == [(trial.call_id, trial.terminals[0])], "SDK receipt mismatch")
    events = history()
    types = [e["type"] for e in events]
    disk = {p.name: p.read_text(encoding="ascii").splitlines() for p in directory.iterdir()}
    duplicate_rejected = None
    evidence_input_digest = None
    if name in expected_errors:
        require(blocked_by and not disk and not trial.executions and not events_before_effect, "blocked tool executed")
        require(types == (["GATE_DECIDED"] if name in ("deny", "rewrite_mismatch") else []), "unexpected persisted events")
    else:
        before = ["GATE_DECIDED"] + (["REWRITE_VALIDATED"] if name == "rewrite" else []) + ["EXEC_STARTED"]
        require(events_before_effect == before and types == before + ["EXEC_FINISHED"], "incorrect commit order")
        output_ref = events[-1]["evidence"]["output_digest"]
        evidence_input_digest = events[-1]["evidence"]["input_digest"]
        require(evidence_input_digest == original_digest, "ledger evidence lost the original argument digest")
        if name == "rewrite":
            require(events[0]["intent"]["args_after"] == events[1]["args_digest"] == actual_digest,
                    "persisted rewritten argument differs from the actual argument")
        require(SnapshotStore(store).read(output_ref)["value"] == trial.terminals[0]
                and trial.executions == [actual] and disk == {actual: trial.terminals}, "effect or evidence mismatch")
        try:
            journal.execute_once(decision_id, trial.call_id, write)
        except TransitionError:
            duplicate_rejected = True
        require(duplicate_rejected and trial.executions == [actual] and history() == events, "duplicate execution")
    return {"case": name, "sdk_call_id": trial.call_id, "decision_id": decision_id, "run_id": run.run_id,
            "original_args_digest": original_digest, "actual_args_digest": actual_digest if disk else None,
            "evidence_input_digest": evidence_input_digest,
            "persisted_types_before_write": events_before_effect, "persisted_types_after": types, "disk": disk,
            "blocked_by": blocked_by, "repeat_rejected": duplicate_rejected, "passed": True}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/b-m0/ledger")
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix="consumer-", dir=args.output.resolve()))
    store = SQLiteEventStore(root / "probe.sqlite")
    identities = replay_identities(matrix, store)
    with chdir(root):
        cases = [await journal_case(name, root / name, store)
                 for name in ("allow", "rewrite", "rewrite_mismatch", "deny", "missing_snapshot", "stale_segment")]
    result = {"format": "b-m1-consumer-v1", "completed_at": datetime.now(timezone.utc).isoformat(),
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "matrix_canonical_sha256": digest_json(matrix), "matrix_source_head": matrix["git_head"],
              "sdk_for_journal_cases": "openjiuwen", "online_model_calls": 0, "identities": identities,
              "journal_cases": cases, "passed": True}
    report = root / "report.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
