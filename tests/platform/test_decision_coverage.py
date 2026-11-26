"""11 类决策覆盖：生产协议路径逐类计数，UNHOOKED 与缺失显式区分"""
from __future__ import annotations

from scripts.verify_decision_coverage import collect_coverage

CASES = {
    "file_write": ("write_file", {"path": "a.txt", "content": "x"}, "ASK"),
    "file_delete": ("delete_file", {"path": "a.txt"}, "ASK"),
    "exec": ("run_command", {"argv": ["echo", "ok"]}, "ASK"),
    "network_write": ("http_request", {"url": "https://example.com"}, "ASK"),
    "vcs_publish": ("git_publish", {"operation": "push", "remote": "origin",
                                    "branch": "main"}, "ASK"),
    "policy_decision": ("set_policy", {}, "DENY"),
    "human_ack": ("ask_human", {"message": "确认"}, "ALLOW"),
    "delegate": ("delegate_task", {"task": "子任务"}, "ALLOW"),
    "abort": ("abort_run", {}, "ALLOW"),
    "run_close": ("close_run", {}, "ALLOW"),
    "final_claim": ("final_claim", {"claim": "完成"}, "ALLOW"),
}


def test_all_eleven_classes_have_a_production_path(platform):
    for index, (name, (tool, args, expected)) in enumerate(CASES.items()):
        run_id = platform.create_run(f"coverage-{index}")[
            "snapshot"]["run"]["id"]
        turn_id, _ = platform.begin(run_id, f"exec-{index}")
        intent = platform.intent(turn_id, f"DEC-{name}", name, tool, args)
        assert intent["gate"] == expected, (name, intent)
    report = collect_coverage(platform.config.database)
    assert report["status"] == "PASS"
    assert report["chain"]["status"] == "verified"
    assert report["summary"] == {
        "expected": 11, "covered": 11, "unhooked": 0, "missing": [],
        "covered_percent": 100.0}
    assert all(item["observations"] == 1 for item in report["classes"])


def test_unhooked_class_is_recorded_but_not_counted_as_covered(platform):
    run_id = platform.create_run("coverage-unhooked")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-unhooked")
    intent = platform.intent(turn_id, "DEC-UNHOOKED", "abort",
                             "unregistered_side_effect", {"x": 1})
    assert intent["gate"] == "NONE" and intent["execute"] is True
    report = collect_coverage(platform.config.database)
    by_name = {item["decision_class"]: item for item in report["classes"]}
    assert by_name["abort"]["status"] == "unhooked"
    assert by_name["abort"]["gates"] == {"NONE": 1}
    assert report["summary"]["covered"] == 0
    assert report["summary"]["unhooked"] == 1
    assert len(report["summary"]["missing"]) == 10
    assert report["status"] == "FAIL"


def test_empty_ledger_reports_every_class_missing(tmp_path):
    import sqlite3
    from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
    path = tmp_path / "empty.db"
    SQLiteEventStore(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=DELETE")
    report = collect_coverage(path)
    assert report["status"] == "FAIL"
    assert report["summary"]["covered"] == 0
    assert len(report["summary"]["missing"]) == 11
