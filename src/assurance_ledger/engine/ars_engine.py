"""
ARS 打分引擎（ARS-1）—— 板块②（B）归属

它只做三件事：**收计数 → 调口径 → 挂解释**。
公式不在这里，在 rubric.py（唯一口径出处）；解释不在这里，在 explain.py。

为什么这么拆：
  M1 要求"第三方脚本复算差异 = 0"。引擎公开计数与口径，
  由独立脚本检验算术一致性；真实重测结果还取决于动作执行。

两段落点（主 PRD v2.2 §0.3）：
    ARS_d 设计态 = C 单项，跑之前就能出 → 设计预警（C < 0.5 提示，不阻断）
    ARS_r 运行态 = L + R + D + F，跑完证据后出 → 发布闸（ARS < 70 阻断）

C7（S4 → ARS）在 build_counts 里执行：
    L 的分子只来自 locator_source == resolver 的 RESOLVED
    R 的分子只来自 recompute_by == verifier 的 CONSISTENT
    自报/自证的条目被剔除并计入 c7_violations。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..core.ars import ARSCoord, ARSDims, ARSResult, DocStatus
from ..core.rule import RuleSet
from .compiler import RuleCompiler, fixtures_eligible, is_supported_cmp_ir
from .rubric import (
    MIN_SAMPLE,
    REQUIRE_FIXTURES_FOR_C,
    RUBRIC_VERSION,
    Counts,
    ScoreLine,
    rubric_hash,
    score_from_counts,
)
from .signals import LOC_NO_LOCATOR, RC_NOT_RUN, REASON_INSUFFICIENT_EVIDENCE


@dataclass
class CountBundle:
    """一次评分的中间结果 —— 计数 + 归因 + C7 诊断"""

    counts: Counts
    actionable: Dict[str, int] = field(default_factory=dict)
    structural: Dict[str, int] = field(default_factory=dict)
    c7: Dict[str, int] = field(default_factory=dict)
    targets: Dict[str, List[str]] = field(default_factory=dict)


class ARSEngine:
    """
    ARS 打分引擎

    典型用法（③ 板块跑完 S4 之后）：

        engine = ARSEngine()
        result = engine.score(coord, rule_set=rs, claim_signals=claims, check_signals=checks)
        if not result.can_gate:        # PARTIAL / DESIGN_ONLY
            ...                        # 只展示分项，不进闸门比较
    """

    def __init__(self, *, require_fixtures_for_c: bool = REQUIRE_FIXTURES_FOR_C) -> None:
        # 口径唯一（ARS-10）：这些运行期开关会改变评分结果却不改变 rubric hash，
        # 属于非规范评分设置 —— 直接拒绝，不允许"同一 hash 出两种分"。
        if require_fixtures_for_c is not REQUIRE_FIXTURES_FOR_C:
            raise ValueError(
                f"非规范评分设置：require_fixtures_for_c={require_fixtures_for_c!r} "
                f"（口径默认 {REQUIRE_FIXTURES_FOR_C}）会改变 C 的口径但不改变 rubric hash，"
                "拒绝执行"
            )
        self.require_fixtures_for_c = require_fixtures_for_c

    # ==================================================
    # 主入口
    # ==================================================
    def score(
        self,
        coord: ARSCoord,
        *,
        rule_set: Optional[RuleSet] = None,
        claim_signals: Optional[Sequence[Any]] = None,
        check_signals: Optional[Sequence[Any]] = None,
        min_sample: int = MIN_SAMPLE,
    ) -> ARSResult:
        """
        算一次 ARS

        rule_set=None        → C 记 MISSING（规则编译未产出）
        claim_signals=None   → L/R/F 记 MISSING；两个信号都 None 视为"运行态未跑"
        check_signals=None   → D 记 MISSING

        入口校验：评分坐标必须非空；min_sample 只接受口径默认值（非规范设置拒绝执行）；
        重复 claim/check/rule ID 拒绝入分。
        """
        _validate_coord(coord)
        if self.require_fixtures_for_c is not REQUIRE_FIXTURES_FOR_C:
            raise ValueError("noncanonical fixture policy")
        if rule_set is not None and rule_set.skill_version != coord.skill_version:
            raise ValueError("RuleSet skill_version differs from scoring coordinate")
        if min_sample != MIN_SAMPLE:
            raise ValueError(
                f"非规范评分设置：min_sample={min_sample!r}（口径默认 {MIN_SAMPLE}）"
                "会改变 PARTIAL 判定但不改变 rubric hash，拒绝执行"
            )
        bundle = self.build_counts(
            rule_set=rule_set,
            claim_signals=claim_signals,
            check_signals=check_signals,
        )
        line = score_from_counts(bundle.counts, min_sample=min_sample)
        return self._assemble(coord, line, rule_set, bundle)

    #: 设计态专用：只算 C（跑之前就能出，不依赖链路运行）
    def design_only(self, coord: ARSCoord, rule_set: RuleSet) -> ARSResult:
        return self.score(coord, rule_set=rule_set)

    def score_case(self, coord: ARSCoord, case: Mapping[str, Any]) -> ARSResult:
        """
        从一份 case JSON 直接出分

        case 的形状见 engine/signals.py；`fixtures/demo_case.json` 直接喂进来即可，
        这样 `make demo` 与测试走同一条代码路径。
        """
        case_skill = case.get("skill_version")
        if case_skill is not None:
            if not isinstance(case_skill, str) or not case_skill.strip():
                raise ValueError(f"case 的 skill_version 非法：{case_skill!r}")
            if case_skill.strip() != coord.skill_version:
                raise ValueError(
                    f"case 的 skill_version={case_skill!r} 与评分坐标 "
                    f"skill_version={coord.skill_version!r} 不一致，拒绝评分"
                )

        from .signals import CaseSignals

        sig = CaseSignals.from_case(case)
        rule_set: Optional[RuleSet] = None
        if sig.rules is not None:
            rule_set = RuleCompiler().compile_set(sig.rules, coord.skill_version)
        return self.score(
            coord,
            rule_set=rule_set,
            claim_signals=sig.claims,
            check_signals=sig.checks,
        )

    # ==================================================
    # 计数（唯一允许读信号的地方）
    # ==================================================
    def build_counts(
        self,
        *,
        rule_set: Optional[RuleSet] = None,
        claim_signals: Optional[Sequence[Any]] = None,
        check_signals: Optional[Sequence[Any]] = None,
    ) -> CountBundle:
        # ---------- 入口校验（core 冻结 → 引擎侧兜底） ----------
        # 重复 / 空 ID 一律拒绝入分，外部直接构造的 RuleSet 同样过闸
        claims: Optional[List[Any]] = None if claim_signals is None else list(claim_signals)
        checks: Optional[List[Any]] = None if check_signals is None else list(check_signals)
        _validate_rule_set_ingress(rule_set)
        _validate_signal_ids(claims, "claim_id", "claim 信号")
        _validate_signal_ids(checks, "check_id", "check 信号")
        r_run = claims is not None or checks is not None

        # ---------- 缺失维度 ----------
        missing: set = set()
        if rule_set is None:
            missing.add("C")
        if not r_run:
            missing.update({"L", "R", "D", "F"})
        else:
            if claims is None:
                missing.update({"L", "R", "F"})
            if checks is None:
                missing.add("D")

        # ---------- 未知观测 = 该维未完整产出 ----------
        # 单个 None / NOT_RUN 观测就足以让对应维度不能按"已知 0"出完整总分：
        #   evidence_fresh is None          → F 缺测（不能把"未知新鲜度"当作普通分母）
        #   recompute == NOT_RUN            → R 未复算（不能把"没跑"当作 INCONSISTENT）
        #   double_run_consistent is None   → D 未双跑（不能把"没跑"当作不一致）
        # 只把维度标 MISSING，不丢分母、不改已知分子：Counts 仍保留 n_claims/n_checks 与
        # 已知的 L/R/F/D，由口径层产出 PARTIAL（score/band=None）。
        if claims is not None:
            if any(s.evidence_fresh is None for s in claims):
                missing.add("F")
            if any(s.recompute == RC_NOT_RUN for s in claims):
                missing.add("R")
        if checks is not None:
            if any(c.double_run_consistent is None for c in checks):
                missing.add("D")

        # ---------- 五个分子（C7 在这里生效） ----------
        # C 分子走引擎侧闸门：compilable 必须带有效 cmp IR，且 fixture 全绿
        # （外部直接构造的 RuleSet 不经过 core 的宽松口径，也过不了这道闸）
        c_num = (
            _engine_c_numerator(rule_set, self.require_fixtures_for_c)
            if rule_set is not None
            else 0
        )
        counts = Counts(
            n_claims=len(claims or ()),
            n_rules=rule_set.total if rule_set is not None else 0,
            n_checks=len(checks or ()),
            L=sum(1 for s in (claims or ()) if s.counts_for_l),
            C=c_num,
            R=sum(1 for s in (claims or ()) if s.counts_for_r),
            D=sum(1 for c in (checks or ()) if c.counts_for_d),
            F=sum(1 for s in (claims or ()) if s.counts_for_f),
            missing=missing,
            r_run=r_run,
        )

        # ---------- C7 违规（自报 / 自证） ----------
        c7 = {
            "declared_locator": sum(1 for s in (claims or ()) if s.violates_c7_l),
            "self_verified": sum(1 for s in (claims or ()) if s.violates_c7_r),
        }

        actionable: Dict[str, int] = {}
        structural: Dict[str, int] = {}
        targets: Dict[str, List[str]] = {}

        if claims is not None:
            l_deficit = counts.n_claims - counts.L
            r_deficit = counts.n_claims - counts.R
            f_deficit = counts.n_claims - counts.F

            locator_targets = [s.claim_id for s in claims if s.locator_fixable]
            field_targets = [s.claim_id for s in claims if s.needs_add_field]
            rerun_targets = [s.claim_id for s in claims if s.needs_rerun_check]
            stale_targets = [s.claim_id for s in claims if s.needs_rerun_stale]

            # 组间不重叠 + 不超过缺口（否则报价会虚报收益）
            n_add_locator = min(len(locator_targets), l_deficit)
            n_add_field = min(len(field_targets), r_deficit)
            n_rerun_check = min(len(rerun_targets), max(0, r_deficit - n_add_field))
            n_rerun_stale = min(len(stale_targets), f_deficit)

            actionable.update(
                add_locator=n_add_locator,
                add_field=n_add_field,
                rerun_check=n_rerun_check,
                rerun_stale=n_rerun_stale,
            )
            targets.update(
                add_locator=locator_targets[:n_add_locator],
                add_field=field_targets[:n_add_field],
                rerun_check=rerun_targets[:n_rerun_check],
                rerun_stale=stale_targets[:n_rerun_stale],
            )

            structural["no_locator"] = sum(
                1 for s in claims if s.locator == LOC_NO_LOCATOR
            )
            structural["refuted"] = sum(1 for s in claims if s.is_refuted)
            structural["insufficient_evidence"] = sum(
                1 for s in claims if s.reason_code == REASON_INSUFFICIENT_EVIDENCE
            )

        # ---------- C 项：只改写"主观规则"，且不超过缺口 ----------
        if rule_set is not None:
            c_deficit = rule_set.total - c_num
            subjective = rule_set.by_fail_code.get("SUBJECTIVE_RULE", 0)
            n_rewrite = min(subjective, max(0, c_deficit))
            actionable["rewrite_rule"] = n_rewrite
            targets["rewrite_rule"] = [
                r.rule_id
                for r in rule_set.results
                if (not r.compilable)
                and r.fail_code is not None
                and r.fail_code.value == "SUBJECTIVE_RULE"
            ][:n_rewrite]

        # ---------- 未归因的未验证 claim（没有原因码 → 不报价） ----------
        if claims is not None:
            attributed = actionable.get("add_field", 0) + actionable.get("rerun_check", 0)
            unfixed = counts.n_claims - counts.R - structural.get("refuted", 0)
            structural["unattributed"] = max(0, unfixed - attributed)

        return CountBundle(
            counts=counts, actionable=actionable, structural=structural, c7=c7, targets=targets
        )

    # ==================================================
    # 组装结果
    # ==================================================
    def _assemble(
        self,
        coord: ARSCoord,
        line: ScoreLine,
        rule_set: Optional[RuleSet],
        bundle: CountBundle,
    ) -> ARSResult:
        warnings = list(line.warnings)
        c7, structural = bundle.c7, bundle.structural

        if c7.get("declared_locator"):
            warnings.append(
                f"C7：{c7['declared_locator']} 条 claim 的 locator 来源不受信（自报或未知），"
                "已从 L 分子剔除"
            )
        if c7.get("self_verified"):
            warnings.append(
                f"C7：{c7['self_verified']} 条 claim 的复算来源不受信（自证或未知），"
                "已从 R 分子剔除"
            )
        if c7.get("declared_locator") or c7.get("self_verified"):
            # C7：存在不受信来源的证据 → 只出诊断 PARTIAL，不出总分，不进闸门
            line = replace(line, doc_status=DocStatus.PARTIAL, total=None, band=None)
            warnings.append(
                "C7：存在不受信来源的 RESOLVED/CONSISTENT 证据，拒绝出总分"
                "（doc_status=PARTIAL，score/band=None，can_gate=False）"
            )
        if structural.get("unattributed"):
            warnings.append(
                f"{structural['unattributed']} 条未验证 claim 缺原因码，未进改造方案（记结构缺口）"
            )

        result = ARSResult(
            coord=coord,
            dims=line.dims,
            score=line.total,
            band=line.band,
            doc_status=line.doc_status,
            n_claims=line.counts.n_claims,
            n_rules=line.counts.n_rules,
            n_checks=line.counts.n_checks,
            fail_code_counts=dict(rule_set.by_fail_code) if rule_set is not None else {},
            design_phase_score=line.design_points,
            runtime_phase_score=line.runtime_points,
            design_phase_max=25,
            runtime_phase_max=75,
            actionable=dict(bundle.actionable),
            structural=dict(structural),
            targets={k: list(v) for k, v in bundle.targets.items()},
            c7_violations=dict(c7),
            warnings=warnings,
            rubric_version=RUBRIC_VERSION,
            rubric_hash=rubric_hash(),
        )

        # 兼容旧字段：两段切片（不改判定）
        d = line.dims
        result.dims_design_phase = ARSDims(C=d.C.model_copy())
        result.dims_runtime_phase = ARSDims(
            L=d.L.model_copy(), R=d.R.model_copy(), D=d.D.model_copy(), F=d.F.model_copy()
        )

        # 解释（ARS-2）放最后，因为它要读完整结果
        from .explain import build_explains

        result.explains = build_explains(result)
        return result

    # ==================================================
    # 预测 / 守卫
    # ==================================================
    @staticmethod
    def project(counts: Counts, *, min_sample: int = MIN_SAMPLE) -> ScoreLine:
        """给定计数投影一次分数 —— 改造前与改造后用的是同一个公式"""
        if min_sample != MIN_SAMPLE:
            raise ValueError(
                f"非规范评分设置：min_sample={min_sample!r}（口径默认 {MIN_SAMPLE}）"
                "会改变 PARTIAL 判定但不改变 rubric hash，拒绝执行"
            )
        return score_from_counts(counts, min_sample=min_sample)

    @staticmethod
    def counts_from_result(result: ARSResult) -> Counts:
        """从打分结果复原计数（方案器做投影的起点）"""
        d = result.dims
        return Counts(
            n_claims=result.n_claims,
            n_rules=result.n_rules,
            n_checks=result.n_checks,
            L=d.L.numerator or 0,
            C=d.C.numerator or 0,
            R=d.R.numerator or 0,
            D=d.D.numerator or 0,
            F=d.F.numerator or 0,
            missing=set(),
            r_run=True,
        )

    @staticmethod
    def assert_c7(result: ARSResult) -> None:
        """
        出分前断言（契约 C7 的运行时守卫）

        只要存在"自报定位""自证复算"，就拒绝出分 ——
        "拒绝出分"比"出一个好看的分"重要。
        """
        if result.c7_violations.get("declared_locator") or result.c7_violations.get(
            "self_verified"
        ):
            raise ValueError(
                f"C7 violated: L/R 分子包含被测方自证条目，拒绝出分 {result.c7_violations}"
            )


# ============================================================
# 入口校验（core 冻结 → 引擎侧兜底）
# ============================================================
def _validate_coord(coord: ARSCoord) -> None:
    """评分坐标三要素必须是非空字符串 —— 坐标是 ARS 可比性的锚点"""
    for name in ("skill_version", "model", "data_profile"):
        value = getattr(coord, name, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"评分坐标非法：coord.{name} 必须是非空字符串，收到 {value!r}")


def _validate_rule_set_ingress(rule_set: Optional[RuleSet]) -> None:
    """RuleSet 入口校验：rule_id 非空且不重复（含外部直接构造的 RuleSet）"""
    if rule_set is None:
        return
    seen: set = set()
    for result in rule_set.results:
        rid = result.rule_id
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError(f"RuleSet 含空 rule_id：{rid!r}")
        if rid in seen:
            raise ValueError(f"RuleSet 含重复 rule_id：{rid!r}")
        seen.add(rid)


def _validate_signal_ids(items: Optional[Sequence[Any]], attr: str, label: str) -> None:
    """信号入口校验：ID 非空且不重复"""
    if items is None:
        return
    seen: set = set()
    for item in items:
        sid = getattr(item, attr, None)
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError(f"{label}含空 {attr}：{sid!r}")
        if sid in seen:
            raise ValueError(f"{label}含重复 {attr}：{sid!r}")
        seen.add(sid)


def _engine_c_numerator(rule_set: RuleSet, require_fixtures: bool) -> int:
    """
    引擎侧 C 分子：compilable 必须带有效 cmp IR，且 fixture 恰好
    positive/negative/boundary 各一条全绿 —— 外部直接构造的 RuleSet 也过同一道闸，
    伪造的 predicate IR / 三条 positive 全绿都不能把 C 分子做上去。
    """
    n = 0
    for result in rule_set.results:
        if not result.compilable:
            continue
        if not is_supported_cmp_ir(result.ir):
            continue
        if require_fixtures and not fixtures_eligible(result.fixtures):
            continue
        n += 1
    return n
