"""
A → B 交接适配器（bounded A handoff adapter）—— 板块②（B）归属

把 ①（A）已经采集、并**由 A 侧独立复查过**的只读产物归一成
`ARSEngine.score_case()` 能直接读取的 case JSON：

    capture_report : rail 采集报告（run_id / requested_claim_count / claim_tasks）
    claims         : A 落盘的 claim 行（id / skill_version / statement / locator）
    resolutions    : S3 回源结果（results[].primary / results[].industry_pe）
    rule_rows      : 宿主（host）提供的规则行，原样写入 case["rules"]

边界（必读）
------------
本适配器**假定**这些 A 输入已经由 A 侧独立复查（例如 M15 的分母、来源与绑定检查）。
它只做形状校验、完整性校验和"可信 / 不可信"降级，**不是认证**：
不验签、不重读原始 CSV、不重算摘要、也不证明 A 的报告没有被篡改。
因此它不构成 C7 的认证凭据：没有独立 verifier、同输入双跑和新鲜度观测时，
R / D / F 一律保持未知（NOT_RUN / None），绝不从 A 的 placeholder 里伪造 C 数据。

唯一能把 L 记成正向的地方
--------------------------
一条 resolution 只有在以下条件**全部**满足时才被当作可信来源
（case claim 里才会出现 locator=RESOLVED、locator_source=resolver）：

  * primary.status 与 industry_pe.status 都是 RESOLVED；
  * primary.source_sha256 与 industry_pe.source_sha256 都是非空字符串且相同；
  * primary.locator 与 claim.locator 的 file / row / field 完全一致；
  * industry_pe.locator 的 file / row 与 claim 一致，field == "industry_pe"。

其他情况一律 fail closed：
  * 有 resolution 但任一绑定不成立  → locator=NOT_FOUND；
  * 没有 claim / claim 没有 locator / 没有 resolution → locator=NOT_RUN。
缺失的请求 claim 不会被丢弃，而是保留 NOT_RUN 占位，以维持请求分母。

本模块不 import ①/③ 的任何包内实现（A 代码），只依赖标准库与 JSON 友好形状。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

ADAPTER_NAME = "a_handoff"
ADAPTER_VERSION = "1.0"

LOC_RESOLVED = "RESOLVED"
LOC_NOT_FOUND = "NOT_FOUND"
LOC_NOT_RUN = "NOT_RUN"
SRC_RESOLVER = "resolver"
RC_NOT_RUN = "NOT_RUN"
_INDUSTRY_FIELD = "industry_pe"


def build_a_case(
    capture_report: Mapping[str, Any],
    claims: Optional[List[Mapping[str, Any]]],
    resolutions: Optional[Mapping[str, Any]],
    rule_rows: Optional[List[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """
    把 A 交接输入转成 B 的 case JSON（不评分、不补造观测）。

    返回 ``{skill_version, rules, claims, checks, meta}``：

    * ``claims`` 的长度恒等于 ``capture_report["claim_tasks"]`` 的长度（完整请求分母）；
    * 每条 claim 都带 A 原始 claim_id；``meta["claim_lineage"]`` 保留每个原始 ID
      对应的 claim/primary/industry_pe locator、是否可信以及降级原因；
    * ``checks`` 恒为 None，claim 的 ``recompute`` 恒为 NOT_RUN、
      ``evidence_fresh`` 恒为 None —— 这些维度没有被 A 观测，不能编造。

    输入假定已由 A 独立复查；本函数只做归一与 fail-closed 绑定校验，
    **不是认证**（不验签、不读源文件、不重算摘要）。

    Raises:
        ValueError: 报告计数与任务数不一致、ID 为空/重复、出现未请求的
            claim/result、或已知 claim 的 skill_version 相互冲突。
    """
    run_id, task_ids = _validate_capture_report(capture_report)
    task_set = set(task_ids)

    claims_by_id = _index_claims(claims, task_set)
    results_by_id = _index_results(resolutions, task_set)
    rules = _normalize_rule_rows(rule_rows)
    skill_version = _resolve_skill_version(claims_by_id)

    signals: List[Dict[str, Any]] = []
    lineage: List[Dict[str, Any]] = []
    trusted_count = 0

    for index, claim_id in enumerate(task_ids):
        claim = claims_by_id.get(claim_id)
        result = results_by_id.get(claim_id)

        claim_locator = claim.get("locator") if claim is not None else None
        claim_fields = _locator_fields(claim_locator)

        primary = _mapping_get(result, "primary")
        industry = _mapping_get(result, "industry_pe")
        primary_locator = _mapping_get(primary, "locator")
        industry_locator = _mapping_get(industry, "locator")

        if claim is None:
            locator = LOC_NOT_RUN
            failure = "CLAIM_MISSING"
        elif claim_fields is None:
            locator = LOC_NOT_RUN
            failure = "CLAIM_LOCATOR_MISSING"
        elif result is None:
            locator = LOC_NOT_RUN
            failure = "RESOLUTION_MISSING"
        elif _trusted_binding(claim_fields, primary, industry):
            locator = LOC_RESOLVED
            failure = None
        else:
            locator = LOC_NOT_FOUND
            failure = "RESOLUTION_BINDING_UNTRUSTED"

        trusted_l = locator == LOC_RESOLVED
        if trusted_l:
            trusted_count += 1

        signals.append(
            {
                "claim_id": claim_id,
                "locator": locator,
                "locator_source": SRC_RESOLVER if trusted_l else "",
                "recompute": RC_NOT_RUN,
                "recompute_by": "",
                "reason_code": None,
                "evidence_fresh": None,
            }
        )
        lineage.append(
            {
                "index": index,
                "claim_id": claim_id,
                "original_claim_id": claim_id,
                "claim_present": claim is not None,
                "resolution_present": result is not None,
                "trusted_l": trusted_l,
                "locator_status": locator,
                "claim_locator": _copy_mapping(claim_locator),
                "primary_locator": _copy_mapping(primary_locator),
                "industry_pe_locator": _copy_mapping(industry_locator),
                "failure_code": failure,
            }
        )

    meta: Dict[str, Any] = {
        "adapter": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "source_run_id": run_id,
        "run_id": run_id,
        "requested_claim_count": len(task_ids),
        "requested_claim_ids": list(task_ids),
        "provided_claim_count": len(claims_by_id),
        "resolution_count": len(results_by_id),
        "trusted_l_count": trusted_count,
        "skill_version": skill_version,
        # 逐 claim 血缘：原始 ID → claim/primary/industry_pe locator 与可信判定。
        # list 保序；mapping 便于按原始 claim_id 直接追溯。
        "claim_lineage": lineage,
        "lineage": lineage,
        "lineage_by_claim_id": {row["claim_id"]: row for row in lineage},
        "notes": [
            "A inputs are assumed to have been independently rechecked by A; "
            "this adapter is not proof of authentication.",
            "R/D/F stay unknown (NOT_RUN / None) until independent verifier, "
            "double-run, and freshness observations exist.",
        ],
    }

    return {
        "skill_version": skill_version,
        "rules": rules,
        "claims": signals,
        "checks": None,
        "meta": meta,
    }


# ============================================================
# 输入校验 / 归一
# ============================================================
def _validate_capture_report(capture_report: Mapping[str, Any]) -> Tuple[str, List[str]]:
    report = _require_mapping(capture_report, "capture_report")
    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(f"capture_report.run_id 必须是非空字符串，收到 {run_id!r}")

    requested = report.get("requested_claim_count")
    if type(requested) is not int or requested < 0:
        raise ValueError(
            f"capture_report.requested_claim_count 必须是非负整数，收到 {requested!r}"
        )

    tasks = report.get("claim_tasks")
    if not isinstance(tasks, (list, tuple)):
        raise ValueError(
            f"capture_report.claim_tasks 必须是列表，收到 {type(tasks).__name__}"
        )
    if requested != len(tasks):
        raise ValueError(
            f"requested_claim_count={requested} 与 claim_tasks 长度 {len(tasks)} 不一致"
        )

    task_ids: List[str] = []
    seen: set = set()
    for position, task in enumerate(tasks):
        row = _require_mapping(task, f"claim_tasks[{position}]")
        claim_id = _nonblank_id(row.get("claim_id"))
        if claim_id is None:
            raise ValueError(f"claim_tasks[{position}].claim_id 必须是非空字符串")
        if claim_id in seen:
            raise ValueError(f"claim_tasks 含重复 claim_id：{claim_id!r}")
        seen.add(claim_id)
        task_ids.append(claim_id)

    return run_id.strip(), task_ids


def _index_claims(
    claims: Optional[List[Mapping[str, Any]]], task_ids: set
) -> Dict[str, Mapping[str, Any]]:
    if claims is None:
        return {}
    if not isinstance(claims, (list, tuple)):
        raise ValueError(f"claims 必须是列表或 None，收到 {type(claims).__name__}")

    by_id: Dict[str, Mapping[str, Any]] = {}
    for position, row in enumerate(claims):
        claim = _require_mapping(row, f"claims[{position}]")
        claim_id = _nonblank_id(claim.get("id"))
        if claim_id is None:
            claim_id = _nonblank_id(claim.get("claim_id"))
        if claim_id is None:
            raise ValueError(f"claims[{position}] 缺少非空 id/claim_id")
        if claim_id not in task_ids:
            raise ValueError(f"claims 含未请求的 claim_id：{claim_id!r}")
        if claim_id in by_id:
            raise ValueError(f"claims 含重复 id：{claim_id!r}")
        by_id[claim_id] = claim
    return by_id


def _index_results(
    resolutions: Optional[Mapping[str, Any]], task_ids: set
) -> Dict[str, Mapping[str, Any]]:
    if resolutions is None:
        return {}
    row = _require_mapping(resolutions, "resolutions")
    results = row.get("results")
    if results is None:
        return {}
    if not isinstance(results, (list, tuple)):
        raise ValueError(f"resolutions.results 必须是列表，收到 {type(results).__name__}")

    by_id: Dict[str, Mapping[str, Any]] = {}
    for position, item in enumerate(results):
        result = _require_mapping(item, f"resolutions.results[{position}]")
        claim_id = _nonblank_id(result.get("claim_id"))
        if claim_id is None:
            raise ValueError(f"resolutions.results[{position}].claim_id 必须是非空字符串")
        if claim_id not in task_ids:
            raise ValueError(f"resolutions 含未请求的 claim_id：{claim_id!r}")
        if claim_id in by_id:
            raise ValueError(f"resolutions 含重复 claim_id：{claim_id!r}")
        by_id[claim_id] = result
    return by_id


def _normalize_rule_rows(
    rule_rows: Optional[List[Mapping[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """规则行由宿主提供；适配器不改写、不补造，只做 JSON 形状复制。"""
    if rule_rows is None:
        return None
    if not isinstance(rule_rows, (list, tuple)):
        raise ValueError(f"rule_rows 必须是列表或 None，收到 {type(rule_rows).__name__}")

    rows: List[Dict[str, Any]] = []
    for position, row in enumerate(rule_rows):
        rule = _require_mapping(row, f"rule_rows[{position}]")
        rows.append(deepcopy(dict(rule)))
    return rows


def _resolve_skill_version(claims_by_id: Mapping[str, Mapping[str, Any]]) -> str:
    """
    已知 claim 的 skill_version 必须一致；没有一条已知时保留空字符串（未知）。

    缺 key/None 视为"该行没有 skill_version 观测"，不参与一致性判断；
    非字符串或空白字符串属于脏输入，直接拒绝。
    """
    versions: Dict[str, List[str]] = {}
    for claim_id, claim in claims_by_id.items():
        if "skill_version" not in claim or claim.get("skill_version") is None:
            continue
        raw = claim.get("skill_version")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"claim {claim_id!r} 的 skill_version 必须是非空字符串，收到 {raw!r}"
            )
        versions.setdefault(raw.strip(), []).append(claim_id)

    if len(versions) > 1:
        detail = "; ".join(
            f"{version!r}={ids}" for version, ids in sorted(versions.items())
        )
        raise ValueError(f"已知 claim 的 skill_version 不一致：{detail}")
    return next(iter(versions), "")


# ============================================================
# 绑定校验（唯一允许 L 记正向的地方）
# ============================================================
def _trusted_binding(
    claim_fields: Tuple[str, int, str],
    primary: Optional[Mapping[str, Any]],
    industry: Optional[Mapping[str, Any]],
) -> bool:
    if not isinstance(primary, Mapping) or not isinstance(industry, Mapping):
        return False
    if _canon_status(primary.get("status")) != LOC_RESOLVED:
        return False
    if _canon_status(industry.get("status")) != LOC_RESOLVED:
        return False

    primary_digest = _normalized_digest(primary.get("source_sha256"))
    industry_digest = _normalized_digest(industry.get("source_sha256"))
    if not primary_digest or primary_digest != industry_digest:
        return False

    primary_fields = _locator_fields(primary.get("locator"))
    industry_fields = _locator_fields(industry.get("locator"))
    if primary_fields is None or industry_fields is None:
        return False

    # primary 必须绑定到 claim 声明的 file:row:field
    if primary_fields != claim_fields:
        return False
    # industry_pe 必须同文件、同行，且字段固定为 industry_pe
    if industry_fields[0] != claim_fields[0] or industry_fields[1] != claim_fields[1]:
        return False
    if industry_fields[2] != _INDUSTRY_FIELD:
        return False
    return True


def _locator_fields(locator: Any) -> Optional[Tuple[str, int, str]]:
    if not isinstance(locator, Mapping):
        return None
    file_name = _nonblank_text(locator.get("file"))
    field = _nonblank_text(locator.get("field"))
    row = _normalized_row(locator.get("row"))
    if file_name is None or field is None or row is None:
        return None
    return (file_name, row, field)


def _canon_status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def _normalized_digest(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _normalized_row(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


# ============================================================
# 小工具
# ============================================================
def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} 必须是 mapping，收到 {type(value).__name__}")
    return value


def _mapping_get(value: Any, key: str) -> Optional[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    item = value.get(key)
    return item if isinstance(item, Mapping) else None


def _copy_mapping(value: Any) -> Optional[Dict[str, Any]]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else None


def _nonblank_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _nonblank_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
