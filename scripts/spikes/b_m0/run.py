"""Run the three SDKs against the registered file tool in separate processes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from tempfile import mkdtemp

from adapters import ADAPTERS
from worker import SUITES

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def offline_environment() -> dict[str, str]:
    # Only OS/runtime settings are needed. Model credentials and tracing settings stay outside.
    names = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "PATHEXT", "TEMP", "TMP",
             "TMPDIR", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "LANG",
             "LC_ALL", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", OPENAI_AGENTS_DISABLE_TRACING="1",
               LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false", HF_HUB_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", OTEL_SDK_DISABLED="true")
    return env


def check_jiuwen_lock(lock: Path, manifest: Path) -> dict:
    def package_name(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    data = lock.read_bytes()
    pinned = {}
    for line in data.decode("utf-8-sig").splitlines():
        line = line.partition("#")[0].strip()
        if line:
            name, version = line.split("==")
            pinned[package_name(name)] = version
    installed = {package_name(name): version for name, version in
                 json.loads(manifest.read_text(encoding="utf-8")).items()}
    differences = {name: {"pinned": version, "installed": installed.get(name)}
                   for name, version in pinned.items() if installed.get(name) != version}
    # The bootstrap installs the project in editable mode; pip comes from venv.
    extra = sorted(installed.keys() - pinned.keys() - {"assurance-ledger", "pip"})
    return {"file": lock.name, "sha256": hashlib.sha256(data).hexdigest(), "pins": len(pinned),
            "matching": len(pinned) - len(differences), "differences": differences,
            "unexpected": extra, "passed": bool(pinned) and not differences and not extra}


def run_framework(root: Path, framework: str, python: Path, suite: str, lock: Path | None) -> dict:
    work = root / framework
    work.mkdir()
    summary = {"framework": framework, "passed": False, "executions_passed": 0,
               "scenarios_passed": 0, "executions_measured": 0}
    try:
        with (work / "sdk.log").open("wb") as log:
            process = subprocess.run([str(python.resolve()), "-B", str(HERE / "worker.py"), framework, suite],
                                     cwd=work, env=offline_environment(), stdout=log, stderr=subprocess.STDOUT,
                                     timeout=180, check=False)
        detail = json.loads((work / "result.json").read_text(encoding="utf-8"))
        rows = detail["trials"]
        manifest = work / "installed-versions.json"
        manifest_ok = (manifest.is_file() and hashlib.sha256(manifest.read_bytes()).hexdigest()
                       == detail.get("installed_versions_sha256"))
        expected_grid = {(s, r) for s in SUITES[suite] for r in (1, 2, 3)}
        passes = {(r["scenario"], r["repeat"]) for r in rows if r["passed"]}
        summary.update(executions_passed=len(passes), executions_measured=len(rows),
                       scenarios_passed=sum(all((s, r) in passes for r in (1, 2, 3))
                                            for s in SUITES[suite]),
                       sdk_version=detail.get("sdk_version"), mcp_version=detail.get("mcp_version"),
                       python=detail["python"], model_input=detail["model_input"],
                       installed_versions_sha256=detail.get("installed_versions_sha256"),
                       installed_versions_verified=manifest_ok,
                       details=f"{framework}/result.json",
                       passed=(process.returncode == 0 and detail["passed"] and manifest_ok
                               and detail["framework"] == framework and detail["online_model_calls"] == 0
                               and len(rows) == len(expected_grid) and passes == expected_grid))
        if suite != "baseline":
            summary["trials"] = rows
        if detail.get("error"):
            summary["error"] = detail["error"]
        if framework == "openjiuwen" and lock is not None:
            summary["team_lock"] = check_jiuwen_lock(lock, manifest)
            summary["passed"] = summary["passed"] and summary["team_lock"]["passed"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["passed"] = False
    return summary


def main() -> bool:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "b-m0")
    parser.add_argument("--framework", choices=ADAPTERS, help="Run one SDK for diagnosis")
    parser.add_argument("--suite", choices=SUITES, default="baseline",
                        help="baseline=S01–S03 (default), approval=S04–S06, all=S01–S06")
    parser.add_argument("--jiuwen-lock", type=Path, help="Check installed openJiuwen packages against A's lock")
    for framework in ADAPTERS:
        parser.add_argument(f"--{framework}-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    if args.jiuwen_lock and args.framework not in (None, "openjiuwen"):
        parser.error("--jiuwen-lock requires the openjiuwen framework")
    args.output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mkdtemp(prefix="control-", dir=args.output.resolve()))
    frameworks = [args.framework] if args.framework else list(ADAPTERS)
    engines = [run_framework(run_dir, name, getattr(args, name + "_python"), args.suite, args.jiuwen_lock)
               for name in frameworks]
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    report = {
        "format": "b-m0-control-points-v1" if args.suite == "baseline" else "b-m0-control-points-v2",
        "scope": "registered local tool, " + "/".join(SUITES[args.suite]) + " preflight",
        "completed_at": datetime.now(timezone.utc).isoformat(), "git_head": head, "os": platform.platform(),
        "sources_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (HERE / "run.py", HERE / "adapters.py", HERE / "worker.py", HERE / "approval.py")},
        "frameworks": engines, "scenarios_passed": sum(f["scenarios_passed"] for f in engines),
        "scenarios_requested": len(SUITES[args.suite]) * len(engines),
        "executions_passed": sum(f["executions_passed"] for f in engines),
        "executions_requested": len(SUITES[args.suite]) * 3 * len(engines), "online_model_calls": 0,
        "preflight_passed": all(f["passed"] for f in engines),
    }
    if args.suite != "baseline":
        rows = [row for engine in engines for row in engine.get("trials", [])]
        calls = [row.get("call_id") for row in rows]
        approvals = [row["approval"].get("request", {}).get("approval_id")
                     for row in rows if "approval" in row]
        expected_approvals = len(engines) * len(SUITES["approval"]) * 3
        unique = (all(calls) and len(set(calls)) == len(calls) == report["executions_requested"]
                  and all(approvals) and len(set(approvals)) == len(approvals) == expected_approvals)
        report.update(unique_call_and_approval_ids=unique, preflight_passed=report["preflight_passed"] and unique)
    path = run_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in engines:
        print(f"{item['framework']}: {item['executions_passed']}/{3 * len(SUITES[args.suite])}")
    print(path)
    return report["preflight_passed"]


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
