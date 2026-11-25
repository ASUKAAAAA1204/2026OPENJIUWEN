"""Freeze and repeat core/SDK pytest scopes, recording identities and JUnit.

Run with the interpreter for the selected locked environment. This script never
installs dependencies or reclassifies missing SDK imports as skips.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "core": ["tests"],
    "sdk": ["tests", "fixtures/a_s3", "fixtures/a_acceptance"],
}
PLUGIN = "scripts.baseline.c_pytest"


def source_fingerprint():
    suffixes = {".py", ".json", ".jsonl", ".csv", ".toml", ".ts", ".vue", ".md", ".txt", ".yml", ".yaml"}
    files = [ROOT / "pyproject.toml"]
    for directory in ("src", "tests", "fixtures", "scripts", "ui/src", "docs/api", "docs/reviews/baseline", "requirements", ".github/workflows"):
        files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and p.suffix in suffixes)
    payload = [(p.relative_to(ROOT).as_posix(), digest(p)) for p in sorted(set(files))]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def locked_versions(profile):
    path = ROOT / "requirements" / ("m0-jiuwen-py312.lock.txt" if profile == "sdk" else "m1-ledger-py312.lock.txt")
    result = {}
    def read(file):
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r '):
                read(file.parent / line[3:].strip())
                continue
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
            if not match:
                raise ValueError("unsupported lock entry")
            name, expected = match.groups()
            try:
                actual = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                actual = None
            result[name] = {"expected": expected, "installed": actual}
    read(path)
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def junit_counts(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    counts = {"tests": len(cases), "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    for case in cases:
        key = next((name for tag, name in [("failure", "failures"), ("error", "errors"), ("skipped", "skipped")]
                    if case.find(tag) is not None), "passed")
        counts[key] += 1
    return counts


def round_valid(frozen, evidence, counts, returncode, allowed_skips):
    identities_match = evidence["nodeids"] == frozen
    unexpected = [row for row in evidence["skipped"]
                  if (row["nodeid"], row["kind"]) not in allowed_skips]
    valid = (returncode == 0 and evidence["exitstatus"] == 0 and identities_match
             and counts["tests"] == len(frozen) and not counts["failures"]
             and not counts["errors"] and not unexpected)
    return bool(valid), identities_match, unexpected


def run(command, log, environment):
    with log.open("wb") as stream:
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=900)
            return result.returncode
        except subprocess.TimeoutExpired:
            stream.write(b"\nC baseline command timed out after 900 seconds.\n")
            return 124


def write_report(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="core")
    parser.add_argument("--rounds", type=int, choices=[1, 2, 3], default=3)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory under outputs/")
    parser.add_argument("--skip-register", type=Path,
                        default=ROOT / 'docs/reviews/baseline/c-skip-register.json')
    parser.add_argument("--require-lock", action="store_true", help="fail if installed versions differ from the A profile lock")
    args = parser.parse_args(argv)
    destination = args.output.resolve()
    outputs = (ROOT / "outputs").resolve()
    if not destination.is_relative_to(outputs) or destination == outputs:
        parser.error("--output must be a new subdirectory under repository outputs/")
    if destination.exists():
        parser.error("--output already exists; preserve earlier evidence and choose a new directory")
    destination.mkdir(parents=True)
    report = {"schema_version": "temisguard-c-baseline-v1", "status": "FAIL",
              "profile": args.profile, "scopes": PROFILES[args.profile],
              "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
              "requested_rounds": args.rounds, "rounds": [],
              "credentials": "model API credentials removed",
              "network_guard": "pytest process blocks external sockets/DNS; loopback permitted; subprocesses/OS are not isolated"}
    summary = destination / "summary.json"
    try:
        required = ["pytest", "pydantic", "rfc8785", "fastapi", "httpx", "cryptography"]
        if args.profile == "sdk":
            required.append("openjiuwen")
        missing = [name for name in required if importlib.util.find_spec(name) is None]
        report["installed_versions"] = {}
        for name in required:
            try:
                report["installed_versions"][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                report["installed_versions"][name] = None
        report["lock_sha256"] = {p.name: digest(p) for p in sorted((ROOT / "requirements").glob("*.lock.txt"))}
        pins = locked_versions(args.profile)
        report["lock_mismatches"] = {name: values for name, values in pins.items()
                                     if values["expected"] != values["installed"]}
        report["lock_required"] = args.require_lock
        source_hash = report["source_sha256"] = source_fingerprint()
        if missing:
            report.update(status="BLOCKED", missing_dependencies=missing)
            write_report(summary, report)
            print(f"BLOCKED: missing dependencies: {', '.join(missing)}; {summary}")
            return 2
        if args.require_lock and report["lock_mismatches"]:
            raise ValueError("installed dependencies do not match the selected profile lock")
        allowed = set()
        if args.skip_register:
            registry = json.loads(args.skip_register.read_text(encoding="utf-8"))
            if set(registry) != {"schema_version", "items"} or registry["schema_version"] != "temisguard-c-skips-v1":
                raise ValueError("invalid skip-register schema")
            if not isinstance(registry["items"], list):
                raise ValueError("skip-register items must be an array")
            for item in registry["items"]:
                if (not isinstance(item, dict) or set(item) != {"nodeid", "kind", "reason"}
                        or any(not isinstance(item[name], str) or not item[name].strip() for name in item)
                        or item["kind"] not in ("skip", "xfail")):
                    raise ValueError("invalid skip registration")
                key = (item["nodeid"], item["kind"])
                if key in allowed:
                    raise ValueError("duplicate skip registration")
                allowed.add(key)
            report["skip_register_sha256"] = digest(args.skip_register)
        environment = dict(os.environ)
        for name in list(environment):
            if name.upper().endswith(("API_KEY", "API_TOKEN")):
                environment.pop(name)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        check_code = run([sys.executable, "-m", "pip", "check"], destination / "pip-check.log", environment)
        report["pip_check_exit"] = check_code
        if check_code:
            raise ValueError("pip check failed; see pip-check.log")
        collection = destination / "collection.json"
        common = [sys.executable, "-m", "pytest", "-p", PLUGIN, "--c-offline"]
        command = common + ["--collect-only", "-q", "--c-collection-file", str(collection), *PROFILES[args.profile]]
        code = run(command, destination / "collection.log", environment)
        if code or not collection.is_file():
            raise ValueError("collection failed; see collection.log")
        frozen = json.loads(collection.read_text(encoding="utf-8"))["nodeids"]
        if not frozen or len(set(frozen)) != len(frozen) or any("\n" in value for value in frozen):
            raise ValueError("collection must contain unique nonempty nodeids")
        if any(nodeid not in frozen for nodeid, _ in allowed):
            raise ValueError("skip registration contains a test outside the frozen scope")
        nodeids = destination / "nodeids.txt"
        nodeids.write_text("\n".join(frozen) + "\n", encoding="utf-8")
        report.update(collected=len(frozen), nodeids_sha256=digest(nodeids))
        first_outcomes = None
        for number in range(1, args.rounds + 1):
            evidence_path = destination / f"round-{number}-collection.json"
            junit_path = destination / f"round-{number}.xml"
            command = common + ["-q", "@" + str(nodeids), "--c-collection-file", str(evidence_path),
                                "--junitxml", str(junit_path), "--basetemp", str(destination / f"tmp-{number}"),
                                "--durations=10", "--tb=short", "-rs"]
            code = run(command, destination / f"round-{number}.log", environment)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            counts = junit_counts(junit_path)
            valid, same, unexpected = round_valid(frozen, evidence, counts, code, allowed)
            outcomes = (counts, sorted((row["nodeid"], row["kind"]) for row in evidence["skipped"]))
            outcomes_match = first_outcomes is None or outcomes == first_outcomes
            if first_outcomes is None:
                first_outcomes = outcomes
            valid = valid and outcomes_match
            source_matches = source_fingerprint() == source_hash
            valid = valid and source_matches
            report["rounds"].append({"round": number, "returncode": code, **counts,
                                     "identities_match": same, "unexpected_skips": unexpected,
                                     "outcomes_match_first": outcomes_match,
                                     "source_matches": source_matches,
                                     "valid": valid, "junit_sha256": digest(junit_path)})
            print(f"round {number}: {counts}; identities_match={same}; valid={valid}", flush=True)
            if not valid:
                write_report(summary, report)
                return 1
        report["status"] = "PASS"
        if locked_versions(args.profile) != pins:
            raise ValueError("installed package versions changed during the regression rounds")
        write_report(summary, report)
        print(f"PASS: {len(frozen)} frozen nodeids x {args.rounds} rounds; {summary}")
        return 0
    except (ValueError, TypeError, KeyError, OSError, ET.ParseError) as error:
        report["status"] = "FAIL"
        report["error"] = str(error)
        write_report(summary, report)
        print(f"FAIL: {error}; {summary}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
