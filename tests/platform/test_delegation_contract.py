"""父子委派：运行关联、深度上限 3 条委派边、关闭父运行后拒绝委派"""
from __future__ import annotations


def _create(platform, task_id: str, parent: str | None = None):
    context = platform.run_context()
    body = {
        "task_id": task_id, "title": f"Task {task_id}",
        "workspace_id": "workspace", "framework": "openJiuwen",
        "skill_id": "code-assistant", "skill_version": "1.0.0",
        "expected_context_revision": context["revision"],
        "request_id": f"create-{task_id}", "parent_run_id": parent,
    }
    return platform.client.post("/api/v27/runs/create", json=body,
                                headers=platform.fresh_headers(body["request_id"]))


def test_parent_child_links_and_gate_sees_the_same_depth(platform):
    parent = platform.create_run("delegation-root")[
        "snapshot"]["run"]["id"]
    child = _create(platform, "delegation-child", parent)
    assert child.status_code == 200, child.text
    child_run = child.json()["snapshot"]["run"]
    assert child_run["parent_run_id"] == parent
    listed = platform.client.get("/api/v27/runs").json()["items"]
    by_id = {item["id"]: item for item in listed}
    assert by_id[child_run["id"]]["parent_run_id"] == parent
    assert by_id[parent]["child_run_ids"] == [child_run["id"]]
    audit = platform.client.get(
        f"/api/v27/runs/{child_run['id']}/audit").json()
    assert audit["parent_run_id"] == parent

    # 子运行的 delegate 意图仍受 Gate 深度限制：第 4 层运行再委派转 DENY
    current = child_run["id"]
    for index in range(2):
        response = _create(platform, f"delegation-level-{index}", current)
        assert response.status_code == 200, response.text
        current = response.json()["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(current, "exec-deep")
    intent = platform.intent(turn_id, "DEC-DEEP", "delegate", "delegate_task",
                             {"task": "更深一层"})
    assert intent["gate"] == "DENY"
    assert "委派深度超过 3" in intent["gate_reason"]
    # 第 4 条委派边不能再通过运行创建
    too_deep = _create(platform, "delegation-too-deep", current)
    assert too_deep.status_code == 422
    assert too_deep.json()["error"]["code"] == "CONDITION_FAILED"


def test_unknown_or_closed_parent_rejects_delegation(platform):
    unknown = _create(platform, "delegation-unknown", "RUN-DOES-NOT-EXIST")
    assert unknown.status_code == 422
    closed_parent = platform.create_run("delegation-closed")[
        "snapshot"]["run"]["id"]
    platform.close(closed_parent)
    closed = _create(platform, "delegation-child-closed", closed_parent)
    assert closed.status_code == 422
    assert "父运行已关闭" in closed.json()["error"]["message"]


def test_same_task_with_different_parent_conflicts(platform):
    parent = platform.create_run("delegation-a")["snapshot"]["run"]["id"]
    other = platform.create_run("delegation-b")["snapshot"]["run"]["id"]
    assert _create(platform, "delegation-repeat", parent).status_code == 200
    context = platform.run_context()
    body = {
        "task_id": "delegation-repeat", "title": "Task delegation-repeat",
        "workspace_id": "workspace", "framework": "openJiuwen",
        "skill_id": "code-assistant", "skill_version": "1.0.0",
        "expected_context_revision": context["revision"],
        "request_id": "create-delegation-repeat-2", "parent_run_id": other,
    }
    conflict = platform.client.post(
        "/api/v27/runs/create", json=body,
        headers=platform.fresh_headers(body["request_id"]))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "TURN_CONFLICT"
