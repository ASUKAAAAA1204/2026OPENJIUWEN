"""Opt-in DeepSeek smoke tests. Run the controller with A's installed jiuwen environment."""
import argparse
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from uuid import uuid4

from adapters import ADAPTERS, DISTRIBUTIONS, REQUESTED, TOOL, ToolRequest
from controlled import WriteTrial
from run import HERE, ROOT, offline_environment


PROMPT = "Use the write_probe tool exactly once with target requested.txt. The local test file is authorized."
TOOL_SCHEMA = {"type": "function", "function": {
    "name": TOOL, "description": "Write one receipt in the current temporary test directory.",
    "parameters": {"type": "object", "properties": {"target": {"type": "string", "enum": [REQUESTED]}},
                   "required": ["target"], "additionalProperties": False}}}


def parse_choice(completion) -> ToolRequest:
    choice, = completion.choices
    action, = choice.message.tool_calls or []
    arguments = json.loads(action.function.arguments)
    if (choice.finish_reason != "tool_calls" or action.type != "function" or action.function.name != TOOL
            or arguments != {"target": REQUESTED} or not re.fullmatch(r"[\w-]{1,192}", action.id, re.ASCII)):
        raise ValueError("Model did not request exactly the authorized tool and argument")
    return ToolRequest(action.id, action.function.name, arguments)


async def sample(framework, directory, settings, client):
    run_id = "live-" + uuid4().hex
    trial = WriteTrial(directory, "ALLOW")
    row = {"run_id": run_id, "framework": framework, "sdk_version": metadata.version(DISTRIBUTIONS[framework]),
           "model": settings["model"], "provider_requests": 0, "passed": False}
    stage = "sdk"

    async def choose():
        nonlocal stage
        if row["provider_requests"]:
            raise ValueError("The SDK attempted a second inference")
        row["provider_requests"] = 1
        stage = "provider"
        try:
            completion = await client.chat.completions.create(
                model=settings["model"], messages=[{"role": "user", "content": PROMPT}],
                tools=[TOOL_SCHEMA], tool_choice="auto", max_tokens=min(256, settings["max_tokens"]),
                temperature=0, extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            row["failure"] = {"stage": "provider_http" if status else "provider_connection",
                              "type": type(exc).__name__, "http_status": status}
            raise
        stage = "model_tool_response"
        action = parse_choice(completion)
        row.update(provider_response_sha256=hashlib.sha256(completion.id.encode()).hexdigest(),
                   response_model=completion.model, tool_call_id=action.call_id, model_arguments=action.arguments,
                   prompt_tokens=getattr(completion.usage, "prompt_tokens", None),
                   completion_tokens=getattr(completion.usage, "completion_tokens", None))
        trial.call_id = action.call_id
        stage = "sdk"
        return action

    try:
        replies = await asyncio.wait_for(ADAPTERS[framework](run_id, trial.guarded_call, choose=choose),
                                        settings["timeout_seconds"] + 20)
        files = {p.name: p.read_text(encoding="ascii").splitlines() for p in directory.iterdir()}
        receipts = files.get(REQUESTED, [])
        row.update(files=files, actual_invocations=trial.executions, sdk_replies=replies,
                   passed=(row["provider_requests"] == 1 and row.get("tool_call_id") == trial.call_id
                           and set(files) == {REQUESTED} and len(receipts) == 1
                           and receipts[0].startswith(f"written:{trial.call_id}:{REQUESTED}:")
                           and trial.requested == trial.executions == [REQUESTED] and trial.terminals == receipts
                           and replies == [(trial.call_id, receipts[0])]))
        if not row["passed"]:
            row["failure"] = {"stage": "receipt_or_disk"}
    except Exception as exc:
        row.setdefault("failure", {"stage": stage, "type": type(exc).__name__})
    return row


async def worker(framework, out):
    logging.disable(logging.CRITICAL)
    import httpx
    from openai import AsyncOpenAI

    settings = json.load(sys.stdin)
    if settings["base_url"] not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
        raise ValueError("Unsupported API host")
    client = AsyncOpenAI(api_key=settings.pop("api_key"), base_url=settings["base_url"], max_retries=0,
                         timeout=settings["timeout_seconds"], http_client=httpx.AsyncClient(
                             follow_redirects=False, trust_env=False, timeout=settings["timeout_seconds"]))
    rows = []
    try:
        for repeat in (1, 2, 3):
            subdir = out / f"trial-{repeat}"
            subdir.mkdir()
            row = await sample(framework, subdir, settings, client)
            rows.append({**row, "repeat": repeat})
            (out / "trials.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            if not row["passed"]:
                break
    finally:
        await client.close()
    return len(rows) == 3 and all(row["passed"] for row in rows)


def main():
    from assurance_ledger.rail.model_config import load_deepseek_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/b-m0/online")
    for framework in ADAPTERS:
        parser.add_argument(f"--{framework}-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    config = load_deepseek_settings(args.env_file, environ={})
    payload = json.dumps({"model": config.model, "api_key": config.api_key, "base_url": config.base_url,
                          "timeout_seconds": config.timeout_seconds, "max_tokens": config.max_tokens})
    args.output.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="deepseek-", dir=args.output.resolve()))
    report = {"format": "b-m0-live-v1", "provider": "DeepSeek", "model": config.model,
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "sources_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                                 for name in ("online.py", "adapters.py", "controlled.py", "approval.py")},
              "task_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(), "frameworks": []}
    stop = False
    for framework in ADAPTERS:
        subdir = out / framework
        subdir.mkdir()
        rows = []
        error = None
        if not stop:
            python = getattr(args, framework + "_python").resolve()
            try:
                # The secret travels only through stdin. SDK debug streams are never retained.
                with tempfile.TemporaryDirectory(dir=subdir) as scratch:
                    p = subprocess.run([str(python), "-B", str(HERE / "online.py"), "--worker", framework, str(subdir)],
                                       input=payload, text=True, cwd=scratch, env=offline_environment(),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=3*(config.timeout_seconds+25)+35, check=False)
                dest = subdir / "trials.json"
                rows = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else []
                if p.returncode and not any(row.get("failure") for row in rows):
                    error = "worker_failed"
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                error = type(exc).__name__
        else:
            error = "not_run_after_provider_refusal"
        if any(row.get("failure", {}).get("http_status") in (401, 402, 403, 429) for row in rows):
            stop = True
        report["frameworks"].append({"framework": framework, "trials": rows, "error": error})
    measured = [row for f in report["frameworks"] for row in f["trials"]]
    successes = [r for r in measured if r["passed"]]
    response_ids = {r["provider_response_sha256"] for r in successes}
    passed = (len(successes) == len(response_ids) == 9
              and all(not f["error"] and [r["repeat"] for r in f["trials"]] == [1,2,3] for f in report["frameworks"]))
    report.update(completed_at=datetime.now(timezone.utc).isoformat(), provider_requests=sum(
        r["provider_requests"] for r in measured), passed_trials=len(successes), required_trials=9, passed=passed)
    path = out / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Real tool calls verified: {len(successes)}/9")
    print(path)
    return passed


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        sys.exit(0 if asyncio.run(worker(sys.argv[2], Path(sys.argv[3]))) else 1)
    sys.exit(0 if main() else 1)
