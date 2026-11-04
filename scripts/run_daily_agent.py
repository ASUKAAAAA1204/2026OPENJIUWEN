"""A 的采集验证入口；默认本地替身，--mode live 才调用模型服务。"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="openJiuwen CSV 采集验证：本地替身或 DeepSeek 真实模型")
    parser.add_argument("--mode", choices=["local", "live"], default="local")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    check = parser.add_mutually_exclusive_group()
    check.add_argument("--check-config", action="store_true", help="仅检查本地模型配置，不联网、不打印密钥")
    check.add_argument("--resolve-only", type=Path, metavar="RUN_DIRECTORY",
                       help="从已有运行目录重新读取来源，不调用模型，也不覆盖原始结果")
    check.add_argument("--pack-only", type=Path, metavar="RUN_DIRECTORY",
                       help="从已有运行导出自包含证据包，不调用模型")
    check.add_argument("--check-package", type=Path, metavar="PACKAGE_JSON",
                       help="检查 A 证据包的内容摘要，不执行事实判定或账本校验")
    check.add_argument("--acceptance", action="store_true", help="运行 A 的批量验收；默认本地模拟，显式 live 才使用模型额度")
    check.add_argument("--recheck-acceptance", type=Path, metavar="RUN_DIRECTORY",
                       help="用最新 M5/M15/M10 判据离线复查已有采集，写新报告，不调用模型")
    parser.add_argument("--acceptance-claims", type=int, default=100, help="验收样例任务数，默认 100")
    parser.add_argument("--model-call-limit", type=int, default=220, help="验收批次的模型调用硬上限，默认 220")
    rule_input = parser.add_mutually_exclusive_group()
    rule_input.add_argument("--rule-file", type=Path, help="打包时附带规则 JSON 快照；不编译或执行其中的规则")
    rule_input.add_argument("--b-rule-return", type=Path, metavar="B_RETURN_DIRECTORY",
                           help="按 B R1 返回清单校验候选规则与字段绑定；不替换冻结接口，不判定自然语言结论")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    company = parser.add_mutually_exclusive_group()
    company.add_argument("--company", default="A")
    company.add_argument("--companies", nargs="+", help="每家公司对应一个独立结论任务，例如 A B C")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=1,
                        help="批次内同时运行的任务数，默认 1，最多 4")
    args = parser.parse_args()
    if args.rule_file and not args.pack_only:
        parser.error("--rule-file 仅用于 --pack-only")
    if args.b_rule_return and not args.pack_only:
        parser.error("--b-rule-return 仅用于 --pack-only")
    if args.recheck_acceptance:
        from assurance_ledger.rail.acceptance import recheck_acceptance
        try:
            report = recheck_acceptance(args.recheck_acceptance,
                                        args.output or root / "outputs" / "acceptance_rechecks")
        except (OSError, ValueError, KeyError, TypeError):
            parser.exit(2, "验收复查未完成：请检查运行目录、任务清单和 JSON 文件。\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in {"passed", "local_validated"} else 1
    if args.pack_only or args.check_package:
        from assurance_ledger.rail.evidence_io import read_json
        try:
            if args.pack_only:
                from assurance_ledger.rail.evidence_capture import export_capture_packages
                report = export_capture_packages(args.pack_only, args.output,
                                                  rule=read_json(args.rule_file) if args.rule_file else None,
                                                  b_rule_return=args.b_rule_return)
            else:
                from assurance_ledger.ledger.packer import check_package_integrity
                report = check_package_integrity(read_json(args.check_package))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            parser.exit(2, "证据处理未完成：请检查输入 JSON、运行目录，以及 rail/requirements-evidence.txt 依赖。\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.resolve_only:
        from assurance_ledger.rail.source_checks import resolve_capture
        try:
            report = resolve_capture(args.resolve_only)
            result_file = args.resolve_only / f"resolutions-recheck-{uuid4().hex}.json"
            result_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError, KeyError, TypeError):
            parser.exit(2, "无法复查：运行目录缺少有效的 claims.json、claim_sources.json，或文件无法读取。\n")
        print(json.dumps({**report, "result_file": str(result_file.resolve())}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.companies and (any(not name.strip() for name in args.companies)
                           or len(set(args.companies)) != len(args.companies)):
        parser.error("--companies 不能为空或包含重复公司")
    if not args.companies and not args.acceptance and args.concurrency != 1:
        parser.error("--concurrency 需要配合 --companies 使用")
    if args.acceptance and (args.companies or args.source):
        parser.error("--acceptance 使用版本固定的批量样例，不与 --companies/--source 混用")
    settings = None
    try:
        if args.mode == "live" or args.check_config:
            from assurance_ledger.rail.model_config import load_deepseek_settings, ModelConfigError
            try:
                settings = load_deepseek_settings(args.env_file)
            except ModelConfigError as exc:
                parser.exit(2, f"配置未就绪：{exc}\n")
            if args.check_config:
                print(json.dumps({"status": "config_valid", "connection_tested": False,
                                  **settings.public_summary()}, ensure_ascii=False, indent=2))
                return 0
        from assurance_ledger.rail.local_spike import run_local_spike, run_live_spike, run_claim_batch
    except ImportError as exc:
        parser.exit(2, f"缺少运行依赖：{exc.name}。请使用 Python 3.11–3.13 安装 .[spike]。\n")
    output = args.output or root / "outputs" / ("rail_live" if args.mode == "live" else "rail_spike")
    if args.acceptance:
        from assurance_ledger.rail.acceptance import run_acceptance
        try:
            report = asyncio.run(run_acceptance(output, settings=settings, claims=args.acceptance_claims,
                                                concurrency=args.concurrency, model_call_limit=args.model_call_limit))
        except (ValueError, OSError) as exc:
            parser.exit(2, f"批量验收输入或落盘未就绪：{type(exc).__name__}。\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in {"passed", "local_validated"} else 1
    source = args.source or root / "fixtures/rail_spike" / ("market_multi.csv" if args.companies else "market.csv")
    if args.companies:
        report = asyncio.run(run_claim_batch(source, output, companies=args.companies,
                                            settings=settings, concurrency=args.concurrency))
    elif args.mode == "live":
        report = asyncio.run(run_live_spike(source, output, company=args.company, settings=settings))
    else:
        report = asyncio.run(run_local_spike(source, output, company=args.company))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
