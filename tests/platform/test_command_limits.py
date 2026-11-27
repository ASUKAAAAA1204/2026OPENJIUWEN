from __future__ import annotations

import os
from pathlib import Path

import pytest

from assurance_ledger.adapters import tools
from assurance_ledger.adapters.tools import (
    CommandLimits,
    ToolExecutor,
    ToolViolation,
)
from assurance_ledger.gate.engine import evaluate
from assurance_ledger.policy.table import PolicyTable, default_configuration


def _policy() -> PolicyTable:
    return PolicyTable(version="policy-v1", description="默认策略",
                       published_at="2026-11-27T00:00:00Z",
                       published_by="SYSTEM", eligible=True, reason="测试",
                       configuration=default_configuration())


def _executor(tmp_path: Path, **kwargs) -> ToolExecutor:
    root = tmp_path / "root"
    workspace = root / "ws"
    workspace.mkdir(parents=True)
    return ToolExecutor(workspace, root, allowed_remotes=("origin",),
                        allowed_branches=("main",), **kwargs)


def test_git_remote_and_branch_allowlist(tmp_path):
    policy = _policy()

    def judge(args, **allow):
        return evaluate(policy, decision_class="vcs_publish",
                        tool_name="git_publish", args=args,
                        workspace=tmp_path, **allow)

    allowed = judge({"operation": "push", "remote": "origin", "branch": "main"},
                    allowed_remotes=("origin",),
                    allowed_branches=("main", "release/*"))
    assert allowed.gate != "DENY"
    release = judge({"operation": "tag", "remote": "origin",
                     "branch": "release/1.0"},
                    allowed_remotes=("origin",),
                    allowed_branches=("main", "release/*"))
    assert release.gate != "DENY"
    remote = judge({"operation": "push", "remote": "upstream",
                    "branch": "main"}, allowed_remotes=("origin",),
                   allowed_branches=("main",))
    assert remote.gate == "DENY"
    assert "GIT_REMOTE_NOT_ALLOWED" in remote.reason
    branch = judge({"operation": "push", "remote": "origin",
                    "branch": "feature/x"}, allowed_remotes=("origin",),
                   allowed_branches=("main",))
    assert branch.gate == "DENY"
    assert "GIT_BRANCH_NOT_ALLOWED" in branch.reason
    closed = judge({"operation": "push", "remote": "origin", "branch": "main"})
    assert closed.gate == "DENY"
    assert "GIT_REMOTE_NOT_ALLOWED" in closed.reason


def test_tool_executor_rechecks_git_allowlist(tmp_path):
    executor = _executor(tmp_path)
    with pytest.raises(ToolViolation):
        executor.git_publish({"operation": "push", "remote": "upstream",
                              "branch": "main"})
    with pytest.raises(ToolViolation):
        executor.git_publish({"operation": "push", "remote": "origin",
                              "branch": "feature/x"})


def test_command_timeout_and_output_cap(tmp_path, monkeypatch):
    executor = _executor(tmp_path)
    timed = executor.run_command({
        "argv": ["python", "-c", "import time; time.sleep(5)"],
        "timeout_seconds": 1})
    assert timed["ok"] is False
    assert "资源限制" in timed["summary"]
    assert timed["command_limits"] in ("full", "partial")
    monkeypatch.setattr(tools, "MAX_OUTPUT_CHARS", 100)
    capped = executor.run_command({"argv": ["python", "-c", "print('x'*500)"]})
    assert len(capped["stdout"]) == 100
    assert capped["ok"] is True


def test_command_limits_are_reported(tmp_path):
    executor = _executor(tmp_path, limits=CommandLimits(cpu_seconds=5))
    result = executor.run_command({"argv": ["python", "-c", "print('ok')"]})
    assert result["ok"] is True
    assert result["command_limits"] in ("full", "partial")


@pytest.mark.skipif(os.name != "posix", reason="RLIMIT_AS 只在 POSIX 生效")
def test_command_resource_limit_posix(tmp_path):
    executor = _executor(tmp_path, limits=CommandLimits(memory_mb=64))
    result = executor.run_command({
        "argv": ["python", "-c", "x = bytearray(512 * 1024 * 1024)"],
        "timeout_seconds": 10})
    assert result["ok"] is False
    assert result["exit_code"] != 0
