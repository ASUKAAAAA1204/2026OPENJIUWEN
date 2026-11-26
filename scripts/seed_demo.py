"""本地演示数据：仅用于界面展示，不构成产品验收

在已启动的容器内执行：
  docker compose exec app python scripts/seed_demo.py

需要 TEMIS_DEMO_ADMIN_PASSWORD 与 TEMIS_WORKER_BOOTSTRAP_TOKEN。
"""
from __future__ import annotations

import argparse
import json
import os
import threading

import httpx


class DemoError(RuntimeError):
    pass


def _ok(response: httpx.Response, label: str) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.status_code >= 400:
        raise DemoError(f"{label} -> {response.status_code}: {body}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="写入一条本地演示运行")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username",
                        default=os.environ.get("TEMIS_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password-env", default="TEMIS_DEMO_ADMIN_PASSWORD")
    parser.add_argument("--bootstrap-env", default="TEMIS_WORKER_BOOTSTRAP_TOKEN")
    parser.add_argument("--task-id", default="demo-workflow")
    args = parser.parse_args()
    password = os.environ.get(args.password_env, "")
    bootstrap = os.environ.get(args.bootstrap_env, "")
    if not password:
        print(f"缺少环境变量 {args.password_env}")
        return 2
    if not bootstrap:
        print(f"缺少环境变量 {args.bootstrap_env}（用于注册演示 worker）")
        return 2
    base = args.base_url.rstrip("/")
    admin = httpx.Client(base_url=base, timeout=40.0)
    try:
        session = _ok(admin.get("/api/v27/session"), "session")
        login = _ok(admin.post(
            "/api/v27/session/login",
            json={"username": args.username, "password": password},
            headers={"X-CSRF-Token": session["csrf_token"]}), "login")
        csrf = login["csrf_token"]
        admin.headers.update({"X-CSRF-Token": csrf})
        listed = _ok(admin.get("/api/v27/runs",
                               params={"q": args.task_id, "limit": 20}), "runs")
        existing = next((item for item in listed["items"]
                         if item["title"].endswith(args.task_id)), None)
        if existing is not None:
            print(f"演示运行已存在：{existing['id']}（{existing['state']}）")
            return 0
        registration = _ok(admin.post(
            "/internal/v1/workers/register",
            json={"framework": "openJiuwen", "version": "0.1.20.post1",
                  "pid": os.getpid(), "capabilities": {"demo": True}},
            headers={"X-Worker-Bootstrap-Token": bootstrap}), "register")
        worker = httpx.Client(base_url=base, timeout=40.0,
                              headers={"X-Worker-Token": registration["token"]})
        worker_id = registration["worker_id"]
        worker.post(f"/internal/v1/workers/{worker_id}/heartbeat",
                    json={"worker_id": worker_id})
        context = _ok(admin.get("/api/v27/run-context"), "context")
        run = _ok(admin.post(
            "/api/v27/runs/create",
            json={"task_id": args.task_id,
                  "title": f"演示任务 {args.task_id}",
                  "workspace_id": "workspace", "framework": "openJiuwen",
                  "skill_id": "code-assistant", "skill_version": "1.0.0",
                  "expected_context_revision": context["revision"],
                  "request_id": f"seed-{args.task_id}"},
            headers={"Idempotency-Key": f"seed-{args.task_id}"}), "create")
        run_id = run["snapshot"]["run"]["id"]
        state = _ok(admin.get(
            f"/api/v27/runs/{run_id}/execution-state?segment=1"), "state")
        events: list = []

        def consume() -> None:
            client = httpx.Client(base_url=base, timeout=60.0,
                                  cookies=admin.cookies)
            try:
                with client.stream(
                        "POST", f"/api/v27/runs/{run_id}/execute",
                        json={"request_id": f"exec-{args.task_id}",
                              "expected_segment": 1,
                              "expected_execution_revision": state["revision"]},
                        headers={"X-CSRF-Token": csrf,
                                 "Idempotency-Key": f"exec-{args.task_id}",
                                 "Accept": "text/event-stream"}) as response:
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))
            except Exception:
                pass

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        turn = _ok(worker.post("/internal/v1/turns/claim",
                               json={"worker_id": worker_id}), "claim")
        turn_id = turn["turn_id"]
        _ok(worker.post(f"/internal/v1/turns/{turn_id}/events",
                        json={"client_event_id": f"started:{turn_id}",
                              "type": "turn_started",
                              "payload": {"turn_id": turn_id,
                                          "run_id": run_id, "segment": 1}}),
            "turn_started")

        def intent(decision: str, cls: str, tool: str, payload: dict) -> dict:
            return _ok(worker.post("/internal/v1/decisions/intent", json={
                "turn_id": turn_id, "decision_id": decision,
                "decision_class": cls, "tool_name": tool, "args": payload,
                "side_effect": True, "summary": f"{cls}/{tool}"}), "intent")

        def result(decision: str, attempt: str) -> None:
            _ok(worker.post(f"/internal/v1/decisions/{decision}/result", json={
                "attempt_id": attempt, "status": "EXEC_FINISHED",
                "summary": "演示执行完成",
                "evidence": {"input_digest": "sha256:" + "a" * 64,
                             "output_digest": "sha256:" + "b" * 64,
                             "model_id": "demo-model", "model_params": "t=0",
                             "verdict": "completed"},
                "output": {"demo": True}}), "result")

        allowed = intent("DEC-DEMO-ALLOW", "human_ack", "ask_human",
                         {"question": "继续吗？"})
        result("DEC-DEMO-ALLOW", allowed["attempt_id"])
        intent("DEC-DEMO-DENY-1", "policy_decision", "set_policy", {})
        asked = intent("DEC-DEMO-ASK", "file_write", "write_file",
                       {"path": "notes.txt", "content": "hello"})
        approvals = _ok(admin.get("/api/v27/approvals"), "approvals")
        item = next(a for a in approvals
                    if a["approval_id"] == asked["approval_id"])
        _ok(admin.post(f"/api/v27/approvals/{item['approval_id']}/respond",
                       json={"result": "approve", "note": "演示批准",
                             "approved_args_digest": item["args_digest"],
                             "expected_revision": item["revision"],
                             "request_id": "seed-approve"},
                       headers={"Idempotency-Key": "seed-approve"}), "approve")
        worker.get(f"/internal/v1/approvals/{item['approval_id']}/wait?timeout=2")
        result("DEC-DEMO-ASK", asked["attempt_id"])
        intent("DEC-DEMO-DENY-2", "policy_decision", "set_policy", {})
        _ok(worker.post(f"/internal/v1/turns/{turn_id}/finish",
                        json={"outcome": "completed", "run_state": "open"}),
            "finish")
        thread.join(timeout=10)
        life = _ok(admin.get(f"/api/v27/runs/{run_id}/lifecycle"), "lifecycle")
        _ok(admin.post(f"/api/v27/runs/{run_id}/close", json={
            "request_id": "seed-close",
            "expected_revision": life["revision"],
            "expected_segment": life["run"]["segment"],
            "expected_state": life["run"]["state"],
            "reason": "演示结束"},
            headers={"Idempotency-Key": "seed-close"}), "close")
        print(f"演示运行已写入：{run_id}")
        print(f"事件：{[event['type'] for event in events]}")
        print("登录后打开 运行记录 / 人工审批 / 证据与债务 / 版本评估 查看")
        return 0
    except DemoError as error:
        print(f"演示数据写入失败：{error}")
        return 2
    finally:
        admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
