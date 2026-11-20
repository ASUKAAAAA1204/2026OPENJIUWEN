"""Run the B M0 preflight for the registered file-write tool (S01–S03)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone
from tempfile import mkdtemp

from adapters import ADAPTERS

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


def run_framework(root: Path, framework: str, python: Path) -> dict:
    work = root / framework
    work.mkdir()
    summary = {"framework": framework, "passed": False, "executions_passed": 0,
               "scenarios_passed": 0, "executions_measured": 0}
    try:
        with (work / "sdk.log").open("wb") as log:
            process = subprocess.run([str(python.resolve()), "-B", str(HERE / "worker.py"), framework],
                                     cwd=work, env=offline_environment(), stdout=log, stderr=subprocess.STDOUT,
                                     timeout=180, check=False)
        detail = json.loads((work / "result.json").read_text(encoding="utf-8"))
        rows = detail["trials"]
        expected_grid = {(s, r) for s in ("S01", "S02", "S03") for r in (1, 2, 3)}
        passes = {(r["scenario"], r["repeat"]) for r in rows if r["passed"]}
        summary.update(executions_passed=len(passes), executions_measured=len(rows),
                       scenarios_passed=sum(all((s, r) in passes for r in (1, 2, 3))
                                            for s in ("S01", "S02", "S03")),
                       sdk_version=detail.get("sdk_version"), python=detail["python"],
                       details=f"{framework}/result.json",
                       passed=process.returncode == 0 and len(rows) == 9 and passes == expected_grid)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return summary


def main() -> bool:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "b-m0")
    parser.add_argument("--framework", choices=ADAPTERS, help="Run one SDK for diagnosis")
    for framework in ADAPTERS:
        parser.add_argument(f"--{framework}-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mkdtemp(prefix="control-", dir=args.output.resolve()))
    frameworks = [args.framework] if args.framework else list(ADAPTERS)
    engines = [run_framework(run_dir, name, getattr(args, name + "_python")) for name in frameworks]
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    report = {
        "format": "b-m0-control-points-v1", "scope": "registered local tool, S01/S02/S03 preflight",
        "completed_at": datetime.now(timezone.utc).isoformat(), "git_head": head, "os": platform.platform(),
        "sources_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (HERE / "run.py", HERE / "adapters.py", HERE / "worker.py")},
        "frameworks": engines, "scenarios_passed": sum(f["scenarios_passed"] for f in engines),
        "scenarios_requested": 3 * len(engines), "executions_passed": sum(f["executions_passed"] for f in engines),
        "executions_requested": 9 * len(engines), "online_model_calls": 0,
        "preflight_passed": all(f["passed"] for f in engines),
    }
    path = run_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in engines:
        print(f"{item['framework']}: {item['executions_passed']}/9")
    print(path)
    return report["preflight_passed"]


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
