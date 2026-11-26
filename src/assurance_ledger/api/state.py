"""应用状态：配置、单写者、事件存储、会话密钥、广播器"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import PlatformConfig
from ..ledger.decision_journal import DecisionJournal
from ..ledger.record_store import RecordStore
from ..ledger.redaction import Redactor
from ..ledger.sqlite_events import SQLiteEventStore
from ..ledger.writer import WriterQueue


@dataclass
class PlatformState:
    config: PlatformConfig
    writer: WriterQueue
    store: SQLiteEventStore
    journal: DecisionJournal
    record_store: RecordStore
    secret: bytes
    broker: Any
    redactor: Redactor
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    supervisor: Any = None
    recovery_task: Any = None

    def read_redactor(self, policy_configuration: dict[str, Any]) -> Redactor:
        version = policy_configuration.get("redaction_version", "redaction-v1")
        if version == self.redactor.version:
            return self.redactor
        return Redactor(version, self.redactor.patterns and
                        tuple(pattern.pattern for pattern in self.redactor.patterns))


__all__ = ["PlatformState"]
