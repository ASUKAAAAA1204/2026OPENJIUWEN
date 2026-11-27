"""可选 OpenTelemetry 接入；默认关闭，缺依赖时导入分支直接跳过

Span 属性只放枚举与摘要，不放原始参数、token 或完整输出。
"""
from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

_TRACER: Any = None


def enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get("TEMIS_OTEL_ENABLED", "").lower() in ("1", "true", "yes")


def init_otel(service_name: str = "temisguard") -> Any:
    global _TRACER
    if not enabled():
        return None
    if _TRACER is not None:
        return _TRACER
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return None
    provider = TracerProvider(resource=Resource.create(
        {"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("temisguard")
    return _TRACER


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    tracer = _TRACER
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name, attributes=attributes) as current:
        yield current


def reset() -> None:
    global _TRACER
    _TRACER = None


__all__ = ["enabled", "init_otel", "reset", "span"]
