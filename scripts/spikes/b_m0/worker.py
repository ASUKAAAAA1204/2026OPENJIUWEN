"""One SDK per process. Run from a new, empty SDK evidence directory."""
import asyncio
import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from uuid import uuid4

from adapters import ADAPTERS, DISTRIBUTIONS, REQUESTED, Replies


SCENARIOS = {"S01": "ALLOW", "S02": "DENY", "S03": "REWRITE"}
APPROVED = "approved.txt"


@dataclass
class WriteTrial:
    directory: Path
    decision: str
    call_id: str = field(default_factory=lambda: "call-" + uuid4().hex)
    requested: list[str] = field(default_factory=list)
    executions: list[str] = field(default_factory=list)
    terminals: list[str] = field(default_factory=list)

    def write_file(self, target: str) -> str:
        # Count entry to the side-effecting tool independently of gate events and disk reads.
        self.executions.append(target)
        if target not in (REQUESTED, APPROVED):
            raise ValueError("Unexpected file name in fixed test tool")
        receipt = f"written:{self.call_id}:{target}:{uuid4().hex}"
        with (self.directory / target).open("a", encoding="ascii") as output:
            output.write(receipt + "\n")
        return receipt

    def guarded_call(self, target: str) -> str:
        """Write a receipt to the requested test file, subject to the host's test policy."""
        self.requested.append(target)
        if target != REQUESTED:
            raise ValueError("Unexpected tool argument")
        if self.decision == "DENY":
            receipt = f"denied:{self.call_id}"
        elif self.decision == "REWRITE":
            receipt = self.write_file(APPROVED)
        elif self.decision == "ALLOW":
            receipt = self.write_file(target)
        else:
            raise ValueError("Unknown test policy")
        self.terminals.append(receipt)
        return receipt


def verify(trial: WriteTrial, scenario: str, replies: Replies) -> dict:
    expected_targets = {"S01": ["requested.txt"], "S02": [], "S03": ["approved.txt"]}[scenario]
    files = {p.name: p.read_text(encoding="ascii").splitlines() for p in trial.directory.iterdir()}
    if expected_targets:
        target, = expected_targets
        lines = files.get(target, [])
        disk_ok = (set(files) == {target} and len(lines) == 1
                   and lines[0].startswith(f"written:{trial.call_id}:{target}:"))
        expected_terminals = lines
    else:
        disk_ok = not files
        expected_terminals = [f"denied:{trial.call_id}"]
    checks = {
        "original_argument_seen_once": trial.requested == ["requested.txt"],
        "side_effect_invocations": trial.executions == expected_targets,
        "disk_readback": disk_ok,
        "one_terminal": len(trial.terminals) == 1 and trial.terminals == expected_terminals,
        "sdk_receipt_and_call_id": len(replies) == 1 and replies == [
            (trial.call_id, receipt) for receipt in expected_terminals
        ],
    }
    return {"checks": checks, "files_read_back": files, "invocations": trial.executions,
            "requested": trial.requested, "terminals": trial.terminals, "sdk_replies": replies}


async def exercise(framework: str, case: str, repeat: int) -> dict:
    directory = Path(f"{case}-{repeat}")
    directory.mkdir()
    trial = WriteTrial(directory, SCENARIOS[case])
    row = {"scenario": case, "decision": trial.decision, "repeat": repeat, "directory": str(directory),
           "call_id": trial.call_id}
    replies = []
    try:
        replies = await asyncio.wait_for(ADAPTERS[framework](trial.call_id, trial.guarded_call), 30)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row.update(verify(trial, case, replies))
    row["passed"] = "error" not in row and all(row["checks"].values())
    return row


async def run(framework: str) -> bool:
    report = {"framework": framework, "python": platform.python_version(), "executable": sys.executable,
              "sdk_distribution": DISTRIBUTIONS[framework], "model_input": "scripted_tool_request",
              "online_model_calls": 0, "trials": [], "passed": False}
    try:
        report["sdk_version"] = metadata.version(DISTRIBUTIONS[framework])
        packages = {d.metadata["Name"]: d.version for d in metadata.distributions()}
        manifest = (json.dumps(packages, sort_keys=True, indent=2) + "\n").encode("utf-8")
        Path("installed-versions.json").write_bytes(manifest)
        report["installed_versions_sha256"] = hashlib.sha256(manifest).hexdigest()
        for repeat in range(1, 4):
            for case in SCENARIOS:
                report["trials"].append(await exercise(framework, case, repeat))
        report["passed"] = all(row["passed"] for row in report["trials"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    Path("result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    return report["passed"]


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run(sys.argv[1])) else 1)
