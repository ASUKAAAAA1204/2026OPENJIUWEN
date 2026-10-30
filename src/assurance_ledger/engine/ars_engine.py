"""
ARS 三段打分引擎 —— PRD 核心算法

关键设计决策：
  ARS_d 设计态（L+C, 跑之前就能出）
  ARS_r 运行态（R+D+F, 跑完证据后出）
  ARS_g 治理态（改造后全量重算，与改造前对比）

  总 ARS = (L×25 + C×25) + (R×20 + D×15 + F×15)
  每段各自算完再相加；N/A 在同段内重分配，跨段不重分配。
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from ..core.types import LocatorLevel, ClaimStatus, CheckKind
from ..core.claim import Claim
from ..core.rule import RuleSet
from ..core.evidence import EvidenceBundle
from ..core.check import CheckResult
from ..core.ars import ARSCoord, ARSResult, ARSDims, ARSBand


# 权重 —— PRD 锁定的常数
WEIGHT_L = 25   # d 态
WEIGHT_C = 25   # d 态
WEIGHT_R = 20   # r 态
WEIGHT_D = 15   # r 态
WEIGHT_F = 15   # r 态


class ARSEngine:
    """
    ARS 打分引擎

    三段分离是结构上最关键的设计决策。
    - ARS_d 不依赖任何链路运行数据（门禁不能等测完再拦）
    - ARS_r 必须等证据包和检查结果齐了才能算
    """

    # ---------- ARS_d：设计态（L + C）----------

    def compute_design_phase(
        self,
        coord: ARSCoord,
        claims: List[Claim],
        rule_set: RuleSet,
    ) -> ARSDims:
        """
        设计态 ARS_d —— **跑之前就能出**

        L（可定位率）：有精确 locator 的 claim / 总 claim
        C（可编译率）：compilable=True 且过 fixture 的规则 / 总规则
        """
        dims = ARSDims()

        # === L ===
        if claims:
            precise_count = sum(
                1 for c in claims
                if c.locator and c.locator.is_precise
            )
            dims.locatable_rate = precise_count / len(claims)
        else:
            dims.locatable_rate = None  # 分母 0 → N/A，同段内重分配

        # === C ===
        if rule_set.total > 0:
            # C = compilable 数 / 总规则数
            # （fixture 通过在 S1 完成后补进 RuleCompileResult，
            #   初版先按 compilable 计）
            dims.compilable_rate = rule_set.compilable_count / rule_set.total
        else:
            dims.compilable_rate = None

        return dims

    # ---------- ARS_r：运行态（R + D + F）----------

    def compute_runtime_phase(
        self,
        bundles: List[EvidenceBundle],
        check_results: List[CheckResult],
    ) -> ARSDims:
        """
        运行态 ARS_r —— 跑完证据后出

        R（可复算率）：status=VERIFIED 的 claim / 总 claim
        D（确定性率）：is_deterministic=True 的检查 / 总检查
        F（新鲜度）：freshness=fresh 的 claim / 总 claim
        """
        dims = ARSDims()

        # === R ===
        claim_ids = {b.claim.id for b in bundles}
        verified_ids = set()
        for b in bundles:
            if b.adjudication and b.adjudication.state == ClaimStatus.VERIFIED:
                verified_ids.add(b.claim.id)
        if claim_ids:
            dims.recomputable_rate = len(verified_ids) / len(claim_ids)
        else:
            dims.recomputable_rate = None

        # === D ===
        if check_results:
            det_count = sum(1 for c in check_results if c.is_deterministic)
            dims.deterministic_rate = det_count / len(check_results)
        else:
            dims.deterministic_rate = None

        # === F ===
        if bundles:
            fresh_count = sum(
                1 for b in bundles
                for e in b.evidence
                if e.freshness == "fresh"
            )
            total_evidence = sum(len(b.evidence) for b in bundles)
            dims.freshness_rate = fresh_count / max(total_evidence, 1)
        else:
            dims.freshness_rate = None

        return dims

    # ---------- 合成 ----------

    def combine(
        self,
        coord: ARSCoord,
        claims: List[Claim],
        rule_set: RuleSet,
        bundles: List[EvidenceBundle],
        check_results: List[CheckResult],
        *,
        dims_design: Optional[ARSDims] = None,
        dims_runtime: Optional[ARSDims] = None,
    ) -> ARSResult:
        """
        合成完整 ARS —— 发布闸用

        两段各自算完再相加；N/A 在同段内重分配。
        """
        dims_d = dims_design or self.compute_design_phase(coord, claims, rule_set)
        dims_r = dims_runtime or self.compute_runtime_phase(bundles, check_results)

        warnings = []

        # === 设计态分数（满分 50）===
        d_score = self._weighted_sum(
            rates=[dims_d.locatable_rate, dims_d.compilable_rate],
            weights=[WEIGHT_L, WEIGHT_C],
            segment="design",
            warnings=warnings,
        )

        # === 运行态分数（满分 50）===
        r_score = self._weighted_sum(
            rates=[dims_r.recomputable_rate, dims_r.deterministic_rate, dims_r.freshness_rate],
            weights=[WEIGHT_R, WEIGHT_D, WEIGHT_F],
            segment="runtime",
            warnings=warnings,
        )

        total = round(d_score + r_score)
        band = self._score_to_band(total)

        # 样本不足 warning
        if len(claims) < 10:
            warnings.append(f"样本不足：仅 {len(claims)} 条 claim，分档仅供参考")

        return ARSResult(
            coord=coord,
            dims=ARSDims(
                locatable_rate=dims_d.locatable_rate,
                compilable_rate=dims_d.compilable_rate,
                recomputable_rate=dims_r.recomputable_rate,
                deterministic_rate=dims_r.deterministic_rate,
                freshness_rate=dims_r.freshness_rate,
            ),
            score=total,
            band=band,
            dims_design_phase=dims_d,
            dims_runtime_phase=dims_r,
            n_claims=len(claims),
            n_rules=rule_set.total,
            warnings=warnings,
            design_phase_score=round(d_score),
            runtime_phase_score=round(r_score),
        )

    # ---------- 内部工具 ----------

    def _weighted_sum(
        self,
        rates: List[Optional[float]],
        weights: List[int],
        segment: str,
        warnings: List[str],
    ) -> float:
        """
        加权求和 —— N/A 在同段内重分配

        规则：
          - 跳过 None 项
          - 有效权重按比例放大到 100（同段内）
        """
        valid_pairs = [(r, w) for r, w in zip(rates, weights) if r is not None]
        if not valid_pairs:
            warnings.append(f"{segment} 段所有维度 N/A，按 0 处理")
            return 0.0

        total_weight = sum(w for _, w in valid_pairs)
        raw = sum(r * w for r, w in valid_pairs)

        if total_weight != sum(weights):
            # 存在 N/A 项 → 同段内重分配
            scale = sum(weights) / total_weight
            warnings.append(
                f"{segment} 段存在 N/A，权重按比例重分配（{total_weight}→{sum(weights)}）"
            )
            raw *= scale

        return raw

    @staticmethod
    def _score_to_band(score: int) -> str:
        if score >= 85: return ARSBand.A
        if score >= 70: return ARSBand.B
        if score >= 50: return ARSBand.C
        return ARSBand.D
