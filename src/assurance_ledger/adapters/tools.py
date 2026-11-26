"""受控工具：允许根内文件、结构化命令与 Git 发布

这里不判断策略；调用方必须先通过中央 Gate。所有路径在真实绝对路径上校验，
命令禁止 shell，Git 发布先 dry-run。未注册动作返回明确错误。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..adapters.base import Denied, IntentOutcome, TurnHooks
from ..gate.engine import GateError, resolve_within

MAX_FILE_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 200_000
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")


class ToolError(RuntimeError):
    pass


class ToolViolation(ToolError):
    pass


def _safe_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP",
            "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
    return {key: value for key, value in os.environ.items() if key in keep}


class ToolExecutor:
    def __init__(self, workspace: str | Path, allowed_root: str | Path):
        self.workspace = Path(workspace).resolve()
        self.allowed_root = Path(allowed_root).resolve()
        try:
            resolve_within(self.allowed_root, str(self.workspace))
        except GateError as error:
            raise ToolViolation(f"工作目录不在允许根内：{error}") from error

    def _path(self, value: Any) -> Path:
        if not isinstance(value, str):
            raise ToolViolation("path 必须是字符串")
        try:
            return resolve_within(self.workspace, value)
        except GateError as error:
            raise ToolViolation(str(error)) from error

    def read_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        path = self._path(args.get("path"))
        if not path.is_file():
            raise ToolError(f"文件不存在：{path.name}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ToolError("文件超过读取上限")
        return {"ok": True, "path": str(path),
                "text": data.decode("utf-8", errors="replace"),
                "bytes": len(data), "summary": f"读取 {path.name}（{len(data)} 字节）"}

    def write_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        path = self._path(args.get("path"))
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolViolation("content 必须是字符串")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ToolError("写入内容超过上限")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"ok": True, "path": str(path), "bytes": len(data),
                "summary": f"写入 {path.name}（{len(data)} 字节）"}

    def delete_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        path = self._path(args.get("path"))
        if not path.exists():
            raise ToolError("目标不存在")
        if path.is_dir():
            raise ToolViolation("拒绝删除目录")
        path.unlink()
        return {"ok": True, "path": str(path), "summary": f"删除 {path.name}"}

    def run_command(self, args: Mapping[str, Any]) -> dict[str, Any]:
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and item for item in argv):
            raise ToolViolation("argv 必须是非空字符串数组")
        binary = Path(argv[0]).name
        from ..gate.engine import ALLOWED_COMMANDS
        if binary not in ALLOWED_COMMANDS:
            raise ToolViolation(f"命令 {binary} 不在允许清单")
        cwd_value = args.get("cwd", ".")
        cwd = self._path(cwd_value)
        if not cwd.is_dir():
            raise ToolError("cwd 不是目录")
        timeout = args.get("timeout_seconds", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ToolViolation("timeout_seconds 必须是数字")
        timeout = max(1, min(300, float(timeout)))
        completed = subprocess.run(
            argv, cwd=cwd, env=_safe_env(), capture_output=True,
            text=True, timeout=timeout, shell=False)
        stdout = (completed.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (completed.stderr or "")[:MAX_OUTPUT_CHARS]
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "summary": (f"命令退出码 {completed.returncode}；"
                        f"stdout {len(stdout)} 字符，stderr {len(stderr)} 字符"),
        }

    def _git(self, cwd: Path, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=_safe_env(), capture_output=True,
            text=True, timeout=timeout, shell=False)

    def git_publish(self, args: Mapping[str, Any]) -> dict[str, Any]:
        operation = args.get("operation")
        remote = args.get("remote")
        branch = args.get("branch")
        if operation not in ("push", "tag", "release"):
            raise ToolViolation("Git 操作必须是 push/tag/release")
        if args.get("force") is True:
            raise ToolViolation("禁止强制推送")
        for name, value in (("remote", remote), ("branch", branch)):
            if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value):
                raise ToolViolation(f"{name} 名称非法")
            if value.startswith("-") or ".." in value:
                raise ToolViolation(f"{name} 名称非法")
        cwd = self.workspace
        if operation == "push":
            dry = self._git(cwd, "push", "--dry-run", remote, branch)
            if dry.returncode != 0:
                return {"ok": False, "exit_code": dry.returncode,
                        "stderr": (dry.stderr or "")[:MAX_OUTPUT_CHARS],
                        "summary": "Git 推送 dry-run 失败"}
            done = self._git(cwd, "push", remote, branch)
            return {"ok": done.returncode == 0, "exit_code": done.returncode,
                    "stdout": (done.stdout or "")[:MAX_OUTPUT_CHARS],
                    "stderr": (done.stderr or "")[:MAX_OUTPUT_CHARS],
                    "summary": f"Git push {remote}/{branch}"}
        if operation == "tag":
            done = self._git(cwd, "tag", branch)
            if done.returncode != 0:
                return {"ok": False, "exit_code": done.returncode,
                        "stderr": (done.stderr or "")[:MAX_OUTPUT_CHARS],
                        "summary": "Git 打标签失败"}
            pushed = self._git(cwd, "push", remote, f"refs/tags/{branch}")
            return {"ok": pushed.returncode == 0,
                    "exit_code": pushed.returncode,
                    "stderr": (pushed.stderr or "")[:MAX_OUTPUT_CHARS],
                    "summary": f"Git tag {branch} 并推送"}
        done = self._git(cwd, "tag", branch)
        pushed = self._git(cwd, "push", remote, f"refs/tags/{branch}")
        return {"ok": done.returncode == 0 and pushed.returncode == 0,
                "exit_code": pushed.returncode,
                "stderr": (pushed.stderr or "")[:MAX_OUTPUT_CHARS],
                "summary": f"Git release {branch}"}

    def http_request(self, args: Mapping[str, Any]) -> dict[str, Any]:
        url = args.get("url")
        method = str(args.get("method", "GET")).upper()
        if not isinstance(url, str) or not (url.startswith("http://") or
                                            url.startswith("https://")):
            raise ToolViolation("url 必须是 http/https")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ToolViolation("HTTP 方法非法")
        timeout = args.get("timeout_seconds", 30)
        response = httpx.request(
            method, url, json=args.get("json"), timeout=min(float(timeout), 60))
        text = response.text[:MAX_OUTPUT_CHARS]
        return {"ok": response.is_success, "status_code": response.status_code,
                "text": text, "summary": f"HTTP {method} {response.status_code}"}

    def execute(self, tool_name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        handlers = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "file_write": self.write_file,
            "delete_file": self.delete_file,
            "file_delete": self.delete_file,
            "run_command": self.run_command,
            "git_publish": self.git_publish,
            "http_request": self.http_request,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ToolViolation(f"未注册受控工具：{tool_name}")
        return handler(args)


def _summary(value: str) -> str:
    text = value.strip() or "工具执行完成"
    return text[:1000]


async def guarded_execute(hooks: TurnHooks, executor: ToolExecutor, *,
                          tool_name: str, args: dict[str, Any],
                          decision_class: str,
                          model_id: str | None = None,
                          model_params: str | None = None) -> dict[str, Any]:
    try:
        outcome: IntentOutcome = await hooks.guarded_call(
            tool_name, args, decision_class)
    except Denied as error:
        return {"ok": False, "denied": True, "summary": error.reason,
                "decision_id": error.decision_id}
    try:
        result = executor.execute(tool_name, outcome.args)
    except ToolError as error:
        await hooks.report_result(
            outcome.decision_id, outcome.attempt_id or "", "EXEC_FAILED",
            _summary(f"{tool_name} 执行失败：{error}"))
        return {"ok": False, "error": str(error), "summary": str(error),
                "decision_id": outcome.decision_id}
    evidence = {
        "model_id": model_id or os.environ.get("MODEL_NAME", "unknown"),
        "model_params": model_params or os.environ.get("MODEL_PARAMS", "unknown"),
        "verdict": "completed" if result.get("ok") else "failed",
    }
    if outcome.args_digest:
        evidence["input_digest"] = outcome.args_digest
    await hooks.report_result(
        outcome.decision_id, outcome.attempt_id or "", "EXEC_FINISHED",
        _summary(str(result.get("summary", ""))),
        evidence=evidence, output=result)
    return result


__all__ = [
    "MAX_FILE_BYTES",
    "ToolError",
    "ToolExecutor",
    "ToolViolation",
    "guarded_execute",
]
