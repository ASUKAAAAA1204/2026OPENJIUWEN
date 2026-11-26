"""TemisGuard v2.7 公共 API 可执行示例

只依赖 httpx；不读取内部 token，不写入 UI 或本地存储。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx


def _show(label: str, response: httpx.Response) -> dict | None:
    print(f"\n== {label} == {response.status_code}")
    if response.status_code >= 400:
        print(response.text[:1000])
        return None
    try:
        data = response.json()
    except ValueError:
        print(response.text[:1000])
        return None
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="TemisGuard 公共 API 示例")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", default=None,
                        help="从该环境变量读取密码（仅供自动化；默认交互输入）")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--last-event-id", type=int, default=0)
    parser.add_argument("--out", default="export.jsonl")
    args = parser.parse_args()
    import getpass
    import os
    password = (os.environ.get(args.password_env, "")
                if args.password_env else "")
    if not password:
        password = getpass.getpass("密码：")
    with httpx.Client(base_url=args.base_url, timeout=30,
                      follow_redirects=False) as client:
        session = _show("匿名会话", client.get("/api/v27/session"))
        if not session:
            return 1
        csrf = session["csrf_token"]
        login = client.post(
            "/api/v27/session/login",
            json={"username": args.username, "password": password},
            headers={"X-CSRF-Token": csrf})
        signed = _show("登录", login)
        if not signed or not signed.get("authenticated"):
            return 1
        csrf = signed["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        _show("已登录会话", client.get("/api/v27/session"))
        _show("运行列表", client.get("/api/v27/runs?limit=5"))
        _show("运行上下文", client.get("/api/v27/run-context"))
        _show("设置", client.get("/api/v27/settings"))
        _show("策略目录", client.get("/api/v27/policies"))
        _show("管理快照", client.get("/api/v27/management"))
        _show("评估", client.get("/api/v27/evaluations"))
        _show("债务", client.get("/api/v27/debts"))
        _show("审批队列", client.get("/api/v27/approvals"))
        if args.run_id and args.request_id:
            state = _show("执行状态", client.get(
                f"/api/v27/runs/{args.run_id}/execution-state?segment=1"))
            if state:
                stream_headers = {**headers,
                                  "Idempotency-Key": args.request_id,
                                  "Accept": "text/event-stream"}
                if args.last_event_id:
                    stream_headers["Last-Event-ID"] = str(args.last_event_id)
                print("\n== SSE 续读 ==")
                with client.stream(
                        "POST", f"/api/v27/runs/{args.run_id}/execute",
                        json={"request_id": args.request_id,
                              "expected_segment": 1,
                              "expected_execution_revision": state["revision"]},
                        headers=stream_headers) as response:
                    print(response.status_code, response.headers.get("content-type"))
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            event = json.loads(line[6:])
                            print(event["seq"], event["type"])
                            if event["type"] in ("turn_completed", "turn_failed"):
                                break
        if args.run_id:
            audit = _show("审计快照", client.get(
                f"/api/v27/runs/{args.run_id}/audit"))
            if audit and audit.get("decisions"):
                expected = [{
                    "decision_id": item["decision_id"],
                    "evidence_ref": item["evidence_ref"],
                    "content_hash": (item.get("evidence") or {}).get("content_hash"),
                    "rule_snapshot_ref": item["rule_snapshot_ref"],
                } for item in audit["decisions"]]
                key = f"replay-{args.run_id}"
                _show("规则回放", client.post(
                    f"/api/v27/runs/{args.run_id}/replay",
                    json={"request_id": key,
                          "expected_decisions": expected,
                          "execute_tools": False},
                    headers={**headers, "Idempotency-Key": key}))
            key = f"export-{args.run_id}"
            exported = client.post(
                f"/api/v27/runs/{args.run_id}/export",
                json={"request_id": key, "format": "jsonl"},
                headers={**headers, "Idempotency-Key": key})
            print(f"\n== 导出 == {exported.status_code}")
            if exported.status_code == 200:
                path = Path(args.out)
                path.write_bytes(exported.content)
                digest = hashlib.sha256(exported.content).hexdigest()
                print("文件：", path)
                print("字节 SHA256：", digest)
                print("响应头 SHA256：", exported.headers.get("x-content-sha256"))
                print("审计标识：", exported.headers.get("x-export-audit-id"))
                print("一致：", digest == exported.headers.get("x-content-sha256"))
            else:
                print(exported.text[:1000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
