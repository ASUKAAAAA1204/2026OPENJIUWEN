"""可执行示例回归：真实 uvicorn + 公共 API 跑通示例脚本

覆盖开发文档 §9.7 的最小链路：登录 → 运行/事件续读 → 审计 → 回放 → 导出。
示例脚本不读取内部 token；本测试只用服务进程内的初始化代码准备账号与样本运行。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from assurance_ledger.api.app import create_app
from assurance_ledger.auth.users import create_user
from assurance_ledger.config import (
    PlatformConfig,
    SecurityConfig,
    ServerConfig,
    SkillConfig,
    StorageConfig,
    WorkspaceConfig,
)
from assurance_ledger.ledger.writer import read_connection
from assurance_ledger.runs import decisions, execution, lifecycle, turns
from assurance_ledger.workers.registry import register_worker

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "docs" / "api" / "generated" / "examples" / "api_walkthrough.py"


def _config(base: Path, url: str) -> PlatformConfig:
    return PlatformConfig(
        root=base,
        server=ServerConfig(bind="127.0.0.1:0", internal_bind="127.0.0.1:0",
                            public_origin=url, startup_wait_seconds=0),
        storage=StorageConfig(
            database=base / "data/platform.db", outputs=base / "outputs",
            keys=base / "data/keys", backups=base / "data/backups"),
        security=SecurityConfig(allowed_root=base / "workspaces",
                                session_secret="s" * 32,
                                worker_bootstrap_token="boot-token"),
        workspaces=(WorkspaceConfig("workspace", base / "workspaces/workspace"),),
        skills=(SkillConfig("code-assistant", "1.0.0"),),
    )


def _wait(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("server did not start")


def test_documented_example_runs_against_public_api(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.listen(128)
        url = f"http://127.0.0.1:{port}"
        config = _config(tmp_path, url)
        app = create_app(config)
        server = uvicorn.Server(uvicorn.Config(
            app, log_level="critical", access_log=False,
            timeout_graceful_shutdown=2))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [probe]}, daemon=True)
        thread.start()
        try:
            _wait(lambda: server.started)
            platform = app.state.platform
            password = "walkthrough-password"
            create_user(platform.writer, username="walk", password=password,
                        display_name="Walk", role="platform_admin")
            worker = platform.writer.call(lambda db: register_worker(
                db, config, framework="openJiuwen", version="0.1.20.post1",
                pid=1))
            with read_connection(config.database) as db:
                user = dict(db.execute(
                    "SELECT * FROM users WHERE username='walk'").fetchone())
                context = lifecycle.run_context(db, config, user)
            created = lifecycle.create_run(
                platform.writer, platform.store, config, user=user,
                payload={"task_id": "walkthrough", "title": "示例链路",
                         "workspace_id": "workspace", "framework": "openJiuwen",
                         "skill_id": "code-assistant",
                         "skill_version": "1.0.0",
                         "expected_context_revision": context["revision"]},
                request_id="create-walkthrough")
            run_id = created["snapshot"]["run"]["id"]
            with read_connection(config.database) as db:
                run = lifecycle.require_run(db, run_id)
                revision = execution.execution_state(db, run, 1)["revision"]
            started = execution.start_turn(
                platform.writer, config, user=user, run_id=run_id,
                payload={"expected_segment": 1,
                         "expected_execution_revision": revision},
                request_id="exec-walkthrough")
            turn_id = started["turn"]["turn_id"]
            claim = execution.claim_turn(platform.writer, config, worker=worker)
            assert claim["turn_id"] == turn_id
            platform.writer.call(lambda db: turns.append_turn_event(
                db, turn_id=turn_id, client_event_id="started",
                event_type="turn_started",
                payload={"turn_id": turn_id, "run_id": run_id, "segment": 1}))
            intent = decisions.record_intent(
                platform.writer, platform.store, platform.journal,
                platform.record_store, config, worker=worker,
                payload={"turn_id": turn_id, "decision_id": "DEC-WALK",
                         "decision_class": "final_claim", "tool_name": "final_claim",
                         "args": {"claim": "示例"}, "side_effect": True})
            decisions.record_result(
                platform.writer, platform.store, platform.journal,
                platform.record_store, config, worker=worker,
                decision_id="DEC-WALK",
                payload={"attempt_id": intent["attempt_id"],
                         "status": "EXEC_FINISHED", "summary": "done",
                         "evidence": {"model_id": "example",
                                      "model_params": "t=0",
                                      "verdict": "completed"},
                         "output": {"ok": True}})
            execution.finish_turn(
                platform.writer, config, worker=worker, turn_id=turn_id,
                outcome="completed", run_state="open")

            exported = tmp_path / "export.jsonl"
            env = {**os.environ, "TEMIS_EXAMPLE_PASSWORD": password}
            completed = subprocess.run(
                [sys.executable, str(EXAMPLE),
                 "--base-url", url, "--username", "walk",
                 "--password-env", "TEMIS_EXAMPLE_PASSWORD",
                 "--run-id", run_id, "--request-id", "exec-walkthrough",
                 "--out", str(exported)],
                capture_output=True, text=True, timeout=90, env=env,
                cwd=str(ROOT))
            output = completed.stdout + completed.stderr
            assert completed.returncode == 0, output
            assert "登录" in output and "审计快照" in output
            assert "一致： True" in output, output
            assert exported.is_file() and exported.stat().st_size > 0
        finally:
            server.should_exit = True
            thread.join(timeout=10)
