from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from assurance_ledger.adapters.base import IntentOutcome
from assurance_ledger.adapters.tools import (
    ToolExecutor,
    ToolViolation,
    guarded_execute,
)


@pytest.fixture
def executor(tmp_path: Path) -> ToolExecutor:
    root = tmp_path / "workspaces"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    return ToolExecutor(workspace, root, allowed_remotes=("origin",),
                        allowed_branches=("*",))


def test_file_tools_stay_inside_workspace(executor: ToolExecutor):
    written = executor.write_file({"path": "docs/notes.txt",
                                   "content": "hello"})
    assert written["ok"] is True
    assert executor.read_file({"path": "docs/notes.txt"})["text"] == "hello"
    with pytest.raises(ToolViolation):
        executor.read_file({"path": "../secret.txt"})
    with pytest.raises(ToolViolation):
        executor.write_file({"path": "../escape.txt", "content": "x"})
    executor.delete_file({"path": "docs/notes.txt"})
    assert not (executor.workspace / "docs/notes.txt").exists()
    with pytest.raises(ToolViolation):
        executor.delete_file({"path": "docs"})


def test_command_requires_allowlist_and_structured_argv(executor: ToolExecutor):
    with pytest.raises(ToolViolation):
        executor.run_command({"argv": ["definitely-not-allowed", "-x"]})
    with pytest.raises(ToolViolation):
        executor.run_command({"argv": "python --version"})
    result = executor.run_command({"argv": ["python", "--version"],
                                   "timeout_seconds": 30})
    assert "exit_code" in result
    assert result["summary"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_publish_uses_dry_run_and_no_force(executor: ToolExecutor,
                                               tmp_path: Path):
    workspace = executor.workspace
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True,
                   capture_output=True)
    subprocess.run(["git", "init"], cwd=workspace, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=workspace, check=True, capture_output=True)
    (workspace / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True,
                   capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)],
                   cwd=workspace, check=True, capture_output=True)
    branch = subprocess.run(["git", "branch", "--show-current"],
                            cwd=workspace, check=True, capture_output=True,
                            text=True).stdout.strip()
    result = executor.git_publish({"operation": "push", "remote": "origin",
                                   "branch": branch})
    assert result["ok"] is True
    with pytest.raises(ToolViolation):
        executor.git_publish({"operation": "push", "remote": "origin",
                              "branch": branch, "force": True})


def test_guarded_execute_reports_evidence(executor: ToolExecutor):
    calls = {"intent": 0, "result": 0, "evidence": None}

    class Hooks:
        async def guarded_call(self, tool_name, args, decision_class):
            calls["intent"] += 1
            return IntentOutcome(decision_id="DEC-TOOL", attempt_id="ATT-TOOL",
                                 gate="ALLOW", execute=True, args=args,
                                 args_digest="sha256:" + "a" * 64)

        async def report_result(self, decision_id, attempt_id, status,
                                summary, evidence=None, output=None):
            calls["result"] += 1
            calls["evidence"] = evidence
            return {"status": status}

        async def emit(self, event_type, payload):
            return None

    result = asyncio.run(guarded_execute(
        Hooks(), executor, tool_name="write_file", decision_class="file_write",
        args={"path": "out.txt", "content": "x"},
        model_id="test-model", model_params="t=0"))
    assert result["ok"] is True
    assert calls["intent"] == 1
    assert calls["result"] == 1
    assert calls["evidence"]["verdict"] == "completed"
    assert calls["evidence"]["input_digest"] == "sha256:" + "a" * 64
    assert (executor.workspace / "out.txt").read_text(encoding="utf-8") == "x"
