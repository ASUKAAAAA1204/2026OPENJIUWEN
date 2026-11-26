"""决策白名单与工具能力登记：11 类决策、只读/混合/写能力"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

DECISION_CLASSES = (
    "file_write", "file_delete", "exec", "network_write", "vcs_publish",
    "policy_decision", "human_ack", "delegate", "abort", "run_close",
    "final_claim",
)
CAPABILITIES = ("read_only", "mixed", "write")
WHITELIST_VERSION = "whitelist-v1"


class WhitelistError(ValueError):
    pass


@dataclass(frozen=True)
class ToolEntry:
    name: str
    capability: str
    workspace: str
    decision_class: str | None = None

    def payload(self) -> dict[str, Any]:
        item = {"name": self.name, "capability": self.capability,
                "workspace": self.workspace}
        if self.decision_class is not None:
            item["decision_class"] = self.decision_class
        return item


def default_tools() -> tuple[ToolEntry, ...]:
    return (
        ToolEntry("read_file", "read_only", "workspace", None),
        ToolEntry("write_file", "write", "workspace", "file_write"),
        ToolEntry("delete_file", "write", "workspace", "file_delete"),
        ToolEntry("run_command", "mixed", "workspace", "exec"),
        ToolEntry("http_request", "write", "workspace", "network_write"),
        ToolEntry("git_publish", "write", "workspace", "vcs_publish"),
        ToolEntry("delegate_task", "mixed", "workspace", "delegate"),
        ToolEntry("ask_human", "mixed", "workspace", "human_ack"),
        ToolEntry("abort_run", "write", "workspace", "abort"),
        ToolEntry("close_run", "write", "workspace", "run_close"),
        ToolEntry("final_claim", "write", "workspace", "final_claim"),
        ToolEntry("set_policy", "write", "workspace", "policy_decision"),
    )


@dataclass(frozen=True)
class Whitelist:
    version: str
    classes: tuple[str, ...]
    tools: tuple[ToolEntry, ...]

    def tool(self, name: str) -> ToolEntry | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "classes": list(self.classes),
            "tools": [tool.payload() for tool in self.tools],
        }


def default_whitelist() -> Whitelist:
    return Whitelist(version=WHITELIST_VERSION, classes=DECISION_CLASSES,
                     tools=default_tools())


def parse_whitelist(value: Any) -> Whitelist:
    if not isinstance(value, Mapping):
        raise WhitelistError("whitelist 必须是对象")
    version = value.get("version")
    classes = value.get("classes")
    tools = value.get("tools")
    if not isinstance(version, str) or not version.strip():
        raise WhitelistError("whitelist.version 必填")
    if not isinstance(classes, list) or not classes or not all(
            isinstance(item, str) and item in DECISION_CLASSES for item in classes):
        raise WhitelistError("whitelist.classes 必须是 11 类决策的子集")
    if len(set(classes)) != len(classes):
        raise WhitelistError("whitelist.classes 不能重复")
    if not isinstance(tools, list) or not tools:
        raise WhitelistError("whitelist.tools 必须非空")
    parsed: list[ToolEntry] = []
    for item in tools:
        if not isinstance(item, Mapping):
            raise WhitelistError("whitelist.tools 项必须是对象")
        name = item.get("name")
        capability = item.get("capability")
        workspace = item.get("workspace")
        decision_class = item.get("decision_class")
        if not isinstance(name, str) or not name.strip():
            raise WhitelistError("工具名必填")
        if capability not in CAPABILITIES:
            raise WhitelistError(f"工具 {name} 能力非法")
        if not isinstance(workspace, str) or not workspace.strip():
            raise WhitelistError(f"工具 {name} 缺少工作目录")
        if decision_class is not None and decision_class not in DECISION_CLASSES:
            raise WhitelistError(f"工具 {name} 决策类非法")
        parsed.append(ToolEntry(name=name, capability=capability,
                                workspace=workspace, decision_class=decision_class))
    if len({tool.name for tool in parsed}) != len(parsed):
        raise WhitelistError("工具名不能重复")
    return Whitelist(version=version, classes=tuple(classes),
                     tools=tuple(sorted(parsed, key=lambda tool: tool.name)))


__all__ = [
    "CAPABILITIES",
    "DECISION_CLASSES",
    "ToolEntry",
    "WHITELIST_VERSION",
    "Whitelist",
    "WhitelistError",
    "default_tools",
    "default_whitelist",
    "parse_whitelist",
]
