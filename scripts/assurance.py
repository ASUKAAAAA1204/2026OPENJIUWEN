"""统一 CLI

用法：python scripts/assurance.py <run|replay|check|verify-ledger|gate|export|serve>
stdout 只输出机器可读 JSON；退出码 0 成功 / 2 参数错误 / 3 事实失败 / 4 证据不足 / 5 内部错误
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assurance_ledger.ledger import evaluator  # noqa: E402
from assurance_ledger.ledger.canonical import json_dump, read_json  # noqa: E402
from assurance_ledger.ledger.checker import check_package  # noqa: E402
from assurance_ledger.ledger.claim_map import (  # noqa: E402
    build_rule_set,
    ir_for_claim,
    load_claim_map,
    map_index,
    read_claims,
    rule_ir_by_op,
)
from assurance_ledger.ledger.ledger_verify import verify_file  # noqa: E402
from assurance_ledger.ledger.pipeline import (  # noqa: E402
    DEFAULT_CLAIM_MAP,
    DEFAULT_CLAIMS,
    DEFAULT_OUTPUTS,
    DEFAULT_PACKAGES,
    run as run_pipeline,
)
from assurance_ledger.ledger.reasons import is_known  # noqa: E402
from assurance_ledger.ledger.replay import replay_file  # noqa: E402
from assurance_ledger.ledger.report import decide_gate  # noqa: E402

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_FACT = 3
EXIT_INCOMPLETE = 4
EXIT_INTERNAL = 5


class ArgsError(ValueError):
    pass


def emit(value: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _human_minutes(values: list[str] | None) -> dict[str, float]:
    output: dict[str, float] = {}
    for item in values or []:
        if "=" not in item:
            raise ArgsError(f"--human-minutes 需要 claim=分钟：{item}")
        claim_id, text = item.split("=", 1)
        try:
            minutes = float(text)
        except ValueError as exc:
            raise ArgsError(f"人工分钟不是数值：{item}") from exc
        if claim_id in output:
            raise ArgsError(f"同一 claim 只能记一次人工时间：{claim_id}")
        output[claim_id] = minutes
    return output


def _run(args: argparse.Namespace) -> int:
    result = run_pipeline(
        packages_dir=args.packages,
        claims_path=args.claims,
        claim_map_path=None if args.no_claim_map else args.claim_map,
        outputs_dir=args.outputs,
        run_id=args.run_id,
        model=args.model,
        data_profile=args.data_profile,
        principal=args.principal,
        budget_cu=args.budget_cu,
        waiver=args.waiver,
        waiver_owner=args.waiver_owner,
        waiver_reason=args.waiver_reason,
        waiver_due=args.waiver_due,
        human_minutes=_human_minutes(args.human_minutes),
        target_band=args.target_band,
        policy=args.policy,
    )
    if args.full:
        emit(result)
    else:
        emit(
            {
                "run_id": result["run_id"],
                "output_dir": result["output_dir"],
                "coord": result["coord"],
                "score": result["ars"]["score"],
                "band": result["ars"]["band"],
                "doc_status": result["ars"]["doc_status"],
                "action": result["gate"]["action"],
                "reason_code": result["gate"]["reason_code"],
                "ledger": result["ledger"],
                "metrics": result["metrics"],
                "artifacts": result["artifacts"],
            }
        )
    action = result["gate"]["action"]
    reason = result["gate"]["reason_code"]
    if action == "block":
        return EXIT_INCOMPLETE if reason == "SCORE_PARTIAL" else EXIT_FACT
    return EXIT_OK


def _replay(args: argparse.Namespace) -> int:
    result = replay_file(args.package)
    if result is None:
        emit({"ok": False, "reason": "INSUFFICIENT_EVIDENCE"})
        return EXIT_INCOMPLETE
    emit(result)
    return EXIT_FACT if result.get("status") == evaluator.REFUTED else EXIT_OK


def _check(args: argparse.Namespace) -> int:
    claims = read_claims(args.claims)
    claim_map = load_claim_map(args.claim_map)
    rows = map_index(claim_map)
    skill_version = str(claims[0].get("skill_version") or "a-csv-pe@1.0.0") if claims else "a-csv-pe@1.0.0"
    by_op = rule_ir_by_op(build_rule_set(skill_version))
    document = read_json(args.package)
    claim_id = str((document.get("bundle") or {}).get("claim", {}).get("id"))
    binding = rows.get(claim_id) or {}
    ir = ir_for_claim(binding, by_op)
    result = check_package(document, binding=binding, ir=ir)
    emit(result)
    state = (result.get("adjudication") or {}).get("state")
    return EXIT_OK if state == evaluator.VERIFIED else EXIT_FACT if state == evaluator.REFUTED else EXIT_INCOMPLETE


def _verify_ledger(args: argparse.Namespace) -> int:
    result = verify_file(args.ledger)
    emit(result)
    return EXIT_OK if result["status"] == "PASS" else EXIT_FACT


def _gate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    ars = read_json(run_dir / "ars.json")
    stored = read_json(run_dir / "gate.json")
    recomputed = decide_gate(
        doc_status=ars.get("doc_status"),
        score=ars.get("score"),
        claims_summary=stored.get("claims_summary") or {},
        budget_snapshot=stored.get("budget_snapshot") or {},
        waiver=stored.get("waiver") or {},
    )
    consistent = (
        recomputed["action"] == stored.get("action")
        and recomputed["reason_code"] == stored.get("reason_code")
    )
    emit({"gate": stored, "recomputed": recomputed, "consistent": consistent})
    if not consistent:
        return EXIT_INTERNAL
    if stored.get("reason_code") and not is_known(stored.get("reason_code")):
        return EXIT_INTERNAL
    action = stored.get("action")
    if action == "block":
        return EXIT_INCOMPLETE if stored.get("reason_code") == "SCORE_PARTIAL" else EXIT_FACT
    return EXIT_OK


def _export(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    payload = {
        "report": read_json(run_dir / "report.json"),
        "ars": read_json(run_dir / "ars.json"),
        "plan": read_json(run_dir / "plan.json"),
        "gate": read_json(run_dir / "gate.json"),
    }
    json_dump(payload, args.out)
    emit({"ok": True, "out": str(args.out), "parts": sorted(payload)})
    return EXIT_OK


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        emit({"error": {"code": "MISSING_DEPENDENCY", "message": "需要安装 api 可选依赖"}})
        return EXIT_INTERNAL
    from scripts.serve_api import create_app

    emit({"ok": True, "host": args.host, "port": args.port, "outputs": str(args.outputs)})
    uvicorn.run(create_app(args.outputs), host=args.host, port=args.port, log_level=args.log_level)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assurance", description="保证账本 C 端 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="跑一次完整运行")
    run_parser.add_argument("--packages", default=str(DEFAULT_PACKAGES))
    run_parser.add_argument("--claims", default=str(DEFAULT_CLAIMS))
    run_parser.add_argument("--claim-map", default=str(DEFAULT_CLAIM_MAP))
    run_parser.add_argument("--no-claim-map", action="store_true", help="现场生成映射，仅用于排障")
    run_parser.add_argument("--outputs", default=str(DEFAULT_OUTPUTS))
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--model", default="deepseek-chat")
    run_parser.add_argument("--data-profile", default="a-100:source.csv:100行")
    run_parser.add_argument("--principal", default="local-demo")
    run_parser.add_argument("--budget-cu", type=float)
    run_parser.add_argument("--waiver", action="store_true")
    run_parser.add_argument("--waiver-owner")
    run_parser.add_argument("--waiver-reason")
    run_parser.add_argument("--waiver-due")
    run_parser.add_argument("--human-minutes", action="append", metavar="CLAIM=MINUTES")
    run_parser.add_argument("--target-band", default="B")
    run_parser.add_argument("--policy", default="full", choices=("full", "to_target"))
    run_parser.add_argument("--full", action="store_true", help="输出完整结果")
    run_parser.set_defaults(func=_run)

    replay_parser = sub.add_parser("replay", help="重放一个证据包")
    replay_parser.add_argument("package")
    replay_parser.set_defaults(func=_replay)

    check_parser = sub.add_parser("check", help="检查一个证据包")
    check_parser.add_argument("package")
    check_parser.add_argument("--claims", default=str(DEFAULT_CLAIMS))
    check_parser.add_argument("--claim-map", default=str(DEFAULT_CLAIM_MAP))
    check_parser.set_defaults(func=_check)

    verify_parser = sub.add_parser("verify-ledger", help="独立校验账本")
    verify_parser.add_argument("ledger")
    verify_parser.set_defaults(func=_verify_ledger)

    gate_parser = sub.add_parser("gate", help="读产物并复核发布闸")
    gate_parser.add_argument("--run", required=True)
    gate_parser.set_defaults(func=_gate)

    export_parser = sub.add_parser("export", help="导出前端聚合 JSON")
    export_parser.add_argument("--run", required=True)
    export_parser.add_argument("--out", required=True)
    export_parser.set_defaults(func=_export)

    serve_parser = sub.add_parser("serve", help="启动只读 API")
    serve_parser.add_argument("--outputs", default=str(DEFAULT_OUTPUTS))
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8008)
    serve_parser.add_argument("--log-level", default="info")
    serve_parser.set_defaults(func=_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return int(args.func(args))
    except ArgsError as exc:
        emit({"error": {"code": "INVALID_ARGUMENT", "message": str(exc)}})
        return EXIT_ARGS
    except (FileNotFoundError, ValueError) as exc:
        emit({"error": {"code": "INVALID_INPUT", "message": str(exc)}})
        return EXIT_ARGS
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一转内部错误
        emit({"error": {"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"}})
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
