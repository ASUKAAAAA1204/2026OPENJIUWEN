"""策略表：只读、版本化、内容寻址；规则纯数据，不含网络或模型"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ..ledger.canonical import canonical_bytes
from .whitelist import (
    DECISION_CLASSES,
    Whitelist,
    default_whitelist,
    parse_whitelist,
)

POLICY_SCHEMA_VERSION = "temisguard-policy-v1"
DEFAULT_POLICY_VERSION = "policy-v1"
DEFAULT_POLICY_DESCRIPTION = "默认策略：高风险动作转人工，低风险动作放行"
REDACTION_VERSION = "redaction-v1"
DEFAULT_DEBT_THRESHOLD = 6.0
GATE_VALUES = ("ALLOW", "ASK", "DENY", "REWRITE")
EXEC_VALUES = ("normal", "degrade")
RISKS = ("high", "mid", "low")


class PolicyError(ValueError):
    pass


def _rule(decision_class: str, gate: str, exec_policy: str, risk: str,
          *, ask_timeout_seconds: int | None = None,
          ask_timeout_policy: str | None = None,
          rewrite: Mapping[str, Any] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "decision_class": decision_class,
        "gate": gate,
        "exec_policy": exec_policy,
        "risk": risk,
        "ask_timeout_seconds": ask_timeout_seconds,
        "ask_timeout_policy": ask_timeout_policy,
    }
    if rewrite is not None:
        item["rewrite"] = dict(rewrite)
    return item


def default_configuration() -> dict[str, Any]:
    whitelist = default_whitelist()
    return normalize_configuration({
        "schema_version": POLICY_SCHEMA_VERSION,
        "redaction_version": REDACTION_VERSION,
        "debt_review_threshold": DEFAULT_DEBT_THRESHOLD,
        "whitelist": whitelist.payload(),
        "rules": [
            _rule("file_write", "ASK", "normal", "high", ask_timeout_seconds=30,
                  ask_timeout_policy="DENY"),
            _rule("file_delete", "ASK", "normal", "high", ask_timeout_seconds=30,
                  ask_timeout_policy="DENY"),
            _rule("exec", "ASK", "normal", "high", ask_timeout_seconds=30,
                  ask_timeout_policy="DENY"),
            _rule("network_write", "ASK", "normal", "high", ask_timeout_seconds=30,
                  ask_timeout_policy="DENY"),
            _rule("vcs_publish", "ASK", "normal", "high", ask_timeout_seconds=30,
                  ask_timeout_policy="DENY"),
            _rule("policy_decision", "DENY", "normal", "high"),
            _rule("human_ack", "ALLOW", "normal", "low"),
            _rule("delegate", "ALLOW", "normal", "mid"),
            _rule("abort", "ALLOW", "normal", "low"),
            _rule("run_close", "ALLOW", "normal", "low"),
            _rule("final_claim", "ALLOW", "normal", "mid"),
        ],
    })


def normalize_configuration(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyError("策略配置必须是对象")
    schema = value.get("schema_version", POLICY_SCHEMA_VERSION)
    if schema != POLICY_SCHEMA_VERSION:
        raise PolicyError(f"不支持的策略 schema：{schema!r}")
    redaction = value.get("redaction_version")
    if not isinstance(redaction, str) or not redaction.strip():
        raise PolicyError("redaction_version 必填")
    threshold = value.get("debt_review_threshold", DEFAULT_DEBT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise PolicyError("debt_review_threshold 必须是数字")
    if threshold != threshold or threshold in (float("inf"), float("-inf")) or threshold < 0:
        raise PolicyError("debt_review_threshold 必须是非负有限数字")
    whitelist = parse_whitelist(value.get("whitelist"))
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list):
        raise PolicyError("rules 必须是数组")
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            raise PolicyError("规则必须是对象")
        decision_class = raw.get("decision_class")
        if decision_class not in DECISION_CLASSES:
            raise PolicyError(f"未知决策类：{decision_class!r}")
        if decision_class in seen:
            raise PolicyError(f"决策类规则重复：{decision_class}")
        seen.add(decision_class)
        gate = raw.get("gate")
        if gate not in GATE_VALUES:
            raise PolicyError(f"{decision_class} gate 非法")
        exec_policy = raw.get("exec_policy")
        if exec_policy not in EXEC_VALUES:
            raise PolicyError(f"{decision_class} exec_policy 非法")
        risk = raw.get("risk")
        if risk not in RISKS:
            raise PolicyError(f"{decision_class} risk 非法")
        seconds = raw.get("ask_timeout_seconds")
        policy = raw.get("ask_timeout_policy")
        if gate == "ASK":
            if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
                raise PolicyError(f"{decision_class} ASK 超时秒数必须是正整数")
            if policy not in ("ALLOW", "DENY"):
                raise PolicyError(f"{decision_class} ASK 超时处置必须是 ALLOW/DENY")
            if risk in ("high", "mid") and policy != "DENY":
                raise PolicyError(f"{decision_class} 高危/中危超时不得放行")
        else:
            if seconds is not None or policy is not None:
                raise PolicyError(f"{decision_class} 非 ASK 规则不得设置超时字段")
        rule: dict[str, Any] = {
            "decision_class": decision_class,
            "gate": gate,
            "exec_policy": exec_policy,
            "risk": risk,
            "ask_timeout_seconds": seconds,
            "ask_timeout_policy": policy,
        }
        rewrite = raw.get("rewrite")
        if rewrite is not None:
            if gate != "REWRITE":
                raise PolicyError(f"{decision_class} 只有 REWRITE 规则可配置改写")
            if not isinstance(rewrite, Mapping):
                raise PolicyError(f"{decision_class} rewrite 必须是对象")
            drop = rewrite.get("drop_flags", [])
            if not isinstance(drop, list) or not all(
                    isinstance(item, str) and item for item in drop):
                raise PolicyError(f"{decision_class} rewrite.drop_flags 必须是字符串数组")
            rule["rewrite"] = {"drop_flags": sorted(set(drop))}
        rules.append(rule)
    unknown = set(value) - {
        "schema_version", "redaction_version", "debt_review_threshold",
        "whitelist", "rules",
    }
    if unknown:
        raise PolicyError(f"策略配置含未知字段：{sorted(unknown)}")
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "redaction_version": redaction,
        "debt_review_threshold": float(threshold),
        "whitelist": whitelist.payload(),
        "rules": sorted(rules, key=lambda item: item["decision_class"]),
    }


def content_hash(configuration: Mapping[str, Any]) -> str:
    normalized = normalize_configuration(configuration)
    return hashlib.sha256(canonical_bytes(normalized)).hexdigest()


@dataclass(frozen=True)
class PolicyTable:
    version: str
    description: str
    published_at: str
    published_by: str
    eligible: bool
    reason: str
    configuration: dict[str, Any]

    @property
    def content_hash(self) -> str:
        return content_hash(self.configuration)

    @property
    def whitelist(self) -> Whitelist:
        return parse_whitelist(self.configuration["whitelist"])

    @property
    def rules(self) -> dict[str, dict[str, Any]]:
        return {rule["decision_class"]: rule
                for rule in self.configuration["rules"]}

    @property
    def debt_threshold(self) -> float:
        return float(self.configuration["debt_review_threshold"])

    def rule_for(self, decision_class: str) -> dict[str, Any] | None:
        return self.rules.get(decision_class)

    def payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_hash": self.content_hash,
            "description": self.description,
            "published_at": self.published_at,
            "published_by": self.published_by,
            "eligible": self.eligible,
            "reason": self.reason,
            "configuration": json.loads(json.dumps(self.configuration)),
        }

    @classmethod
    def from_row(cls, row: Any) -> "PolicyTable":
        return cls(
            version=row["version"],
            description=row["description"],
            published_at=row["published_at"],
            published_by=row["published_by"],
            eligible=bool(row["eligible"]),
            reason=row["reason"],
            configuration=normalize_configuration(json.loads(row["configuration"])),
        )


__all__ = [
    "DEFAULT_DEBT_THRESHOLD",
    "DEFAULT_POLICY_DESCRIPTION",
    "DEFAULT_POLICY_VERSION",
    "EXEC_VALUES",
    "GATE_VALUES",
    "POLICY_SCHEMA_VERSION",
    "PolicyError",
    "PolicyTable",
    "RISKS",
    "content_hash",
    "default_configuration",
    "normalize_configuration",
]
