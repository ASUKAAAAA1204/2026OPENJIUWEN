"""S07–S12. SDK callbacks, independent directory reads and observed business identities."""
import asyncio
from dataclasses import asdict
from pathlib import Path

from adapters import ADAPTERS, REQUESTED, agents_handoff
from approval import PendingApproval
from controlled import ACTIVE_RUN, BusinessRun, WriteTrial


async def invoke(framework, trial, run, **options):
    token = ACTIVE_RUN.set(run)
    try:
        return await ADAPTERS[framework](trial.call_id, trial.guarded_call, target=trial.target,
                                         name=trial.tool_name, **options)
    finally:
        ACTIVE_RUN.reset(token)


def observe(trial, replies):
    files = {p.name: p.read_text(encoding="ascii").splitlines()
             for p in trial.directory.iterdir() if p.is_file()}
    return {"call_id": trial.call_id, "tool": trial.tool_name, "requested": trial.requested,
            "invocations": trial.executions, "files": files, "contexts": trial.contexts,
            "events": trial.events, "terminals": trial.terminals, "sdk_replies": list(replies),
            "gate_ns": trial.gate_ns, "file_ns": trial.file_ns, "approval_wait_ns": trial.wait_ns}


def matches_write(row, target):
    receipts = row["files"].get(target, [])
    return (row["requested"] == [target] and row["invocations"] == [target]
            and set(row["files"]) == {target} and len(receipts) == 1
            and receipts[0].startswith(f"written:{row['call_id']}:{target}:")
            and row["terminals"] == receipts and row["sdk_replies"] == [(row["call_id"], receipts[0])])


def finish(observations, checks, **evidence):
    return {"call_id": observations[0]["call_id"], "observations": observations, "checks": checks,
            "passed": all(checks.values()), **evidence}


async def failed_tool(framework, directory):
    # An existing directory cannot be opened as an appendable file, on Windows or POSIX.
    (directory / REQUESTED).mkdir()
    trial = WriteTrial(directory, "ALLOW")
    replies = await invoke(framework, trial, BusinessRun())
    row = observe(trial, replies)
    error, = trial.events
    receipt = f"failed:{trial.call_id}:{error['error']}"
    return finish([row], {"one_real_attempt": trial.executions == [REQUESTED],
                          "one_failure": error["type"] == "EXEC_FAILED" and error["call_id"] == trial.call_id
                          and error["error"] in ("PermissionError", "IsADirectoryError"),
                          "sdk_and_terminal": replies == [(trial.call_id, receipt)] and trial.terminals == [receipt],
                          "no_write": row["files"] == {} and not list((directory / REQUESTED).iterdir())})


async def cancelled_wait(framework, directory):
    approval = PendingApproval(timeout_seconds=10)
    trial = WriteTrial(directory, "ASK", approval=approval)
    task = asyncio.create_task(invoke(framework, trial, BusinessRun()))
    try:
        await asyncio.wait({task, approval.opened}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            await task
            raise ValueError("The SDK finished before cancellation")
        binding = approval.opened.result()
        before = {"files": sorted(p.name for p in directory.iterdir()), "calls": list(trial.executions),
                  "pending": not approval.outcome.done()}
        task.cancel()
        try:
            await task
            sdk_state = "returned"
        except asyncio.CancelledError:
            sdk_state = "cancelled"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    late = approval.respond(binding, "approve")
    row = observe(trial, [])
    return finish([row], {"observed_wait": before == {"files": [], "calls": [], "pending": True},
                          "sdk_cancelled": sdk_state == "cancelled", "one_cancel_terminal":
                          trial.terminals == [f"cancelled:{trial.call_id}"],
                          "no_late_write": not late and not trial.executions and not list(directory.iterdir())},
                  pending_request=asdict(binding), before_cancel=before, sdk_state=sdk_state, late_reply_accepted=late)


async def concurrent_tools(framework, directory):
    all_entered = asyncio.Event()
    entered = set()
    active = 0
    peak = 0

    async def run_one(index):
        nonlocal active, peak
        run = BusinessRun()
        slot = directory / f"task-{index}"
        slot.mkdir()
        trial = WriteTrial(slot, "ALLOW", target=f"receipt-{index}.txt")

        async def simultaneous(target):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.add(trial.call_id)
            if len(entered) == 10:
                all_entered.set()
            try:
                await all_entered.wait()
                return trial.write_file(target)
            finally:
                active -= 1

        trial.operation = simultaneous
        replies = await invoke(framework, trial, run)
        observed = observe(trial, replies)
        observed.update(slot=slot.name, expected_run=asdict(run))
        return observed

    tasks = [asyncio.create_task(run_one(index)) for index in range(10)]
    try:
        rows = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    errors = 0
    for index, row in enumerate(rows):
        target = f"receipt-{index}.txt"
        expected = {**row["expected_run"], "target": target, "call_id": row["call_id"]}
        errors += row["contexts"] != [expected] or not matches_write(row, target)
    return finish(rows, {"ten_actually_overlapped": peak == 10 and active == 0,
                         "unique_calls": len(entered) == 10, "identity_and_args": errors == 0,
                         "distinct_runs_and_tasks": all(len({r["expected_run"][k] for r in rows}) == 10
                                                        for k in ("task_id", "run_id"))},
                  peak_in_tool=peak, association_errors=errors)


async def resumed_tool(framework, directory):
    run = BusinessRun()
    original = asdict(run)
    trial = WriteTrial(directory, "ALLOW")
    holds = []

    def resume(native_id, sdk_call):
        holds.append({"native_id": native_id, "sdk_call_id": sdk_call, "run": asdict(run),
                      "files": sorted(p.name for p in directory.iterdir()), "invocations": list(trial.executions)})
        run.segment += 1

    replies = await invoke(framework, trial, run, on_pause=resume)
    row = observe(trial, replies)
    expected = {**original, "segment": 2, "target": REQUESTED, "call_id": trial.call_id}
    return finish([row], {"one_native_interruption": len(holds) == 1 and bool(holds[0]["native_id"]),
                          "held_before_write": holds[0]["sdk_call_id"] == trial.call_id
                          and holds[0]["run"] == original and holds[0]["files"] == holds[0]["invocations"] == [],
                          "same_business_identity_next_segment": row["contexts"] == [expected],
                          "one_resumed_write": matches_write(row, REQUESTED)},
                  paused=holds, resumed_run=asdict(run))


async def delegated_tool(framework, directory):
    parent = BusinessRun()
    parent_dir, child_dir = directory / "parent", directory / "child"
    parent_dir.mkdir()
    child_dir.mkdir()
    outer = WriteTrial(parent_dir, "ALLOW", target="delegate.txt", tool_name="delegate_probe")
    inner = WriteTrial(child_dir, "ALLOW", target="child.txt")
    children = []
    child_replies = []

    async def delegate(target):
        owner = ACTIVE_RUN.get()
        child = BusinessRun(parent_run_id=owner.run_id)
        children.append(child)
        if framework != "agents":
            child_replies.extend(await invoke(framework, inner, child))
        return f"delegated:{outer.call_id}:{child.run_id}"

    outer.operation = delegate
    native = None
    if framework == "agents":
        async def child_tool(target: str) -> str:
            """Write the delegated receipt."""
            child, = children
            token = ACTIVE_RUN.set(child)
            try:
                return await inner.guarded_call(target)
            finally:
                ACTIVE_RUN.reset(token)

        token = ACTIVE_RUN.set(parent)
        try:
            native = await agents_handoff(outer.call_id, inner.call_id, inner.target, outer.guarded_call, child_tool)
        finally:
            ACTIVE_RUN.reset(token)
        child_replies = native["child_replies"]
        parent_replies = native["parent_replies"]
    else:
        parent_replies = await invoke(framework, outer, parent)
    child, = children
    parent_row = observe(outer, parent_replies)
    child_row = observe(inner, child_replies)
    route_ok = (parent_replies == [(outer.call_id, f"delegated:{outer.call_id}:{child.run_id}")] if native is None
                else native["parent_call_id"] == parent_replies[0][0] == outer.call_id
                and native["from_agent"] == "probe_parent" and native["to_agent"] == native["result_agent"] == "probe_child")
    checks = {"real_delegation": route_ok and outer.terminals == [f"delegated:{outer.call_id}:{child.run_id}"],
              "parent_and_child_ids": child.parent_run_id == parent.run_id and child.run_id != parent.run_id,
              "separate_call_owners": parent_row["contexts"] == [{**asdict(parent), "target": outer.target, "call_id": outer.call_id}]
              and child_row["contexts"] == [{**asdict(child), "target": inner.target, "call_id": inner.call_id}],
              "only_child_wrote": not outer.executions and not parent_row["files"] and matches_write(child_row, inner.target)}
    return finish([parent_row, child_row], checks, parent=asdict(parent), child=asdict(child), native_handoff=native)


async def unclassified_tool(framework, directory):
    trial = WriteTrial(directory, "ALLOW", tool_name="unlisted_write_probe", side_effect=True)
    before = []

    async def write_after_mark(target):
        before.extend(dict(e) for e in trial.events)
        return trial.write_file(target)

    trial.operation = write_after_mark
    row = observe(trial, await invoke(framework, trial, BusinessRun()))
    expected = [{"type": "UNHOOKED", "tool": trial.tool_name, "call_id": trial.call_id,
                 "side_effect": True, "gate": "NONE"}]
    return finish([row], {"unclassified_marked_once_before_effect": trial.events == before == expected,
                          "declared_test_effect_observed": matches_write(row, REQUESTED)},
                  events_before_effect=before)


CASES = {"S07": failed_tool, "S08": cancelled_wait, "S09": concurrent_tools,
         "S10": resumed_tool, "S11": delegated_tool, "S12": unclassified_tool}
