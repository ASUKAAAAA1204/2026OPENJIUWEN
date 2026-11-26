"""Exercise the public platform API and real SDK workers against a configured DeepSeek model."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.api.app import create_app
from assurance_ledger.auth.users import create_user
from assurance_ledger.config import (PlatformConfig, SecurityConfig, ServerConfig, SkillConfig,
                                     StorageConfig, WorkerConfig, WorkspaceConfig)
from assurance_ledger.ledger.canonical import digest_json


FRAMEWORKS = {"jiuwen": "openJiuwen", "graph": "LangGraph", "agents": "OpenAI Agents SDK"}
CASES = {"approve": "approved", "reject": "rejected", "timeout": "timeout"}


def check(condition, description: str) -> None:
    if not condition:
        raise ValueError(description)


async def get(client, route):
    response = await client.get("/api/v27/" + route)
    response.raise_for_status()
    return response.json()


def mutation(request_id, csrf):
    return {"Idempotency-Key": request_id, "X-CSRF-Token": csrf}


async def post(client, route, csrf, body):
    response = await client.post("/api/v27/" + route, json=body, headers=mutation(body["request_id"], csrf))
    response.raise_for_status()
    return response.json()


async def stream_events(client, run_id, csrf, body, last_seq=None):
    headers = {**mutation(body["request_id"], csrf), "Accept": "text/event-stream"}
    if last_seq is not None:
        headers["Last-Event-ID"] = str(last_seq)
    async with client.stream("POST", f"/api/v27/runs/{run_id}/execute", headers=headers, json=body) as response:
        response.raise_for_status()
        check(response.headers["content-type"].startswith("text/event-stream"), "execute returned no event stream")
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                yield json.loads(line[5:])


async def approval(client, approval_id):
    items = await get(client, "approvals")
    matches = [entry for entry in items if entry["approval_id"] == approval_id]
    check(len(matches) == 1, "expected exactly one matching approval")
    return matches[0]


async def exercise(client, csrf, workspace: Path, sdk: str, case: str):
    tag = f"{sdk}-{case}"
    path, content = f"demo-{tag}.txt", f"B_DEMO_{sdk.upper()}_{case.upper()}"
    target = workspace / path
    check(not target.exists(), "test target already exists")
    digest = digest_json({"path": path, "content": content})
    context = await get(client, "run-context")
    created = await post(client, "runs/create", csrf, {
        "request_id": f"create-{tag}", "task_id": tag, "workspace_id": "workspace",
        "title": f"Write the file {path} containing exactly {content} with no newline.",
        "skill_id": "code-assistant", "skill_version": "1.0.0", "framework": FRAMEWORKS[sdk],
        "expected_context_revision": context["revision"],
    })
    run_id = created["snapshot"]["run"]["id"]
    before = await get(client, f"runs/{run_id}/execution-state?segment=1")
    body = {"request_id": f"exec-{tag}", "expected_segment": 1, "expected_execution_revision": before["revision"]}
    events, pending = [], None
    async for event in stream_events(client, run_id, csrf, body):
        events.append(event)
        if event["type"] != "approval_requested":
            continue
        check(pending is None and not target.exists(), "file exists before approval or approval repeated")
        pending = await approval(client, event["approval_id"])
        check(pending["status"] == "pending" and pending["can_respond"] and pending["run_id"] == run_id
              and pending["decision_id"] == event["decision_id"] and pending["args_digest"] == digest,
              "approval is not bound to this request and its exact arguments")
        if case != "timeout":
            await post(client, f"approvals/{pending['approval_id']}/respond", csrf, {
                "result": case, "note": "Synthetic demo", "approved_args_digest": digest,
                "expected_revision": pending["revision"], "request_id": f"answer-{tag}",
            })
    check(pending is not None, f"no ASK received; observed {[e['type'] for e in events]}")
    check([e["seq"] for e in events] == list(range(1, len(events) + 1)), "event sequence gap")
    yes = case == "approve"
    types = [e["type"] for e in events]
    check(types[0] == "turn_started" and types[-1] == ("turn_completed" if yes else "turn_failed")
          and types.count("tool_intent") == 1 and types.count("tool_result") == int(yes),
          f"unexpected terminal or tool events: {types}")
    item = await approval(client, pending["approval_id"])
    check(item["status"] == CASES[case] and item["response"]["approved_args_digest"] == digest,
          "final approval outcome or argument fingerprint differs")
    audit = await get(client, f"runs/{run_id}/audit")
    check(audit["chain"]["status"] == "verified" and len(audit["decisions"]) == 1, "invalid ledger chain or count")
    decision = audit["decisions"][0]
    check(decision["decision_id"] == item["decision_id"] and decision["gate"] == "ASK"
          and decision["tool_name"] == "write_file" and decision["args_digest"] == digest, "decision differs")
    history = await get(client, f"runs/{run_id}/decisions/{item['decision_id']}/events")
    ledger = [entry["type"] for entry in history["events"]]
    expected = ["GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED"] + (["EXEC_STARTED", "EXEC_FINISHED"] if yes else [])
    check(ledger == expected, f"unexpected ledger history: {ledger}")
    file_hash = model = None
    if yes:
        data = target.read_bytes()
        check(data == content.encode("utf-8"), "wrong bytes on disk")
        file_hash = hashlib.sha256(data).hexdigest()
        evidence = decision["evidence"] or {}
        model = evidence.get("model_id")
        check(model and model != "unknown" and json.loads(evidence["model_params"])["sdk_call_id"]
              and evidence["input_digest"] == digest and evidence["content_hash"], "missing model/tool evidence")
    else:
        check(not target.exists(), "unapproved file exists")
    # Reconnect to the same completed request as a browser would. Re-reading must not touch the file.
    mtime = target.stat().st_mtime_ns if yes else None
    replay = [e async for e in stream_events(client, run_id, csrf, body, last_seq=1)]
    check(replay == events[1:], "reconnected stream differs")
    check((target.stat().st_mtime_ns if target.exists() else None) == mtime, "reconnect changed a file")
    print(f"{FRAMEWORKS[sdk]} {case}: PASS", flush=True)
    return {"framework": FRAMEWORKS[sdk], "case": case, "run_id": run_id,
            "decision_id": item["decision_id"], "approval_status": item["status"], "args_digest": digest,
            "file_sha256": file_hash, "model_on_execution_evidence": model, "sse": types,
            "ledger_events": ledger, "chain": audit["chain"]["status"], "sse_replay_matches": True}


async def verify(args, root: Path, report):
    os.environ["MODEL_ENV_FILE"] = str(args.env_file.resolve(strict=True))
    jiuwen = str(args.openjiuwen_python.resolve(strict=True))
    other = str(args.framework_python.resolve(strict=True))
    workspace = root / "workspaces" / "workspace"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        addr = f"127.0.0.1:{listener.getsockname()[1]}"
        url = "http://" + addr
        config = PlatformConfig(
            root=root, server=ServerConfig(addr, addr, url),
            storage=StorageConfig(root / "data/platform.db", root / "outputs", root / "keys", root / "backups"),
            security=SecurityConfig(allowed_root=workspace.parent, session_secret=secrets.token_urlsafe(40),
                                    worker_bootstrap_token=secrets.token_urlsafe(32)),
            workspaces=(WorkspaceConfig("workspace", workspace),), skills=(SkillConfig("code-assistant", "1.0.0"),),
            workers=tuple(WorkerConfig(sdk, FRAMEWORKS[sdk], jiuwen if sdk == "jiuwen" else other)
                          for sdk in args.frameworks), start_workers=True,
        )
        app = create_app(config)
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, timeout_graceful_shutdown=3))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(60):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("central service did not start")
                    await asyncio.sleep(0.1)
            password = secrets.token_urlsafe(30)
            create_user(app.state.platform.writer, username="demo-operator", password=password,
                        display_name="Demo operator", role="platform_admin")
            async with httpx.AsyncClient(base_url=url, headers={"Origin": url}, timeout=90, trust_env=False) as client:
                anonymous = await get(client, "session")
                login = await client.post("/api/v27/session/login", headers={"X-CSRF-Token": anonymous["csrf_token"]},
                                          json={"username": "demo-operator", "password": password})
                login.raise_for_status()
                csrf = login.json()["csrf_token"]
                wanted = {FRAMEWORKS[sdk] for sdk in args.frameworks}
                async with asyncio.timeout(90):
                    while True:
                        context = await get(client, "run-context")
                        ready = {f["name"]: f["version"] for f in context["frameworks"] if f["available"]}
                        if wanted == set(ready):
                            break
                        states = app.state.platform.supervisor.status()
                        check(all(s["state"] != "unavailable" for s in states),
                              "worker unavailable: " + ", ".join(s["name"] for s in states if s["state"] == "unavailable"))
                        await asyncio.sleep(0.4)
                report["sdk_versions"] = ready
                for case in args.cases:
                    results = await asyncio.gather(
                        *(asyncio.wait_for(exercise(client, csrf, workspace, sdk, case), 110) for sdk in args.frameworks),
                        return_exceptions=True,
                    )
                    for sdk, result in zip(args.frameworks, results):
                        if isinstance(result, BaseException):
                            # Validation messages contain only event names. HTTP failures expose no request body.
                            detail = str(result) if isinstance(result, ValueError) else type(result).__name__
                            report["failures"].append({"framework": FRAMEWORKS[sdk], "case": case, "reason": detail})
                        else:
                            report["cases"].append(result)
                    if report["failures"]:
                        break
        finally:
            if server.started:
                await asyncio.to_thread(app.state.platform.supervisor.stop)
            server.should_exit = True
            await asyncio.wait_for(task, 20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True, help="local DeepSeek configuration")
    parser.add_argument("--openjiuwen-python", type=Path, required=True)
    parser.add_argument("--framework-python", type=Path, required=True, help="LangGraph / Agents SDK interpreter")
    parser.add_argument("--frameworks", nargs="+", choices=FRAMEWORKS, default=list(FRAMEWORKS))
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--output", type=Path, required=True, help="new UTF-8 JSON report (no credentials or disk paths)")
    args = parser.parse_args()
    if args.output.exists() or len(set(args.frameworks)) != len(args.frameworks) or len(set(args.cases)) != len(args.cases):
        parser.error("select each framework/case once and a new report filename")
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "provider": "DeepSeek",
              "approval_timeout_seconds": 30, "sdk_versions": {}, "cases": [], "failures": []}
    try:
        with tempfile.TemporaryDirectory(prefix="temisguard-demo-") as area:
            asyncio.run(verify(args, Path(area).resolve(), report))
    except Exception as exc:
        report["failures"].append({"stage": "platform", "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    report["passed"] = len(report["cases"])
    report["expected"] = len(args.frameworks) * len(args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": report["passed"], "expected": report["expected"], "failures": report["failures"]}, ensure_ascii=False))
    return int(bool(report["failures"]) or report["passed"] != report["expected"])


if __name__ == "__main__":
    raise SystemExit(main())
