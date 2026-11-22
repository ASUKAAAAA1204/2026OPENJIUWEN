"""One SDK per process. Run from a new, empty SDK evidence directory."""
import asyncio
import hashlib
import json
import platform
import sys
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
from time import perf_counter_ns, process_time_ns

from adapters import ADAPTERS, DISTRIBUTIONS, REQUESTED, Replies
from approval import ApprovalRequest, PendingApproval, args_digest
from controlled import APPROVED, WriteTrial
from lifecycle import CASES


BASELINE = {"S01": "ALLOW", "S02": "DENY", "S03": "REWRITE"}
RESPONSES = {"S04": "approve", "S05": "reject", "S06": None}
SUITES = {"baseline": tuple(BASELINE), "approval": tuple(RESPONSES),
          "all": (*BASELINE, *RESPONSES), "lifecycle": tuple(CASES),
          "m0": (*BASELINE, *RESPONSES, *CASES)}


async def dispatch(framework: str, trial: WriteTrial, case: str, observation: dict) -> Replies:
    call = ADAPTERS[framework](trial.call_id, trial.guarded_call)
    approval = trial.approval
    if approval is None:
        return await call

    sdk = asyncio.create_task(call)
    try:
        # Observe the real tool after it asks, while its SDK invocation is still outstanding.
        await asyncio.wait({sdk, approval.opened}, return_when=asyncio.FIRST_COMPLETED)
        if approval.opened.done():
            request = approval.opened.result()
            observation["request"] = asdict(request)
            observation["while_waiting"] = {
                "sdk_pending": not sdk.done(), "approval_pending": not approval.outcome.done(),
                "invocations": list(trial.executions), "terminals": list(trial.terminals),
                "files": sorted(path.name for path in trial.directory.iterdir()),
            }
            result = RESPONSES[case]
            if result is not None:
                # The test approver binds its own expected target to the observed request.
                binding = ApprovalRequest(request.approval_id, trial.call_id, args_digest(REQUESTED))
                observation["response"] = {**asdict(binding), "result": result,
                                           "accepted": approval.respond(binding, result)}
        return await sdk
    finally:
        if not sdk.done():
            sdk.cancel()
        await asyncio.gather(sdk, return_exceptions=True)


def verify(trial: WriteTrial, scenario: str, replies: Replies) -> dict:
    expected_targets = {"S01": [REQUESTED], "S02": [], "S03": [APPROVED],
                        "S04": [REQUESTED], "S05": [], "S06": []}[scenario]
    files = {p.name: p.read_text(encoding="ascii").splitlines() for p in trial.directory.iterdir()}
    if expected_targets:
        target, = expected_targets
        lines = files.get(target, [])
        disk_ok = (set(files) == {target} and len(lines) == 1
                   and lines[0].startswith(f"written:{trial.call_id}:{target}:"))
        expected_terminals = lines
    else:
        disk_ok = not files
        reason = "timeout_denied" if scenario == "S06" else "denied"
        expected_terminals = [f"{reason}:{trial.call_id}"]
    checks = {
        "original_argument_seen_once": trial.requested == ["requested.txt"],
        "side_effect_invocations": trial.executions == expected_targets,
        "disk_readback": disk_ok,
        "one_terminal": len(trial.terminals) == 1 and trial.terminals == expected_terminals,
        "sdk_receipt_and_call_id": len(replies) == 1 and replies == [
            (trial.call_id, receipt) for receipt in expected_terminals
        ],
    }
    return {"checks": checks, "files_read_back": files, "write_count": sum(map(len, files.values())),
            "invocations": trial.executions,
            "requested": trial.requested, "terminals": trial.terminals, "sdk_replies": replies}


def verify_approval(trial: WriteTrial, case: str, observation: dict) -> dict[str, bool]:
    approval = trial.approval
    assert approval is not None
    request = observation.get("request", {})
    response = observation.get("response")
    result = approval.outcome.result() if approval.settled_at is not None else None
    elapsed = None if approval.settled_at is None else approval.settled_at - approval.opened_at
    observation.update(outcome=result, risk=approval.risk, timeout_policy=approval.timeout_policy,
                       timeout_seconds=approval.timeout_seconds, elapsed_seconds=elapsed)
    if case == "S06":
        response_ok = response is None and result == "timeout"
        timing_ok = approval.settled_at is not None and approval.settled_at >= approval.deadline
    else:
        expected = RESPONSES[case]
        response_ok = result == expected and response == {**request, "result": expected, "accepted": True}
        timing_ok = approval.settled_at is not None and approval.settled_at < approval.deadline
    return {
        "approval_bound_to_call_and_args": bool(request.get("approval_id"))
            and request.get("call_id") == trial.call_id and request.get("args_digest") == args_digest(REQUESTED),
        "pending_before_side_effect": observation.get("while_waiting") == {
            "sdk_pending": True, "approval_pending": True, "invocations": [], "terminals": [], "files": []},
        "approval_response_or_automatic_timeout": response_ok,
        "deadline_observed": timing_ok,
    }


async def exercise(framework: str, case: str, repeat: int) -> dict:
    directory = Path(f"{case}-{repeat}")
    directory.mkdir()
    if case in CASES:
        try:
            evidence = await asyncio.wait_for(CASES[case](framework, directory), 35)
        except Exception as exc:
            evidence = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"scenario": case, "repeat": repeat, "directory": str(directory), **evidence}
    trial = WriteTrial(directory, BASELINE.get(case, "ASK"),
                       approval=PendingApproval() if case in RESPONSES else None)
    row = {"scenario": case, "decision": trial.decision, "repeat": repeat, "directory": str(directory),
           "call_id": trial.call_id}
    replies = []
    observation = {"response": None}
    try:
        replies = await asyncio.wait_for(dispatch(framework, trial, case, observation), 30)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row.update(verify(trial, case, replies), gate_ns=trial.gate_ns, file_ns=trial.file_ns,
               approval_wait_ns=trial.wait_ns)
    if trial.approval is not None:
        row["checks"].update(verify_approval(trial, case, observation))
        row["approval"] = observation
    row["passed"] = "error" not in row and all(row["checks"].values())
    return row


async def run(framework: str, suite: str = "baseline") -> bool:
    report = {"framework": framework, "python": platform.python_version(), "executable": sys.executable,
              "sdk_distribution": DISTRIBUTIONS[framework], "model_input": "scripted_tool_request",
              "online_model_calls": 0, "trials": [], "passed": False}
    try:
        report["sdk_version"] = metadata.version(DISTRIBUTIONS[framework])
        packages = {d.metadata["Name"]: d.version for d in metadata.distributions()}
        report["mcp_version"] = packages.get("mcp")
        manifest = (json.dumps(packages, sort_keys=True, indent=2) + "\n").encode("utf-8")
        Path("installed-versions.json").write_bytes(manifest)
        report["installed_versions_sha256"] = hashlib.sha256(manifest).hexdigest()
        started, cpu_started = perf_counter_ns(), process_time_ns()
        for repeat in range(1, 4):
            for case in SUITES[suite]:
                begin = perf_counter_ns()
                row = await exercise(framework, case, repeat)
                row["elapsed_ns"] = perf_counter_ns() - begin
                report["trials"].append(row)
        report.update(wall_ns=perf_counter_ns() - started, cpu_ns=process_time_ns() - cpu_started)
        report["passed"] = all(row["passed"] for row in report["trials"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    Path("result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    return report["passed"]


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run(*sys.argv[1:])) else 1)
