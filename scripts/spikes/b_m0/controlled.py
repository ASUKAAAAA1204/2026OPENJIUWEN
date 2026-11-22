"""Shared M0 execution boundary for registered, directory-scoped probe tools."""
import asyncio
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

from adapters import REQUESTED, TOOL
from approval import PendingApproval

APPROVED = "approved.txt"
CLASSIFIED = {TOOL: "file_write", "delegate_probe": "delegate"}


@dataclass
class BusinessRun:
    task_id: str = field(default_factory=lambda: "task-" + uuid4().hex)
    run_id: str = field(default_factory=lambda: "run-" + uuid4().hex)
    segment: int = 1
    parent_run_id: str | None = None


ACTIVE_RUN: ContextVar[BusinessRun | None] = ContextVar("probe_business_run", default=None)


@dataclass
class WriteTrial:
    directory: Path
    decision: str
    call_id: str = field(default_factory=lambda: "call-" + uuid4().hex)
    requested: list[str] = field(default_factory=list)
    executions: list[str] = field(default_factory=list)
    terminals: list[str] = field(default_factory=list)
    approval: PendingApproval | None = None
    target: str = REQUESTED
    tool_name: str = TOOL
    side_effect: bool = True
    operation: Callable[[str], Awaitable[str]] | None = None
    events: list[dict] = field(default_factory=list)
    contexts: list[dict] = field(default_factory=list)
    file_ns: int = 0
    gate_ns: int = 0
    wait_ns: int = 0

    def write_file(self, target: str) -> str:
        self.executions.append(target)
        if target not in (self.target, APPROVED) or not re.fullmatch(r"[A-Za-z0-9_-]+\.txt", target):
            raise ValueError("Unexpected test file")
        receipt = f"written:{self.call_id}:{target}:{uuid4().hex}"
        started = perf_counter_ns()
        try:
            with (self.directory / target).open("a", encoding="ascii") as output:
                output.write(receipt + "\n")
        finally:
            self.file_ns += perf_counter_ns() - started
        return receipt

    async def guarded_call(self, target: str) -> str:
        """Run the registered probe on its bound target and return a single receipt."""
        started = perf_counter_ns()
        self.requested.append(target)
        if target != self.target:
            raise ValueError("Unexpected tool argument")
        current = ACTIVE_RUN.get()
        if current is not None:
            self.contexts.append({**asdict(current), "target": target, "call_id": self.call_id})
        decision = self.decision
        outcome = None
        unclassified = self.side_effect and self.tool_name not in CLASSIFIED
        if unclassified:
            self.events.append({"type": "UNHOOKED", "call_id": self.call_id, "tool": self.tool_name,
                                "side_effect": True, "gate": "NONE"})
            decision = "NONE"
        if decision == "ASK":
            if self.approval is None:
                raise ValueError("ASK has no approval context")
            waiting = perf_counter_ns()
            self.gate_ns += waiting - started
            try:
                outcome = await self.approval.wait(self.call_id, target)
            except asyncio.CancelledError:
                self.terminals.append(f"cancelled:{self.call_id}")
                raise
            finally:
                self.wait_ns += perf_counter_ns() - waiting
            started = perf_counter_ns()
            decision = {"approve": "ALLOW", "reject": "DENY", "timeout": self.approval.timeout_policy}[outcome]
        self.gate_ns += perf_counter_ns() - started
        if decision == "DENY":
            reason = "timeout_denied" if outcome == "timeout" else "denied"
            receipt = f"{reason}:{self.call_id}"
        elif decision in ("ALLOW", "REWRITE") or unclassified:
            actual = APPROVED if decision == "REWRITE" else target
            try:
                receipt = await self.operation(actual) if self.operation else self.write_file(actual)
            except OSError as error:
                code = type(error).__name__
                self.events.append({"type": "EXEC_FAILED", "call_id": self.call_id, "error": code})
                receipt = f"failed:{self.call_id}:{code}"
        else:
            raise ValueError("Unknown test policy")
        self.terminals.append(receipt)
        return receipt
