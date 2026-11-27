"""受控工具：允许根内文件、结构化命令与 Git 发布

这里不判断策略；调用方必须先通过中央 Gate。所有路径在真实绝对路径上校验，
命令禁止 shell，Git 发布先 dry-run。未注册动作返回明确错误。
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..adapters.base import Denied, IntentOutcome, TurnHooks
from ..gate.engine import GateError, resolve_within

MAX_FILE_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 200_000
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")


@dataclass(frozen=True)
class CommandLimits:
    cpu_seconds: int = 10
    memory_mb: int = 512
    file_size_mb: int = 16
    open_files: int = 128


def _matches(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _posix_limits(limits: CommandLimits):
    def apply() -> None:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,
                           (limits.cpu_seconds, limits.cpu_seconds))
        memory = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        size = limits.file_size_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
        resource.setrlimit(resource.RLIMIT_NOFILE,
                           (limits.open_files, limits.open_files))
    return apply


def _windows_job_limits(process: subprocess.Popen, limits: CommandLimits) -> int | None:
    """best-effort Job Object；不可用时返回 None，调用方记为 partial"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _IoCounters(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class _BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BasicLimit),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        limit_process_memory = 0x00000200
        limit_active_process = 0x00000008
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = (
            limit_process_memory | limit_active_process)
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = limits.memory_mb * 1024 * 1024
        sized = kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not sized:
            kernel32.CloseHandle(job)
            return None
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            kernel32.CloseHandle(job)
            return None
        assigned = kernel32.AssignProcessToJobObject(job, handle)
        kernel32.CloseHandle(handle)
        if not assigned:
            kernel32.CloseHandle(job)
            return None
        return int(job)
    except Exception:
        return None


def _close_job(job: int) -> None:
    try:
        import ctypes
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
    except Exception:
        pass


class ToolError(RuntimeError):
    pass


class ToolViolation(ToolError):
    pass


def _safe_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP",
            "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
    return {key: value for key, value in os.environ.items() if key in keep}


class ToolExecutor:
    def __init__(self, workspace: str | Path, allowed_root: str | Path, *,
                 allowed_remotes: tuple[str, ...] = (),
                 allowed_branches: tuple[str, ...] = (),
                 limits: CommandLimits | None = None):
        self.workspace = Path(workspace).resolve()
        self.allowed_root = Path(allowed_root).resolve()
        self.allowed_remotes = tuple(allowed_remotes)
        self.allowed_branches = tuple(allowed_branches)
        self.limits = limits or CommandLimits()
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
        preexec = _posix_limits(self.limits) if os.name == "posix" else None
        process = subprocess.Popen(
            argv, cwd=cwd, env=_safe_env(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, shell=False,
            preexec_fn=preexec)
        job = _windows_job_limits(process, self.limits)
        limit_state = "full" if os.name != "nt" or job is not None else "partial"
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                    "summary": "资源限制：命令超时",
                    "command_limits": limit_state}
        finally:
            if job is not None:
                _close_job(job)
        code = process.returncode
        stdout = (stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (stderr or "")[:MAX_OUTPUT_CHARS]
        if code is not None and code < 0:
            summary = "资源限制：命令被系统终止"
        else:
            summary = (f"命令退出码 {code}；stdout {len(stdout)} 字符，"
                       f"stderr {len(stderr)} 字符")
        return {
            "ok": code == 0,
            "exit_code": code,
            "stdout": stdout,
            "stderr": stderr,
            "summary": summary,
            "command_limits": limit_state,
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
        if not _matches(remote, self.allowed_remotes):
            raise ToolViolation("GIT_REMOTE_NOT_ALLOWED")
        if not _matches(branch, self.allowed_branches):
            raise ToolViolation("GIT_BRANCH_NOT_ALLOWED")
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
                          decision_id: str | None = None,
                          model_id: str | None = None,
                          model_params: str | None = None) -> dict[str, Any]:
    try:
        if decision_id is None:
            outcome: IntentOutcome = await hooks.guarded_call(tool_name, args, decision_class)
        else:
            outcome = await hooks.guarded_call(tool_name, args, decision_class, decision_id=decision_id)
    except Denied as error:
        return {"ok": False, "denied": True, "summary": error.reason,
                "decision_id": error.decision_id}
    if not outcome.execute or not outcome.attempt_id or (decision_id and outcome.decision_id != decision_id):
        raise ToolViolation("中央服务未提供绑定该决策的执行许可")
    try:
        result = executor.execute(tool_name, outcome.args)
    except (OSError, ToolError) as error:
        message = str(error) if isinstance(error, ToolError) else type(error).__name__
        await hooks.report_result(
            outcome.decision_id, outcome.attempt_id, "EXEC_FAILED",
            _summary(f"{tool_name} 执行失败：{message}"))
        return {"ok": False, "error": message, "summary": message,
                "decision_id": outcome.decision_id}
    evidence = {
        "model_id": model_id or os.environ.get("MODEL_NAME", "unknown"),
        "model_params": model_params or os.environ.get("MODEL_PARAMS", "unknown"),
        "verdict": "completed" if result.get("ok") else "failed",
    }
    if outcome.args_digest:
        evidence["input_digest"] = outcome.args_digest
    await hooks.report_result(
        outcome.decision_id, outcome.attempt_id, "EXEC_FINISHED" if result.get("ok") else "EXEC_FAILED",
        _summary(str(result.get("summary", ""))),
        evidence=evidence, output=result)
    return result


__all__ = [
    "MAX_FILE_BYTES",
    "CommandLimits",
    "ToolError",
    "ToolExecutor",
    "ToolViolation",
    "guarded_execute",
]
