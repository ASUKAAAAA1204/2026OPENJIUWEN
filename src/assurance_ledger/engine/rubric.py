"""
口径（rubric）：常量、纯计数公式、口径版本与 hash —— 板块②（B）归属

本模块是**唯一的口径出处**。它只做一件事：

    计数 → 五维比例 → 段内加权（N/A 重分配）→ 总分 → 分档

不读数据库、不读链路对象、不调 LLM、无副作用。
这样任何第三方脚本都能用同一套常量复算出同一个数（M1 复算差异 = 0），
而"分数是我自己定的"这句质疑只能靠口径公开来回答（ARS-10）。

红线：只报整数与分档；PARTIAL / DESIGN_ONLY 不出总分。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import Any, Dict, List, Optional, Set

from ..core.ars import ARSBand, ARSDims, Dim, DimStatus, DocStatus

# ============================================================
# 口径常量 —— 改这里 = 改口径，必须升 RUBRIC_VERSION
# ============================================================
RUBRIC_VERSION = "ars-1.2"

WEIGHTS: Dict[str, int] = {"L": 25, "C": 25, "R": 20, "D": 15, "F": 15}

#: 两段各算各的；跨段不重分配
SEGMENTS: Dict[str, tuple] = {
    "design": ("C",),                 # 设计态只算 C（满分 25）
    "runtime": ("L", "R", "D", "F"),  # 运行态 L/R/D/F（满分 75）
}
SEGMENT_MAX: Dict[str, int] = {
    seg: sum(WEIGHTS[k] for k in keys) for seg, keys in SEGMENTS.items()
}

#: 分档（只报整数，不报小数）
BANDS: tuple = (("A", 85), ("B", 70), ("C", 50))

#: 发布闸阈值（硬闸：ARS < 70 阻断）
GATE_THRESHOLD = 70
#: 设计预警阈值（软约束：C < 0.5 标低分，design_ack 确认继续，不阻断）
DESIGN_WARN_THRESHOLD = 0.5
#: 样本不足阈值：n_claims < 10 时只报分项，不出总分与分档
MIN_SAMPLE = 10
#: 边际枯竭阈值：低于 0.5 分/CU 的动作不再进方案
MARGIN_FLOOR = 0.5
#: 报价缓冲：直接成本 × 5%
BUFFER_RATE = 0.05
#: S1：每条检查 3 个 fixture（正/反/边界）
FIXTURES_PER_CHECK = 3
#: C 是否要求 fixture 全绿才计入分子（v1.2 起为 True；False 只给旧数据兼容用）
REQUIRE_FIXTURES_FOR_C = True

# ============================================================
# 改造动作库（ARS PRD §8，单位价格写死）
# ============================================================
#: 每个动作影响的维度、单价（CU/条）、说明
ACTION_LIBRARY: Dict[str, Dict[str, Any]] = {
    "add_locator": {
        "cu": 0.7,
        "affects": ["L"],
        "label": "补定位",
        "note": "把结论定位到 file:row:field，并回源解析成功才计入 L",
    },
    "rewrite_rule": {
        "cu": 3.0,
        "affects": ["C", "D"],
        "label": "原地改写规则（含 1 check + 3 fixture）",
        "note": "把不可判词改成可判谓词；n_rules 恒不变，n_checks +1/条",
    },
    "add_field": {
        "cu": 2.0,
        "affects": ["R"],
        "label": "补字段",
        "note": "字段已在源里，只需映射进证据包",
    },
    "rerun_check": {
        "cu": 1.0,
        "affects": ["R"],
        "label": "重跑检查",
        "note": "输入已更新、检查可跑；把 indeterminate 变 verified",
    },
    "rerun_stale": {
        "cu": 1.0,
        "affects": ["F"],
        "label": "重跑过期证据",
        "note": "过期证据可重新采集，提升 claim 级新鲜度",
    },
}

#: 动作执行的固定顺序（报告展示用；贪心排序另按 分/CU）
ACTION_ORDER: tuple = ("add_locator", "rewrite_rule", "add_field", "rerun_check", "rerun_stale")


def rubric_definition() -> Dict[str, Any]:
    """口径的完整定义 —— 参与 hash，公开给第三方复算用"""
    return {
        "version": RUBRIC_VERSION,
        "implementation_revision": "a-handoff-2026-11-02",
        "unknown_observations": "R_NOT_RUN_D_NULL_F_NULL_are_MISSING",
        "rounding": "exact-rational-half-up",
        "missing_reallocation": False,
        "weights": dict(WEIGHTS),
        "segments": {k: list(v) for k, v in SEGMENTS.items()},
        "segment_max": dict(SEGMENT_MAX),
        "bands": [list(b) for b in BANDS],
        "gate_threshold": GATE_THRESHOLD,
        "design_warn_threshold": DESIGN_WARN_THRESHOLD,
        "min_sample": MIN_SAMPLE,
        "margin_floor": MARGIN_FLOOR,
        "buffer_rate": BUFFER_RATE,
        "fixtures_per_check": FIXTURES_PER_CHECK,
        "require_fixtures_for_c": REQUIRE_FIXTURES_FOR_C,
        "actions": {k: {"cu": v["cu"], "affects": list(v["affects"])} for k, v in ACTION_LIBRARY.items()},
    }


def rubric_hash() -> str:
    """
    口径指纹 —— 写进账本，让分数可追溯到口径版本（ARS-10）

    换权重、换阈值、换单价，这个 hash 就变；报告里分数与 hash 必须同时出现。
    """
    canonical = json.dumps(
        rubric_definition(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ============================================================
# 计数 → 分数
# ============================================================
#: 每个维度的分母来自哪个计数字段
_DENOM_FIELD: Dict[str, str] = {
    "C": "n_rules",
    "L": "n_claims",
    "R": "n_claims",
    "D": "n_checks",
    "F": "n_claims",
}


def round_half_up(value: float, digits: int = 0) -> float:
    """
    四舍五入（round half up）—— 与 PRD 一致

    Python 内置 round() 是 banker's rounding（79.5 → 80 但 78.5 → 78），
    这里必须用 Decimal 才能保证"47.00 → 47、79.46 → 79"在任何环境一致。
    """
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def band_of(score: int) -> str:
    """整数分 → 分档"""
    for name, low in BANDS:
        if score >= low:
            return name
    return ARSBand.D


@dataclass
class Counts:
    """
    一次评分所需的**全部**计数 —— 这就是"可复算"的输入

    任何人拿到这 8 个整数 + missing/r_run 两个标记，就能算出同样的分。
    """

    n_claims: int = 0      # 总 claim 数（L/R/F 的分母）
    n_rules: int = 0       # 总规则数（C 的分母）
    n_checks: int = 0      # 总检查数（D 的分母）

    L: int = 0             # 回源解析成功的 claim 数（只来自 resolver 的 RESOLVED）
    C: int = 0             # 编译通过且 fixture 三条全绿的规则数
    R: int = 0             # 独立 verifier 复算一致的 claim 数
    D: int = 0             # 同输入双跑结果一致的检查数
    F: int = 0             # 全部证据都 fresh 的 claim 数

    missing: Set[str] = field(default_factory=set)  # 应有但未产出的维度
    r_run: bool = True                              # 运行态是否跑过

    def copy(self, **changes: Any) -> "Counts":
        """投影用：复制后改若干计数，不改原对象"""
        return replace(self, **changes)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "n_claims": self.n_claims,
            "n_rules": self.n_rules,
            "n_checks": self.n_checks,
            "L": self.L,
            "C": self.C,
            "R": self.R,
            "D": self.D,
            "F": self.F,
            "missing": sorted(self.missing),
            "r_run": self.r_run,
        }


@dataclass
class ScoreLine:
    """纯公式的输出 —— 不含任何解释文案，那些由 explain.py 生成"""

    counts: Counts
    dims: ARSDims
    design_points: Optional[float]
    runtime_points: Optional[float]
    total: Optional[int]
    band: Optional[str]
    doc_status: DocStatus
    warnings: List[str] = field(default_factory=list)

    @property
    def design_warning(self) -> bool:
        """设计预警：C < 0.5（不阻断，只提示）"""
        c = self.dims.C
        return c.available and c.value is not None and c.value < DESIGN_WARN_THRESHOLD

    @property
    def exact_total(self) -> Optional[float]:
        """
        未取整的总分 —— 报价与增益必须用它

        47 与 79 是"报告上给人看的整数"，32.46 是"账上算的增益"；
        用整数相减会得到 32，对不上 PRD 的 32.46，也会让 0.95 分/CU 变成 0.94。
        """
        if self.design_points is None or self.runtime_points is None:
            return None
        return float(sum((_segment_exact(self.dims, segment) for segment in SEGMENTS), Fraction()))

    @property
    def margin_per_cu(self) -> Optional[float]:
        return None


def score_from_counts(counts: Counts, *, min_sample: int = MIN_SAMPLE) -> ScoreLine:
    """
    口径本体：计数 → 分项 → 段分 → 总分

    这是整个产品唯一的打分公式，engine 与方案器都调它，
    统一预测与重测的算术口径；M1 仍由独立复算验证，不能由共享代码自证。
    """
    if type(min_sample) is not int or min_sample < 1:
        raise ValueError("min_sample must be a positive integer")
    if type(counts.r_run) is not bool:
        raise ValueError("r_run must be boolean")
    if not counts.missing <= set(_DENOM_FIELD):
        raise ValueError("unknown missing dimension")
    for name in ("n_claims", "n_rules", "n_checks", *WEIGHTS):
        value = getattr(counts, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    for key, denominator in _DENOM_FIELD.items():
        if getattr(counts, key) > getattr(counts, denominator):
            raise ValueError(f"{key} numerator exceeds {denominator}")

    warnings: List[str] = []
    dims = ARSDims()

    # ---------- 1. 逐维：状态与比例 ----------
    for key, dim in ((d.key, d) for d in dims.items()):
        if key in counts.missing:
            dim.status = DimStatus.MISSING
            dim.numerator = None
            dim.denominator = None
            dim.value = None
            warnings.append(f"{key} 维数据未产出（MISSING）：{_missing_hint(key)}")
            continue

        numerator = int(getattr(counts, key))
        denominator = int(getattr(counts, _DENOM_FIELD[key]))

        if denominator <= 0:
            dim.status = DimStatus.NA
            dim.numerator = 0
            dim.denominator = 0
            dim.value = None
            continue

        dim.status = DimStatus.VALUE
        dim.numerator = numerator
        dim.denominator = denominator
        dim.value = numerator / denominator

    # ---------- 2. 段内加权（NA 在同段内重分配，跨段不重分配） ----------
    design_points = _allocate(dims, "design", warnings)
    runtime_points = None if not counts.r_run else _allocate(dims, "runtime", warnings)

    # ---------- 3. 文档级状态（顺序：DESIGN_ONLY → PARTIAL → COMPLETE） ----------
    if not counts.r_run:
        if dims.C.status == DimStatus.VALUE:
            doc_status = DocStatus.DESIGN_ONLY
            warnings.append("运行态未跑：仅报设计态 C 分项，不出总分（DESIGN_ONLY）")
        else:
            doc_status = DocStatus.PARTIAL
            warnings.append("运行态未跑且 C 维不可用：只报分项（PARTIAL）")
    else:
        any_missing = any(d.status == DimStatus.MISSING for d in dims.items())
        too_few = counts.n_claims < min_sample
        if any_missing or too_few:
            doc_status = DocStatus.PARTIAL
            if any_missing:
                warnings.append("存在 MISSING 维：只报分项，不出总分与分档（PARTIAL）")
            if too_few:
                warnings.append(
                    f"样本不足：仅 {counts.n_claims} 条 claim（<{min_sample}），只报分项（PARTIAL）"
                )
        else:
            doc_status = DocStatus.COMPLETE

    # ---------- 4. 总分与分档 ----------
    total: Optional[int] = None
    band: Optional[str] = None
    if doc_status == DocStatus.COMPLETE and design_points is not None and runtime_points is not None:
        raw = sum((_segment_exact(dims, segment) for segment in SEGMENTS), Fraction())
        rounded = raw + Fraction(1, 2)
        total = rounded.numerator // rounded.denominator
        band = band_of(total)

    return ScoreLine(
        counts=counts,
        dims=dims,
        design_points=design_points,
        runtime_points=runtime_points,
        total=total,
        band=band,
        doc_status=doc_status,
        warnings=warnings,
    )


def _missing_hint(key: str) -> str:
    return {
        "C": "需要 S0/S1 的规则编译结果（RuleSet）",
        "L": "需要 S3 的 LocatorResolver 回源结果",
        "R": "需要 S4/独立 verifier 的复算结果",
        "D": "需要 S4 的同输入双跑一致性记录",
        "F": "需要 S3 的 claim 级新鲜度",
    }.get(key, "")


def _allocate(dims: ARSDims, segment: str, warnings: List[str]) -> float:
    """
    段内加权求和

    规则（ARS PRD §4.3）：
      - 跳过分母为 0 的维度（NA）
      - 有效权重按比例放大到该段满分（同段内），跨段不重分配
      - MISSING 不参与重分配（它会让整体变成 PARTIAL，总分不出）
    """
    keys = SEGMENTS[segment]
    seg_dims: List[Dim] = [dims.get(k) for k in keys]
    seg_max = SEGMENT_MAX[segment]

    for dim in seg_dims:
        dim.points = None if dim.value is None else dim.value * dim.weight
        dim.allocated_points = None

    valid = [d for d in seg_dims if d.available and d.points is not None]
    if not valid:
        warnings.append(f"{segment} 段无可计分维度，该段按 0 处理")
        return 0.0

    # Only NA releases weight. Missing evidence must not inflate known dimensions.
    total_weight = sum(d.weight for d in seg_dims if d.status != DimStatus.NA)
    raw = sum(d.points for d in valid)
    scale = seg_max / total_weight
    if abs(scale - 1.0) > 1e-12:
        warnings.append(
            f"{segment} 段存在 NA，权重在段内重分配（{total_weight} → {seg_max}）"
        )
    for dim in valid:
        dim.allocated_points = dim.points * scale
    return float(_segment_exact(dims, segment))


def _segment_exact(dims: ARSDims, segment: str) -> Fraction:
    """Keep ratios exact until the integer score is rounded at the boundary."""
    segment_dims = [dims.get(key) for key in SEGMENTS[segment]]
    weight = sum(d.weight for d in segment_dims if d.status != DimStatus.NA)
    if not weight:
        return Fraction()
    points = sum((Fraction(d.numerator, d.denominator) * d.weight
                  for d in segment_dims if d.available), Fraction())
    return points * Fraction(SEGMENT_MAX[segment], weight)
