"""四值 Gate 引擎：纯规则、同步、不联网、不调用模型

输入策略表与工具意图，输出 ALLOW/ASK/DENY/REWRITE/NONE 之一。
NONE 用于未命中白名单的副作用动作（UNHOOKED），仍须记账但不进入规则判定。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..policy.table import PolicyTable

ALLOWED_COMMANDS = frozenset({
    "git", "python", "python3", "pytest", "npm", "node", "make",
    "rg", "ls", "cat", "pwd", "echo", "sed", "grep",
})
SHELL_META = frozenset("|&;<>()$`\n")
MAX_DELEGATION_DEPTH = 3


class GateError(ValueError):
    pass


@dataclass(frozen=True)
class GateResult:
    gate: str
    reason: str
    risk: str
    exec_policy: str
    final_gate: str | None = None
    args_digest: str | None = None
    rewritten_args: dict[str, Any] | None = None
    rule: dict[str, Any] | None = None
    declaration_available: bool | None = None
    parameters_match: bool | None = None
    convertible: bool | None = None
    timeout_allowed: bool | None = None

    def __post_init__(self):
        if self.final_gate is None:
            object.__setattr__(self, "final_gate", self.gate)

    def payload(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "final_gate": self.final_gate,
            "reason": self.reason,
            "risk": self.risk,
            "exec_policy": self.exec_policy,
            "rewritten_args": self.rewritten_args,
        }


def _deny(reason: str, risk: str = "high", exec_policy: str = "normal", *,
          declared: bool | None = True,
          match: bool | None = False,
          convertible: bool | None = False,
          timeout_allowed: bool | None = False) -> GateResult:
    return GateResult("DENY", reason, risk, exec_policy,
                      declaration_available=declared, parameters_match=match,
                      convertible=convertible, timeout_allowed=timeout_allowed)


def _unhooked(reason: str, risk: str = "mid") -> GateResult:
    return GateResult("NONE", reason, risk, "normal",
                      declaration_available=False, parameters_match=None,
                      convertible=None, timeout_allowed=False)


def resolve_within(root: Path, candidate: str) -> Path:
    if not isinstance(candidate, str) or not candidate.strip():
        raise GateError("路径不能为空")
    if "\x00" in candidate:
        raise GateError("路径含非法字符")
    if candidate.startswith("\\\\") or candidate.startswith("//"):
        raise GateError("不允许 UNC 路径")
    path = Path(candidate)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        parts = path.parts
        if ".." in parts:
            raise GateError("路径不允许包含 ..")
        resolved = (root / path).resolve()
    root_resolved = root.resolve()
    left = os.path.normcase(str(resolved))
    right = os.path.normcase(str(root_resolved))
    if left != right and not left.startswith(right + os.sep):
        raise GateError("路径越出受控工作目录")
    return resolved


def _validate_file(args: Mapping[str, Any], workspace: Path) -> tuple[Path | None, str | None]:
    target = args.get("path")
    if not isinstance(target, str):
        return None, "文件操作缺少 path"
    try:
        resolved = resolve_within(workspace, target)
    except (GateError, OSError, ValueError) as error:
        return None, str(error)
    return resolved, None


def _validate_exec(args: Mapping[str, Any], workspace: Path) -> tuple[list[str] | None, str | None]:
    argv = args.get("argv")
    if isinstance(argv, str):
        return None, "命令必须使用结构化 argv，禁止 shell 字符串"
    if not isinstance(argv, list) or not argv or not all(
            isinstance(item, str) and item for item in argv):
        return None, "argv 必须是非空字符串数组"
    if any(any(ch in SHELL_META for ch in item) for item in argv):
        return None, "命令参数含 shell 元字符，按不可判定处理"
    binary = Path(argv[0]).name
    if binary not in ALLOWED_COMMANDS:
        return None, f"命令 {binary} 不在允许清单"
    cwd = args.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str):
            return None, "cwd 必须是字符串"
        try:
            resolve_within(workspace, cwd)
        except (GateError, OSError, ValueError) as error:
            return None, f"cwd 非法：{error}"
    return list(argv), None


def _validate_network(args: Mapping[str, Any]) -> str | None:
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        return "网络写入缺少 url"
    if not (url.startswith("http://") or url.startswith("https://")):
        return "url 必须是 http/https"
    return None


def _validate_vcs(args: Mapping[str, Any]) -> str | None:
    operation = args.get("operation")
    if operation not in ("push", "tag", "release"):
        return "Git 操作必须是 push/tag/release"
    if args.get("force") is True:
        return "禁止强制推送"
    return None


def _apply_rewrite(args: Mapping[str, Any], rule: Mapping[str, Any]) -> dict[str, Any]:
    rewritten = dict(args)
    drop = set(rule.get("rewrite", {}).get("drop_flags", []))
    if drop:
        argv = rewritten.get("argv")
        if isinstance(argv, list):
            rewritten["argv"] = [item for item in argv if item not in drop]
        flags = rewritten.get("flags")
        if isinstance(flags, list):
            rewritten["flags"] = [item for item in flags if item not in drop]
    if rewritten.get("force") is True:
        rewritten["force"] = False
    return rewritten


def evaluate(policy: PolicyTable, *, decision_class: str, tool_name: str,
             args: Mapping[str, Any], workspace: Path,
             delegation_depth: int = 1,
             side_effect: bool = True) -> GateResult:
    if not isinstance(args, Mapping):
        raise GateError("args 必须是对象")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise GateError("tool_name 必填")
    if not side_effect:
        return _unhooked("只读调用，不计入关键决策", risk="low")
    whitelist = policy.whitelist
    if decision_class not in whitelist.classes:
        return _unhooked(f"未命中白名单决策类 {decision_class!r}")
    rule = policy.rule_for(decision_class)
    if rule is None:
        return _deny(f"策略表缺少决策类 {decision_class} 的规则",
                     declared=False, match=None)
    tool = whitelist.tool(tool_name)
    if tool is None:
        return _unhooked(f"工具 {tool_name} 未登记，按未覆盖动作记录")
    if tool.decision_class is not None and tool.decision_class != decision_class:
        return _deny(f"工具 {tool_name} 登记为 {tool.decision_class}，与声明 {decision_class} 不符")
    risk = rule["risk"]
    gate = rule["gate"]
    exec_policy = rule["exec_policy"]

    if exec_policy == "degrade" and tool.capability != "read_only":
        return _deny(f"工具 {tool_name} 未声明只读能力，不能降级执行", risk=risk,
                     exec_policy=exec_policy, match=True)

    if decision_class in ("file_write", "file_delete"):
        _, error = _validate_file(args, workspace)
        if error:
            return _deny(error, risk=risk, match=False, convertible=True)
    elif decision_class == "exec":
        _, error = _validate_exec(args, workspace)
        if error:
            return _deny(error, risk=risk, match=False, convertible=False)
    elif decision_class == "network_write":
        error = _validate_network(args)
        if error:
            return _deny(error, risk=risk, match=False, convertible=False)
    elif decision_class == "vcs_publish":
        error = _validate_vcs(args)
        if error:
            if gate == "REWRITE" and error == "禁止强制推送":
                pass  # 改写后重新判定
            else:
                return _deny(error, risk=risk, match=False, convertible=True)
    elif decision_class == "delegate":
        if delegation_depth > MAX_DELEGATION_DEPTH:
            return _deny("委派深度超过 3", risk=risk, match=True)

    if gate == "REWRITE":
        if "rewrite" not in rule:
            return _deny("策略要求改写但未配置改写规则", risk=risk, match=True)
        rewritten = _apply_rewrite(args, rule)
        if rewritten == dict(args):
            return GateResult("REWRITE", "参数无需改写，按原参数放行", risk, exec_policy,
                              final_gate="ALLOW", rule=rule,
                              declaration_available=True, parameters_match=True,
                              convertible=True, timeout_allowed=False)
        if decision_class == "exec":
            _, error = _validate_exec(rewritten, workspace)
            if error:
                return _deny(f"改写后仍不可执行：{error}", risk=risk, match=False)
        if decision_class == "vcs_publish":
            error = _validate_vcs(rewritten)
            if error:
                return _deny(f"改写后仍不可发布：{error}", risk=risk, match=False)
        return GateResult("REWRITE", "改写后重新判定通过", risk, exec_policy,
                          final_gate="ALLOW", rewritten_args=rewritten, rule=rule,
                          declaration_available=True, parameters_match=True,
                          convertible=True, timeout_allowed=False)

    if gate == "ALLOW":
        if decision_class == "exec":
            argv, _ = _validate_exec(args, workspace)
            assert argv is not None
            if any(item.startswith("-") and item in ("-c", "--eval") for item in argv):
                return GateResult("ASK", "命令参数风险不可判定，转人工", risk,
                                  exec_policy, rule=rule, declaration_available=True,
                                  parameters_match=False, convertible=True,
                                  timeout_allowed=False)
        if decision_class == "network_write" and not args.get("host_allowlisted"):
            return GateResult("ASK", "目标主机未在允许清单，转人工", risk,
                              exec_policy, rule=rule, declaration_available=True,
                              parameters_match=True, convertible=True,
                              timeout_allowed=False)
    return GateResult(gate, f"策略 {policy.version} 判定 {gate}", risk,
                      exec_policy, rule=rule, declaration_available=True,
                      parameters_match=True, convertible=True,
                      timeout_allowed=(gate == "ASK" and rule.get("ask_timeout_policy") == "ALLOW"))


__all__ = [
    "ALLOWED_COMMANDS",
    "GateError",
    "GateResult",
    "MAX_DELEGATION_DEPTH",
    "evaluate",
    "resolve_within",
]
