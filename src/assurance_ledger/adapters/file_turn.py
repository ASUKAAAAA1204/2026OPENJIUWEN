"""One model turn with a registered workspace file tool, shared by the three SDK workers."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from ..ledger.canonical import digest_json, parse_json
from ..rail.model_config import DeepSeekSettings, ModelConfigError, load_deepseek_settings
from .base import AdapterUnavailable, TurnHooks, TurnRequest, TurnResult
from .tools import CommandLimits, ToolExecutor, guarded_execute

TOOL = "write_file"
PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Relative file path in the user's workspace"},
        "content": {"type": "string", "description": "Complete UTF-8 file content"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}
INSTRUCTIONS = (
    "You are a workspace file assistant. Answer without tools unless the user asks to write a file. "
    "For an explicit write, request write_file once with a relative path and the requested content. "
    "The host controls permission and reports the actual outcome. Do not claim a write has occurred."
)


@dataclass(frozen=True)
class ModelStep:
    model: str
    text: str
    call_id: str | None = None
    arguments: dict[str, str] | None = None


Choose = Callable[[], Awaitable[ModelStep]]
Write = Callable[[str, str], Awaitable[str]]
Dispatch = Callable[[str, Choose, Write], Awaitable[tuple[str | None, str]]]


class ModelTurnError(ValueError):
    """Known failure before a tool is allowed to run; message safe for the UI."""


def parse_step(completion) -> ModelStep:
    if len(completion.choices) != 1:
        raise ModelTurnError("模型返回了不支持的候选数量")
    item = completion.choices[0]
    calls = item.message.tool_calls or []
    text = item.message.content or ""
    if not calls and item.finish_reason == "stop" and text.strip():
        return ModelStep(completion.model, text)
    if len(calls) != 1 or item.finish_reason != "tool_calls":
        raise ModelTurnError("本轮只接受一个完整的文件工具请求")
    call = calls[0]
    if (call.type != "function" or call.function.name != TOOL or not isinstance(call.id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,192}", call.id)):
        raise ModelTurnError("模型返回了未登记的工具或无效的调用编号")
    try:
        args = parse_json(call.function.arguments)
    except (ValueError, TypeError):
        raise ModelTurnError("工具参数不是有效 JSON") from None
    if (not isinstance(args, dict) or set(args) != {"path", "content"}
            or not all(isinstance(v, str) for v in args.values()) or not args["path"].strip()):
        raise ModelTurnError("写入请求需要路径和文本内容")
    return ModelStep(completion.model, text, call.id, args)


async def deepseek_step(settings: DeepSeekSettings, task: str) -> ModelStep:
    import httpx
    from openai import APIError, AsyncOpenAI

    http = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=settings.timeout_seconds)
    async with AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url, http_client=http,
                           max_retries=0, timeout=settings.timeout_seconds) as client:
        try:
            completion = await client.chat.completions.create(
                model=settings.model, temperature=0, max_tokens=settings.max_tokens,
                messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": task}],
                tools=[{"type": "function", "function": {"name": TOOL, "description": "Write one workspace file",
                                                        "parameters": PARAMETERS}}],
                tool_choice="auto", extra_body={"thinking": {"type": "disabled"}},
            )
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            raise ModelTurnError(f"模型服务返回 HTTP {status}" if status else "模型服务连接失败") from None
    return parse_step(completion)


class FileAdapter:
    def __init__(self, name: str, version: str, root: Path,
                 plan: Callable[[str], Awaitable[ModelStep]], dispatch: Dispatch):
        self.name = name
        self.version = version
        self.root = root
        self.plan = plan
        self.dispatch = dispatch
        self.active: dict[str, asyncio.Task] = {}

    async def cancel(self, turn_id: str) -> None:
        task = self.active.get(turn_id)
        if task is not None:
            task.cancel()

    async def run_turn(self, request: TurnRequest, hooks: TurnHooks) -> TurnResult:
        if (request.framework != self.name or not request.task.strip() or not request.workspace_id
                or not Path(request.workspace_path).is_absolute()):
            return TurnResult("failed", "运行缺少任务或有效的授权工作目录")
        if request.turn_id in self.active:
            raise ValueError("同一轮次已在执行")
        limits = (CommandLimits(**request.command_limits)
                  if request.command_limits else CommandLimits())
        tool = ToolExecutor(
            request.workspace_path, self.root,
            allowed_remotes=tuple(request.git_allowed_remotes),
            allowed_branches=tuple(request.git_allowed_branches),
            limits=limits)
        step = None
        model_error = None
        entered = False
        receipt = None
        ok = False

        async def choose():
            nonlocal step, model_error
            if step is not None or model_error is not None:
                raise RuntimeError("模型步骤重复调用")
            try:
                step = await self.plan(request.task)
            except ModelTurnError as exc:
                model_error = exc
                raise
            return step

        async def write_file(path: str, content: str) -> str:
            """Write one UTF-8 file in the workspace after the central policy decision."""
            nonlocal entered, receipt, ok
            args = {"path": path, "content": content}
            if entered or step is None or not step.call_id or step.arguments != args:
                raise ValueError("SDK 工具调用与模型请求不一致")
            entered = True
            identity = [request.run_id, request.segment, request.turn_id, self.name, step.call_id]
            decision_id = "DEC-" + digest_json(identity).removeprefix("sha256:")
            result = await guarded_execute(
                hooks, tool, tool_name=TOOL, args=args, decision_class="file_write", decision_id=decision_id,
                model_id=step.model, model_params=json.dumps({"sdk_call_id": step.call_id, "temperature": 0}),
            )
            ok = result["ok"]
            receipt = ("written:" if ok else "blocked_or_failed:") + decision_id
            return receipt

        self.active[request.turn_id] = asyncio.current_task()
        try:
            try:
                native_id, native_result = await self.dispatch(request.turn_id, choose, write_file)
            except Exception:
                if model_error is not None:
                    return TurnResult("failed", str(model_error))
                raise
            if model_error is not None:
                return TurnResult("failed", str(model_error))
            if step is None or native_id != step.call_id:
                raise RuntimeError("SDK 回执无法关联到该轮模型响应")
            if step.call_id is None:
                if entered or native_result != step.text:
                    raise RuntimeError("无工具轮次的返回内容不匹配")
                message = "模型未请求文件操作，本轮没有修改文件。"
            else:
                if not entered or receipt is None or native_result != receipt:
                    raise RuntimeError("SDK 未返回该决策的执行回执")
                message = "文件写入成功，执行结果已记账。" if ok else "文件写入未获准或执行失败，请查看决策记录。"
            await hooks.emit("assistant_delta", {"text": message})
            return TurnResult("completed" if step.call_id is None or ok else "failed", message)
        finally:
            self.active.pop(request.turn_id, None)


def configured(name: str, distribution: str, required: str, dispatch: Dispatch) -> FileAdapter:
    version = metadata.version(distribution)
    if version != required:
        raise AdapterUnavailable(f"{name} 需要 {required}，检测到 {version}")
    root = os.environ.get("RUN_ROOT")
    if not root or not Path(root).is_absolute() or not Path(root).is_dir():
        raise AdapterUnavailable("RUN_ROOT 必须指向已存在的授权目录")
    env = dict(os.environ)
    for suffix in ("API_KEY", "BASE_URL", "NAME", "TIMEOUT_SECONDS", "MAX_TOKENS"):
        if env.get("MODEL_" + suffix):
            key = "MODEL" if suffix == "NAME" else suffix
            env["DEEPSEEK_" + key] = env["MODEL_" + suffix]
    try:
        settings = load_deepseek_settings(Path(env.get("MODEL_ENV_FILE") or os.devnull), environ=env)
    except ModelConfigError as exc:
        raise AdapterUnavailable(str(exc)) from None

    async def plan(task: str) -> ModelStep:
        return await deepseek_step(settings, task)

    return FileAdapter(name, version, Path(root), plan, dispatch)
