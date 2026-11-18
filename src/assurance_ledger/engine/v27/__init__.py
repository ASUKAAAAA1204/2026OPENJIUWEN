"""TemisGuard v2.7 引擎（板块② B）—— 公开 API。

独立增量计算包：使用标准库 + rfc8785，不调用 legacy engine
（ars_engine/signals 等）的旧计算逻辑。口径版本 temisguard-b-v2.7。

用法（示意）：

    from assurance_ledger.engine.v27 import RunFacts, compute_run, aggregate_skill
    output = compute_run(run_facts)
    skill_report = aggregate_skill([run_facts_a, run_facts_b, ...])
"""

from .models import (
    DEBT_WEIGHT,
    DISPOSITIONS,
    GATED,
    GATES,
    MARK_TYPES,
    RESOLUTION_METHOD,
    RISKS,
    SEVERITIES,
    SEVERITY_RANK,
    VERSION,
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    PolicyObservation,
    ReplayOutcome,
    RunFacts,
    RunReference,
    content_hash,
    ensure_json_friendly,
    make_debt_id,
    require_bool,
    require_enum,
    require_finite_number,
    require_id,
    require_timestamp,
    ts_to_str,
    validate_debt_item,
    validate_evidence_ref,
)
from .marks import (
    MAX_PROPAGATION_HOPS,
    Mark,
    MarkReport,
    distinct_marked_decision_ids,
    drop_severity,
    generate_marks,
    orphan_decision_id,
)
from .debt import DebtProjection, EvidenceRef, merge_debt_items
from .readings import (
    FRESHNESS_WINDOW,
    MAX_SAMPLE_RUNS,
    MIN_CLASSES,
    MIN_SAMPLE_RUNS,
    RATE_NAMES,
    Rate,
    aggregate_skill,
    compute_run,
    open_debts_for_run,
)

__all__ = [
    "VERSION",
    "DecisionFact",
    "EvidenceRecord",
    "ReplayOutcome",
    "PolicyObservation",
    "DispositionFact",
    "RunReference",
    "RunFacts",
    "Mark",
    "MarkReport",
    "Rate",
    "EvidenceRef",
    "DebtProjection",
    "generate_marks",
    "distinct_marked_decision_ids",
    "orphan_decision_id",
    "drop_severity",
    "make_debt_id",
    "content_hash",
    "validate_debt_item",
    "validate_evidence_ref",
    "merge_debt_items",
    "compute_run",
    "open_debts_for_run",
    "aggregate_skill",
    "require_id",
    "require_bool",
    "require_enum",
    "require_timestamp",
    "require_finite_number",
    "ensure_json_friendly",
    "ts_to_str",
    "MARK_TYPES",
    "SEVERITIES",
    "SEVERITY_RANK",
    "DEBT_WEIGHT",
    "GATES",
    "GATED",
    "DISPOSITIONS",
    "RISKS",
    "RESOLUTION_METHOD",
    "MAX_PROPAGATION_HOPS",
    "FRESHNESS_WINDOW",
    "MAX_SAMPLE_RUNS",
    "MIN_SAMPLE_RUNS",
    "MIN_CLASSES",
    "RATE_NAMES",
]
