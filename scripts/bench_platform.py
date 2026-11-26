"""本机实测：账本持久化与纯规则 Gate 延迟（不作为生产 SLA）"""
from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.config import (  # noqa: E402
    PlatformConfig,
    SecurityConfig,
    ServerConfig,
    StorageConfig,
)
from assurance_ledger.gate.engine import evaluate  # noqa: E402
from assurance_ledger.ledger.migrations import apply_migrations  # noqa: E402
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore  # noqa: E402
from assurance_ledger.ledger.writer import WriterQueue  # noqa: E402
from assurance_ledger.policy.table import PolicyTable, default_configuration  # noqa: E402
from assurance_ledger.runs.lifecycle import seed_defaults  # noqa: E402


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="平台延迟本机实测")
    parser.add_argument("--writes", type=int, default=200)
    parser.add_argument("--gates", type=int, default=2000)
    parser.add_argument("--output", default=str(ROOT / "outputs/bench_platform.json"))
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="temis-bench-"))
    config = PlatformConfig(
        root=base, server=ServerConfig(public_origin="http://127.0.0.1"),
        storage=StorageConfig(
            database=base / "data/platform.db", outputs=base / "outputs",
            keys=base / "data/keys", backups=base / "data/backups"),
        security=SecurityConfig(allowed_root=base / "workspaces",
                                session_secret="s" * 32),
    )
    config.prepare_directories()
    writer = WriterQueue(config.database)

    def bootstrap(db) -> None:
        apply_migrations(db)
        seed_defaults(db, config)

    writer.start(bootstrap)
    store = SQLiteEventStore(config.database)
    persist = []
    try:
        for index in range(args.writes):
            started = time.perf_counter_ns()
            writer.call(lambda db: store.append_on(
                db, f"bench:{index}", {"index": index, "kind": "bench"}))
            persist.append((time.perf_counter_ns() - started) / 1e6)
    finally:
        writer.stop()
    policy = PolicyTable(
        version="bench", description="bench",
        published_at="2026-01-01T00:00:00Z", published_by="bench",
        eligible=True, reason="bench", configuration=default_configuration())
    gates = []
    for _ in range(args.gates):
        started = time.perf_counter_ns()
        evaluate(policy, decision_class="file_write", tool_name="write_file",
                 args={"path": "notes.txt", "content": "x"},
                 workspace=config.workspaces[0].path if config.workspaces
                 else config.allowed_root)
        gates.append((time.perf_counter_ns() - started) / 1e6)
    report = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os_cpu_count(),
            "note": "本机顺序写入，单写者；不代表生产 Linux/容器指标",
        },
        "intent_persist_ms": {
            "samples": len(persist),
            "p50": round(statistics.median(persist), 3),
            "p95": round(p95(persist), 3),
            "max": round(max(persist), 3),
        },
        "gate_ms": {
            "samples": len(gates),
            "p50": round(statistics.median(gates), 4),
            "p95": round(p95(gates), 4),
            "max": round(max(gates), 4),
        },
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def os_cpu_count() -> int:
    import os
    return os.cpu_count() or 0


if __name__ == "__main__":
    raise SystemExit(main())
