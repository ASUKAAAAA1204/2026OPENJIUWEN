"""B v2.7 债务投影测试：精确六条验收、append-only、回放/篡改/非法消解、防御性拷贝。"""
import copy
from datetime import datetime, timezone

import pytest

from assurance_ledger.engine.v27 import (
    DEBT_WEIGHT,
    RESOLUTION_METHOD,
    DebtProjection,
    EvidenceRef,
    content_hash,
    make_debt_id,
    merge_debt_items,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)


def ref(payload, evidence_id):
    return EvidenceRef(evidence_id=evidence_id, content_hash=content_hash(payload), payload=payload)


def demo_debts(projection):
    """演示口径的六条债务：NO_EVIDENCE high+mid、SOURCE_CONFLICT mid+mid、
    SPEC_MISMATCH low+low，初始加权值 11、未消解 6。"""
    opens = [
        ("d-01", "NO_EVIDENCE", "high"),
        ("d-02", "NO_EVIDENCE", "mid"),
        ("d-03", "SOURCE_CONFLICT", "mid"),
        ("d-04", "SOURCE_CONFLICT", "mid"),
        ("d-05", "SPEC_MISMATCH", "low"),
        ("d-06", "SPEC_MISMATCH", "low"),
    ]
    for decision_id, mark_type, severity in opens:
        projection.open_debt(
            run_id="run-demo-1",
            decision_id=decision_id,
            mark_type=mark_type,
            original_severity=severity,
            display_severity=severity,
        )
    return opens


def test_exact_six_entry_debt_acceptance():
    projection = DebtProjection()
    opens = demo_debts(projection)
    summary = projection.summary(run_id="run-demo-1")
    assert summary["weighted_value"] == 11
    assert summary["unresolved_count"] == 6
    assert summary["item_count"] == 6
    assert len(projection.to_events()) == 6
    weights = {o[0]: DEBT_WEIGHT[o[2]] for o in opens}
    assert weights == {
        "d-01": 3, "d-02": 2, "d-03": 2, "d-04": 2, "d-05": 1, "d-06": 1,
    }
    # debt_id 确定性：同一三要素永远同一 ID；不同类型分开计
    assert make_debt_id("run-demo-1", "d-01", "NO_EVIDENCE") != make_debt_id("run-demo-1", "d-01", "SPEC_MISMATCH")
    assert make_debt_id("run-demo-1", "d-01", "NO_EVIDENCE") == make_debt_id("run-demo-1", "d-01", "NO_EVIDENCE")
    ids = {i["debt_id"] for i in summary["items"]}
    assert len(ids) == 6


def test_replay_does_not_duplicate_opens_and_conflicting_reopen_rejected():
    projection = DebtProjection()
    first = projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid")
    again = projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid")
    assert first["debt_id"] == again["debt_id"]
    assert len(projection.to_events()) == 1
    # 同决策多类型分别计债（debt_id 不同，合法开户）
    second = projection.open_debt("r1", "d1", "SPEC_MISMATCH", "mid", "mid")
    assert second["debt_id"] != first["debt_id"]
    assert len(projection.to_events()) == 2
    with pytest.raises(ValueError):
        projection.open_debt("r1", "d1", "NO_EVIDENCE", "high", "high")
    with pytest.raises(ValueError):
        projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid", weight=3)
    with pytest.raises(ValueError):
        projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "high")  # display > original


def test_resolve_requires_matching_method_and_content_addressed_ref():
    projection = DebtProjection()
    projection.open_debt("r1", "d1", "NO_EVIDENCE", "high", "high")
    debt_id = make_debt_id("r1", "d1", "NO_EVIDENCE")
    payload = {"evidence_id": "ev-x", "digest": "supp"}
    item = projection.resolve_debt(debt_id, ref(payload, "ev-x"), "supplement_evidence")
    assert item["status"] == "resolved"
    assert item["resolved"]["evidence_ref"]["content_hash"] == content_hash(payload)
    # 错误消解方式
    projection2 = DebtProjection()
    projection2.open_debt("r1", "d2", "NO_EVIDENCE", "high", "high")
    with pytest.raises(ValueError):
        projection2.resolve_debt(make_debt_id("r1", "d2", "NO_EVIDENCE"), ref(payload, "ev-x"), "unify_sources")
    # 空 evidence_id / 非法 content_hash / payload 与哈希不符
    projection3 = DebtProjection()
    projection3.open_debt("r1", "d3", "NO_EVIDENCE", "high", "high")
    debt3 = make_debt_id("r1", "d3", "NO_EVIDENCE")
    with pytest.raises(ValueError):
        projection3.resolve_debt(debt3, ref(payload, ""), "supplement_evidence")
    with pytest.raises(ValueError):
        EvidenceRef("ev-x", "not-a-hash", payload)
    with pytest.raises(ValueError):
        EvidenceRef("ev-x", content_hash({"a": 1}), payload)
    # 未知债务 ID
    with pytest.raises(ValueError):
        projection3.resolve_debt("debt:unknown", ref(payload, "ev-x"), "supplement_evidence")


def test_spec_mismatch_requires_explicit_regate_success():
    projection = DebtProjection()
    projection.open_debt("r1", "d1", "SPEC_MISMATCH", "low", "low")
    debt_id = make_debt_id("r1", "d1", "SPEC_MISMATCH")
    payload = {"evidence_id": "ev-r", "parameters_match": True}
    with pytest.raises(ValueError):
        projection.resolve_debt(debt_id, ref(payload, "ev-r"), "correct_params_and_regate")
    with pytest.raises(ValueError):
        projection.resolve_debt(debt_id, ref(payload, "ev-r"), "correct_params_and_regate", regate_success=False)
    item = projection.resolve_debt(
        debt_id, ref(payload, "ev-r"), "correct_params_and_regate", regate_success=True
    )
    assert item["resolved"]["regate_success"] is True
    # 非 SPEC_MISMATCH 不允许携带 regate_success
    projection2 = DebtProjection()
    projection2.open_debt("r1", "d2", "SOURCE_CONFLICT", "mid", "mid")
    with pytest.raises(ValueError):
        projection2.resolve_debt(
            make_debt_id("r1", "d2", "SOURCE_CONFLICT"),
            ref(payload, "ev-r"),
            "unify_sources",
            regate_success=True,
        )


def test_append_only_events_and_roundtrip():
    projection = DebtProjection()
    demo_debts(projection)
    payload = {"evidence_id": "ev-s", "digest": "x"}
    debt_id = make_debt_id("run-demo-1", "d-01", "NO_EVIDENCE")
    projection.resolve_debt(debt_id, ref(payload, "ev-s"), "supplement_evidence")
    events = projection.to_events()
    assert [e["event_type"] for e in events].count("debt_open") == 6
    assert [e["event_type"] for e in events].count("debt_resolved") == 1
    # open 事件保留（append-only），消解只是追加
    open_event = next(e for e in events if e["event_type"] == "debt_open" and e["debt_id"] == debt_id)
    assert open_event["event_type"] == "debt_open"
    rehydrated = DebtProjection.from_events(events)
    assert rehydrated.to_events() == events
    assert rehydrated.summary(run_id="run-demo-1")["weighted_value"] == 8  # 11 - 3


def test_replay_events_reject_tampering():
    projection = DebtProjection()
    demo_debts(projection)
    payload = {"evidence_id": "ev-s", "digest": "x"}
    debt_id = make_debt_id("run-demo-1", "d-01", "NO_EVIDENCE")
    projection.resolve_debt(debt_id, ref(payload, "ev-s"), "supplement_evidence")
    events = projection.to_events()

    # 权重突变
    tampered = copy.deepcopy(events)
    tampered[0]["weight"] = 99
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    # 类型突变
    tampered = copy.deepcopy(events)
    tampered[0]["mark_type"] = "SPEC_MISMATCH"
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    # 未知债务 ID（与三要素哈希不符）
    tampered = copy.deepcopy(events)
    tampered[0]["debt_id"] = "debt:ffffffffffffffffffff"
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    # 冲突的重复 open（同 debt_id、不同严重度）
    tampered = copy.deepcopy(events)
    dup = dict(tampered[0])
    dup["seq"] = len(tampered)
    dup["original_severity"] = "mid"
    dup["display_severity"] = "mid"
    dup["weight"] = 2
    tampered.append(dup)
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    # 未知事件类型 / seq 断裂
    tampered = copy.deepcopy(events)
    tampered.append({"seq": len(tampered), "event_type": "debt_waive", "debt_id": debt_id})
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    tampered = copy.deepcopy(events)
    tampered[1]["seq"] = 7
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    # 消解未知债务
    tampered = copy.deepcopy(events)
    tampered.append(
        {
            "seq": len(tampered),
            "event_type": "debt_resolved",
            "debt_id": "debt:ffffffffffffffffffff",
            "method": "supplement_evidence",
            "evidence_ref": {"evidence_id": "e", "content_hash": "a" * 64},
            "regate_success": None,
            "resolved_at": None,
        }
    )
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)
    # 消解方式与类型不匹配
    tampered = copy.deepcopy(events)
    tampered.append(
        {
            "seq": len(tampered),
            "event_type": "debt_resolved",
            "debt_id": make_debt_id("run-demo-1", "d-03", "SOURCE_CONFLICT"),
            "method": "supplement_evidence",
            "evidence_ref": {"evidence_id": "e", "content_hash": "a" * 64},
            "regate_success": None,
            "resolved_at": None,
        }
    )
    with pytest.raises(ValueError):
        DebtProjection.from_events(tampered)


def test_strict_history_rejects_duplicate_logical_events():
    # 历史里同内容事件重复出现（即使 seq 连续合法）：严格加载拒绝重复逻辑
    # open/resolve；API 幂等不应与严格历史去重混为一谈。
    projection = DebtProjection()
    demo_debts(projection)
    events = projection.to_events()
    doubled = []
    for event in events:
        doubled.append(dict(event))
        doubled.append(dict(event))
    for index, event in enumerate(doubled):
        event["seq"] = index
    with pytest.raises(ValueError):
        DebtProjection.from_events(doubled)


def test_defensive_copies_no_alias_leak():
    projection = DebtProjection()
    demo_debts(projection)
    item = projection.items()[0]
    item["weight"] = 999
    item["provenance"].append("mutated")
    assert projection.items()[0]["weight"] != 999
    assert projection.items()[0]["provenance"] == []
    events = projection.to_events()
    events[0]["weight"] = 999
    assert projection.to_events()[0]["weight"] != 999
    summary = projection.summary()
    summary["items"][0]["weight"] = 999
    assert projection.summary()["items"][0]["weight"] != 999


def test_threshold_review_reason_only_no_blocking():
    projection = DebtProjection()
    demo_debts(projection)
    reason = projection.threshold_review_reason("run-demo-1", 10)
    assert reason is not None
    assert reason["code"] == "DEBT_THRESHOLD"
    assert "block" not in reason and "gate" not in reason
    assert projection.threshold_review_reason("run-demo-1", 20) is None
    # threshold=11（等于加权值）也触发（>=）
    assert projection.threshold_review_reason("run-demo-1", 11) is not None
    for bad in (-1, float("nan"), float("inf"), True, "10"):
        with pytest.raises(ValueError):
            projection.threshold_review_reason("run-demo-1", bad)


def test_merge_debt_items_dedup_by_owner_debt_id():
    projection = DebtProjection()
    demo_debts(projection)
    items = projection.items()
    # 完全相同重复引用 -> 只保留一份
    merged = merge_debt_items(items, items)
    assert len(merged) == 6
    # 冲突（同 debt_id 不同权重）-> 拒绝
    conflicting = copy.deepcopy(items)
    conflicting[0]["weight"] = 99
    with pytest.raises(ValueError):
        merge_debt_items(items, conflicting)
    # 非法条目拒绝
    with pytest.raises(ValueError):
        merge_debt_items([{"debt_id": "x"}])
    with pytest.raises(ValueError):
        merge_debt_items([{**items[0], "extra": 1}])
    # 输出排序确定
    assert [m["debt_id"] for m in merged] == sorted(m["debt_id"] for m in merged)


def test_resolution_methods_mapping():
    assert RESOLUTION_METHOD == {
        "NO_EVIDENCE": "supplement_evidence",
        "SOURCE_CONFLICT": "unify_sources",
        "SPEC_MISMATCH": "correct_params_and_regate",
    }


def test_resolve_idempotent_same_params_conflicting_otherwise():
    projection = DebtProjection()
    projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid")
    debt_id = make_debt_id("r1", "d1", "NO_EVIDENCE")
    payload = {"evidence_id": "ev-1", "x": 1}
    first = projection.resolve_debt(debt_id, ref(payload, "ev-1"), "supplement_evidence")
    again = projection.resolve_debt(debt_id, ref(payload, "ev-1"), "supplement_evidence")
    assert first == again
    assert len(projection.to_events()) == 2  # open + resolve 各一
    with pytest.raises(ValueError):
        projection.resolve_debt(debt_id, ref({"evidence_id": "ev-2", "x": 2}, "ev-2"), "supplement_evidence")
