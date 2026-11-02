"""
抗博弈（ARS-9）—— 板块②（B）归属

五项规则用于标记可疑计数和缺失检查；不能单凭这些规则宣称不可刷分。
M14 目标仍需独立红队样本验证：10 个样本中至少 8 个被标记。

全部用**绝对条数**判定，不用比率（ARS PRD §15.2）：
比率在"L 涨、resolved 不动"时会同时变大变小，逻辑上不可能触发。

    H1 held-out    20 条里 5 条不展示给 Skill 作者；held-out 与上报 L 相差 ≤2 条
    H2 抽查        locator 人工回源（demo 全量 / 生产 10%），一致率 ≥95%
    H3 真实性差    L 分子 − resolved 条数 ≤3（C7 的绝对量版本）
    H4 涨分/涨真实 改造后 L 涨、resolved 不涨 → 标 gaming，该次增益不计
    H5 拆分检测    等价琐碎谓词不计 C 增益；抽检 10 条，命中即回退该规则
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: 判定阈值（写死，改则口径版本必须变）
H1_TOLERANCE = 2          # 条
H2_MIN_RATE = 0.95
H3_TOLERANCE = 3          # 条（≈0.15 × 20）
H5_SAMPLE = 10


@dataclass
class Finding:
    """一条抗博弈检查的结论"""

    code: str
    name: str
    flagged: bool
    detail: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    blocks_gain: bool = False   # True 表示这一次改造的增益不计
    evaluated: bool = True

    def to_payload(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "flagged": self.flagged,
            "detail": self.detail,
            "metrics": dict(self.metrics),
            "blocks_gain": self.blocks_gain,
            "evaluated": self.evaluated,
        }


@dataclass
class AntiGamingReport:
    findings: List[Finding] = field(default_factory=list)

    @property
    def flagged(self) -> List[str]:
        return [f.code for f in self.findings if f.flagged]

    @property
    def flagged_count(self) -> int:
        """本份报告命中的机制数；不是 M14 的被检出样本数。"""
        return len(self.flagged)

    @property
    def needs_review(self) -> bool:
        """H1 失败 → 整份报告标"待核"（不是扣分，是失去可比性）"""
        return any(not f.evaluated or f.flagged for f in self.findings)

    @property
    def gaming(self) -> bool:
        """H4 / H5 命中 → 这次增益不算"""
        return any(f.flagged and f.blocks_gain for f in self.findings)

    @property
    def blocked_gain(self) -> bool:
        return any(f.flagged and f.blocks_gain for f in self.findings)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "findings": [f.to_payload() for f in self.findings],
            "flagged": self.flagged,
            "flagged_count": len(self.flagged),
            "needs_review": self.needs_review,
            "gaming": self.gaming,
        }


# ============================================================
# 五项检查
# ============================================================
def h1_heldout(
    reported_l_num: int,
    n_claims: int,
    heldout_resolved: int,
    n_heldout: int,
    tolerance: int = H1_TOLERANCE,
) -> Finding:
    """
    H1：held-out 与上报 L 相差 ≤2 条

    把 held-out 子集的一致率按 20 条折算成"条数"，与上报的 L 分子比绝对差。
    差超过 2 条，说明"上报的 L 只在不被看的那几条上不成立"——
    这时不该扣分，而该让整份报告失去可比性（标"待核"）。
    """
    if n_heldout <= 0 or n_claims <= 0:
        return Finding("H1", "held-out 一致性", False, "样本不足，未做 held-out 检验", {}, evaluated=False)

    expected = heldout_resolved / n_heldout * n_claims
    diff = abs(reported_l_num - expected)
    flagged = diff > tolerance
    return Finding(
        "H1",
        "held-out 一致性",
        flagged,
        f"held-out {n_heldout} 条里 {heldout_resolved} 条解析成功，"
        f"折算 {expected:.1f} 条，与上报 L 分子 {reported_l_num} 相差 {diff:.1f} 条"
        + ("（> 2 条，整份报告标待核）" if flagged else "（≤ 2 条，通过）"),
        {
            "reported_l_num": reported_l_num,
            "n_claims": n_claims,
            "heldout_resolved": heldout_resolved,
            "n_heldout": n_heldout,
            "expected_l_num": round(expected, 2),
            "diff": round(diff, 2),
            "tolerance": tolerance,
        },
    )


def h2_spotcheck(checked: int, agreed: int, min_rate: float = H2_MIN_RATE) -> Finding:
    """H2：人工回源抽查一致率 ≥95%"""
    if checked <= 0:
        return Finding("H2", "locator 抽查", False, "未做抽查", {}, evaluated=False)
    rate = agreed / checked
    flagged = rate < min_rate
    return Finding(
        "H2",
        "locator 抽查",
        flagged,
        f"抽查 {checked} 条，一致 {agreed} 条，一致率 {rate:.0%}"
        + ("（< 95%）" if flagged else "（≥ 95%，通过）"),
        {"checked": checked, "agreed": agreed, "rate": round(rate, 4)},
    )


def h3_c7_gap(reported_l_num: int, resolved_num: int, tolerance: int = H3_TOLERANCE) -> Finding:
    """
    H3：L 分子 − resolved 条数 ≤3

    在 C7 生效时这个差恒为 0（L 分子就是 resolved 条数）。
    所以 H3 实际上是一道**接线检查**：差值 > 0 说明有人绕过 resolver 直接写分子。
    """
    gap = reported_l_num - resolved_num
    flagged = gap > tolerance
    return Finding(
        "H3",
        "真实性差（L 分子 vs resolved 条数）",
        flagged,
        f"L 分子 {reported_l_num}，resolved {resolved_num}，差 {gap} 条"
        + ("（> 3 条，疑似绕过 resolver）" if flagged else "（≤ 3 条，通过）"),
        {"reported_l_num": reported_l_num, "resolved_num": resolved_num,
         "gap": gap, "tolerance": tolerance},
    )


def h4_gain_vs_truth(
    before_l_num: int,
    before_resolved: int,
    after_l_num: int,
    after_resolved: int,
) -> Finding:
    """
    H4：改造后 L 涨、resolved 不涨 → 标 gaming，增益不计

    典型作弊：把 locator 写成"看起来精确"的格式，但值不回源。
    """
    d_l = after_l_num - before_l_num
    d_resolved = after_resolved - before_resolved
    flagged = d_l > 0 and d_resolved <= 0
    return Finding(
        "H4",
        "涨分 vs 涨真实",
        flagged,
        f"改造后 L {before_l_num}→{after_l_num}（{d_l:+d}），"
        f"resolved {before_resolved}→{after_resolved}（{d_resolved:+d}）"
        + ("；只涨分不涨真实 → 标 gaming，本次增益不计" if flagged else "；通过"),
        {"delta_l": d_l, "delta_resolved": d_resolved},
        blocks_gain=flagged,
    )


def h5_split_detect(hits: int, sampled: int = H5_SAMPLE) -> Finding:
    """
    H5：拆分检测 —— 把一条主观规则拆成若干等价琐碎谓词，C 会虚涨

    抽检 10 条，命中即回退该规则（C 增益不计）。
    """
    if sampled <= 0:
        return Finding("H5", "拆分检测", False, "未做抽检", {}, evaluated=False)
    flagged = hits > 0
    return Finding(
        "H5",
        "拆分检测",
        flagged,
        f"抽检 {sampled} 条，命中等价琐碎谓词 {hits} 条"
        + ("；命中即回退该规则的 C 增益" if flagged else "；通过"),
        {"hits": hits, "sampled": sampled},
        blocks_gain=flagged,
    )


# ============================================================
# 汇总
# ============================================================
def evaluate(
    *,
    reported_l_num: int,
    n_claims: int,
    resolved_num: int,
    heldout_resolved: Optional[int] = None,
    n_heldout: int = 0,
    spot_checked: int = 0,
    spot_agreed: int = 0,
    before_l_num: Optional[int] = None,
    before_resolved: Optional[int] = None,
    after_l_num: Optional[int] = None,
    after_resolved: Optional[int] = None,
    split_hits: int = 0,
    split_sampled: int = 0,
) -> AntiGamingReport:
    """跑全部五项 —— 报告的"抗博弈"区块就吃这个"""
    counts = {"reported_l_num": reported_l_num, "n_claims": n_claims,
              "resolved_num": resolved_num, "n_heldout": n_heldout,
              "spot_checked": spot_checked, "spot_agreed": spot_agreed,
              "split_hits": split_hits, "split_sampled": split_sampled}
    optional = {"heldout_resolved": heldout_resolved, "before_l_num": before_l_num,
                "before_resolved": before_resolved, "after_l_num": after_l_num,
                "after_resolved": after_resolved}
    for name, value in {**counts, **optional}.items():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{name} must be a non-negative integer")
    for numerator, denominator in ((reported_l_num, n_claims), (resolved_num, n_claims),
        (n_heldout, n_claims), (heldout_resolved or 0, n_heldout),
        (spot_agreed, spot_checked), (split_hits, split_sampled)):
        if numerator > denominator:
            raise ValueError("anti-gaming count exceeds its sample size")
    findings: List[Finding] = []

    if heldout_resolved is not None and n_heldout > 0:
        findings.append(h1_heldout(reported_l_num, n_claims, heldout_resolved, n_heldout))
    else:
        findings.append(Finding("H1", "held-out 一致性", False, "未提供 held-out 结果", evaluated=False))
    findings.append(h2_spotcheck(spot_checked, spot_agreed))
    findings.append(h3_c7_gap(reported_l_num, resolved_num))

    if None not in (before_l_num, before_resolved, after_l_num, after_resolved):
        findings.append(
            h4_gain_vs_truth(
                before_l_num, before_resolved, after_l_num, after_resolved  # type: ignore[arg-type]
            )
        )
    else:
        findings.append(Finding("H4", "涨分 vs 涨真实", False, "未提供完整的前后计数", evaluated=False))
    findings.append(h5_split_detect(split_hits, split_sampled))

    return AntiGamingReport(findings=findings)


def discount_gain(gain: float, report: AntiGamingReport) -> float:
    """被 H4/H5 判为 gaming 时，这次改造的增益不计（返回 0.0）"""
    return 0.0 if report.blocked_gain else gain
