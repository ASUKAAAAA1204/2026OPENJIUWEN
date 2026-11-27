"""无第三方依赖的计数器/仪表/直方图与 Prometheus 文本输出

标签只允许有限枚举；禁止把 run_id、decision_id、claim_id 放进标签。
"""
from __future__ import annotations

from typing import Any, Mapping

BUCKETS_MS = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 250.0, 500.0, 1000.0)
FRAMEWORKS = ("openJiuwen", "LangGraph", "OpenAI Agents SDK")
GATES = ("ALLOW", "ASK", "DENY", "REWRITE", "NONE")
DECISION_CLASSES = (
    "file_write", "file_delete", "exec", "network_write", "vcs_publish",
    "policy_decision", "human_ack", "delegate", "abort", "run_close",
    "final_claim",
)
RESULT_STATUSES = ("completed", "failed", "unknown", "cancelled")
APPROVAL_RESULTS = ("approved", "rejected", "timed_out", "cancelled", "pending")
UNKNOWN_REASONS = ("heartbeat_lost", "shutdown", "worker_stopped",
                   "adapter_error")
CHAIN_SOURCES = ("audit", "export", "verify")

ALLOWED_LABELS: dict[str, dict[str, tuple[str, ...]]] = {
    "temisguard_intent_persist_ms": {"framework": FRAMEWORKS},
    "temisguard_result_persist_ms": {"status": RESULT_STATUSES},
    "temisguard_gate_ms": {"decision_class": DECISION_CLASSES, "gate": GATES},
    "temisguard_sse_connections": {},
    "temisguard_worker_restarts_total": {"framework": FRAMEWORKS},
    "temisguard_unknown_outcomes_total": {"reason": UNKNOWN_REASONS},
    "temisguard_approval_wait_ms": {"result": APPROVAL_RESULTS},
    "temisguard_writer_queue_depth": {},
    "temisguard_ledger_chain_verify_failures_total": {"source": CHAIN_SOURCES},
}


def _key(labels: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _validate(name: str, labels: Mapping[str, Any] | None) -> None:
    allowed = ALLOWED_LABELS[name]
    provided = dict(labels or {})
    if set(provided) != set(allowed):
        raise ValueError(f"{name} 标签集合必须为 {sorted(allowed)}")
    for key, values in allowed.items():
        if str(provided[key]) not in values:
            raise ValueError(f"{name} 标签 {key}={provided[key]!r} 不在有限枚举内")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


def _render_labels(key: tuple[tuple[str, str], ...],
                   extra: tuple[str, str] | None = None) -> str:
    items = list(key)
    if extra is not None:
        items.append(extra)
    if not items:
        return ""
    body = ",".join(f'{name}="{_escape(value)}"' for name, value in items)
    return "{" + body + "}"


class Counter:
    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self._data: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(self, labels: Mapping[str, Any] | None = None,
            value: float = 1.0) -> None:
        _validate(self.name, labels)
        key = _key(labels)
        self._data[key] = self._data.get(key, 0.0) + float(value)

    def value(self, labels: Mapping[str, Any] | None = None) -> float:
        return self._data.get(_key(labels), 0.0)

    def lines(self) -> list[str]:
        output = [f"# HELP {self.name} {self.help_text}",
                  f"# TYPE {self.name} counter"]
        for key, value in sorted(self._data.items()):
            output.append(f"{self.name}{_render_labels(key)} {value:g}")
        return output


class Gauge:
    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self._data: dict[tuple[tuple[str, str], ...], float] = {}

    def set(self, value: float,
            labels: Mapping[str, Any] | None = None) -> None:
        _validate(self.name, labels)
        self._data[_key(labels)] = float(value)

    def inc(self, value: float = 1.0,
            labels: Mapping[str, Any] | None = None) -> None:
        _validate(self.name, labels)
        key = _key(labels)
        self._data[key] = self._data.get(key, 0.0) + float(value)

    def dec(self, value: float = 1.0,
            labels: Mapping[str, Any] | None = None) -> None:
        self.inc(-value, labels)

    def value(self, labels: Mapping[str, Any] | None = None) -> float:
        return self._data.get(_key(labels), 0.0)

    def lines(self) -> list[str]:
        output = [f"# HELP {self.name} {self.help_text}",
                  f"# TYPE {self.name} gauge"]
        for key, value in sorted(self._data.items()):
            output.append(f"{self.name}{_render_labels(key)} {value:g}")
        return output


class Histogram:
    def __init__(self, name: str, help_text: str,
                 buckets: tuple[float, ...] = BUCKETS_MS):
        self.name = name
        self.help_text = help_text
        self.buckets = tuple(sorted(buckets))
        self._data: dict[tuple[tuple[str, str], ...],
                         tuple[list[int], float, int]] = {}

    def observe(self, milliseconds: float,
                labels: Mapping[str, Any] | None = None) -> None:
        _validate(self.name, labels)
        key = _key(labels)
        counts, total, count = self._data.get(
            key, ([0] * len(self.buckets), 0.0, 0))
        value = float(milliseconds)
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                counts[index] += 1
                break
        self._data[key] = (counts, total + value, count + 1)

    def lines(self) -> list[str]:
        output = [f"# HELP {self.name} {self.help_text}",
                  f"# TYPE {self.name} histogram"]
        for key, (counts, total, count) in sorted(self._data.items()):
            cumulative = 0
            for index, bound in enumerate(self.buckets):
                cumulative += counts[index]
                output.append(
                    f"{self.name}_bucket"
                    f"{_render_labels(key, ('le', _bound(bound)))} {cumulative}")
            output.append(
                f"{self.name}_bucket"
                f"{_render_labels(key, ('le', '+Inf'))} {count}")
            output.append(f"{self.name}_sum{_render_labels(key)} {total:g}")
            output.append(f"{self.name}_count{_render_labels(key)} {count}")
        return output


def _bound(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


class Registry:
    def __init__(self) -> None:
        self._metrics: list[Counter | Gauge | Histogram] = []

    def counter(self, name: str, help_text: str) -> Counter:
        metric = Counter(name, help_text)
        self._metrics.append(metric)
        return metric

    def gauge(self, name: str, help_text: str) -> Gauge:
        metric = Gauge(name, help_text)
        self._metrics.append(metric)
        return metric

    def histogram(self, name: str, help_text: str,
                  buckets: tuple[float, ...] = BUCKETS_MS) -> Histogram:
        metric = Histogram(name, help_text, buckets)
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics:
            lines.extend(metric.lines())
        return "\n".join(lines) + "\n"


class Metrics:
    """服务端指标集；标签只取枚举，数值不来自用户输入"""

    def __init__(self) -> None:
        self.registry = Registry()
        self.intent_persist_ms = self.registry.histogram(
            "temisguard_intent_persist_ms", "完整意图事务耗时（毫秒）")
        self.result_persist_ms = self.registry.histogram(
            "temisguard_result_persist_ms", "结果落账耗时（毫秒）")
        self.gate_ms = self.registry.histogram(
            "temisguard_gate_ms", "纯规则判定耗时（毫秒）")
        self.sse_connections = self.registry.gauge(
            "temisguard_sse_connections", "当前 SSE 订阅数")
        self.worker_restarts = self.registry.counter(
            "temisguard_worker_restarts_total", "worker supervisor 重启次数")
        self.unknown_outcomes = self.registry.counter(
            "temisguard_unknown_outcomes_total", "结果未知的收尾次数")
        self.approval_wait_ms = self.registry.histogram(
            "temisguard_approval_wait_ms", "审批长轮询耗时（毫秒）")
        self.writer_queue_depth = self.registry.gauge(
            "temisguard_writer_queue_depth", "单写者队列深度")
        self.chain_verify_failures = self.registry.counter(
            "temisguard_ledger_chain_verify_failures_total",
            "账本链校验失败次数")

    def render(self) -> str:
        return self.registry.render()

    def set_writer_depth(self, depth: int) -> None:
        self.writer_queue_depth.set(depth)


METRICS = Metrics()

__all__ = [
    "ALLOWED_LABELS",
    "BUCKETS_MS",
    "Counter",
    "Gauge",
    "Histogram",
    "METRICS",
    "Metrics",
    "Registry",
]
