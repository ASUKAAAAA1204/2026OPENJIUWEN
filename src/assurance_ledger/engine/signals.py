"""
输入端口（ports）：A / C 两个板块要交给「编译与评分」的东西 —— 板块②（B）归属

为什么要有这个文件
------------------
积分引擎不能自己去读 claim、证据包、检查记录 —— 那样它会和 ①③ 板块的
内部实现耦合，而且 L/R 的分子会重新变成"被测方自证"（契约 C7 要防的正是这件事）。

所以 B 只声明**它需要的形状**，由 ①（S3 resolver）和 ③（S4 判定 / 独立 verifier）
填进来。这一层是 JSON 友好的：`from_dict` 直接吃 `fixtures/demo_case.json` 的
片段，不要求对方 import 本模块的类。

C7 的两条不变量（启动自检 + 出分前断言，见 ars_engine.ARSEngine.assert_c7）：
    L 的分子只能来自 locator == RESOLVED **且 locator_source == resolver**（来源白名单）
    R 的分子只能来自 recompute == CONSISTENT **且 recompute_by == verifier**（独立 verifier 的结论）
    来源缺失 / 认不出的 RESOLVED / CONSISTENT：不计入分子，且计入 C7 违规计数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ============================================================
# locator 回源结果（S3 LocatorResolver 的三态 + 两种边界）
# ============================================================
LOC_RESOLVED = "RESOLVED"            # 回源成功且值一致 → 计入 L
LOC_NOT_FOUND = "NOT_FOUND"          # 定位不到 → 可补（add_locator 目标）
LOC_VALUE_MISMATCH = "VALUE_MISMATCH"  # 定位到了但值不符 → 可补
LOC_NO_LOCATOR = "NO_LOCATOR"        # 结构上没有可定位字段 → 补不了（structural）
LOC_NOT_RUN = "NOT_RUN"              # 未解析 → 视为不可定位

LOCATOR_VALUES = (
    LOC_RESOLVED,
    LOC_NOT_FOUND,
    LOC_VALUE_MISMATCH,
    LOC_NO_LOCATOR,
    LOC_NOT_RUN,
)
#: 能通过"补定位"修好的两类（决定 add_locator 的条数）
LOCATOR_FIXABLE = (LOC_NOT_FOUND, LOC_VALUE_MISMATCH)

# ============================================================
# 复算结果（S4 / 独立 verifier）
# ============================================================
RC_CONSISTENT = "CONSISTENT"          # 复算一致 → 计入 R
RC_INCONSISTENT = "INCONSISTENT"      # 复算不一致（refuted）→ 该条永久不可修
RC_INDETERMINATE = "INDETERMINATE"    # 无法判定 → 记债务，可能可修
RC_NOT_RUN = "NOT_RUN"                # 未复算 → 按未通过计

RECOMPUTE_VALUES = (RC_CONSISTENT, RC_INCONSISTENT, RC_INDETERMINATE, RC_NOT_RUN)

# ============================================================
# 原因码（决定"这条缺口该用哪个动作去补"）
# ============================================================
REASON_MISSING_FIELD = "MISSING_FIELD"          # 缺字段映射 → add_field
REASON_NO_EXPR = "NO_EXPR"                      # 规则编不出表达式 → rerun_check
REASON_TIMEOUT = "TIMEOUT"                      # 检查超时 → rerun_check
REASON_STALE_INPUT = "STALE_INPUT"              # 输入已过期 → rerun_stale
REASON_REFUTED = "REFUTED"                      # 复算不一致 → 不修（审计不改结论）
REASON_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # 证据不足 → structural

#: 走 rerun_check 的原因码
RERUN_CHECK_REASONS = (REASON_NO_EXPR, REASON_TIMEOUT)

# ============================================================
# 分子来源（C7 的可执行版本）
# ============================================================
SRC_RESOLVER = "resolver"        # L 的分子合法来源：S3 LocatorResolver
SRC_DECLARED = "declared"        # 被测方自报"我很精确" —— 不计入 L（C7 拦截）
SRC_VERIFIER = "verifier"        # R 的分子合法来源：独立 verifier
SRC_SELF = "self"                # 被测方自证 —— 不计入 R（C7 拦截）


# ============================================================
# 信号
# ============================================================
@dataclass(frozen=True)
class ClaimSignal:
    """
    单条 claim 的三个判定信号 —— L / R / F 的分子全部来自这里

    locator        : S3 回源结果（LOC_*）
    recompute      : S4/verifier 复算结果（RC_*）
    reason_code    : 不可判/不通过的原因（REASON_*），供方案器归因
    evidence_fresh : claim 级新鲜度（**全部**证据 fresh 才为 True；None = 证据缺失；
                     只接受真 bool 或 None，字符串/数字一律拒绝）
    locator_source : 这个 RESOLVED 是谁给的 —— 只有 resolver 算数；
                     缺省为空串 = 来源不明 → 不计入 L 且记 C7 违规
    recompute_by   : 这个 CONSISTENT 是谁给的 —— 只有 verifier 算数；
                     缺省为空串 = 来源不明 → 不计入 R 且记 C7 违规
    """

    claim_id: str
    locator: str = LOC_NOT_RUN
    recompute: str = RC_NOT_RUN
    reason_code: Optional[str] = None
    evidence_fresh: Optional[bool] = None
    locator_source: str = ""
    recompute_by: str = ""

    def __post_init__(self) -> None:
        # frozen dataclass 的严格校验：直接构造路径与 from_dict 共用
        if not isinstance(self.claim_id, str) or not self.claim_id.strip():
            raise ValueError(f"claim_id 必须是非空字符串，收到 {self.claim_id!r}")
        if self.evidence_fresh is not None and type(self.evidence_fresh) is not bool:
            raise ValueError(
                f"evidence_fresh 只接受 bool 或 None，收到 "
                f"{type(self.evidence_fresh).__name__}：{self.evidence_fresh!r}"
            )

    # ---------- 三个分子 ----------
    @property
    def counts_for_l(self) -> bool:
        """只有「来源白名单 resolver 的 RESOLVED」才算（C7 白名单，不信任缺省值）"""
        return self.locator == LOC_RESOLVED and self.locator_source == SRC_RESOLVER

    @property
    def counts_for_r(self) -> bool:
        """只有「独立 verifier 复算一致的 CONSISTENT」才算（C7 白名单）"""
        return self.recompute == RC_CONSISTENT and self.recompute_by == SRC_VERIFIER

    # ---------- C7 违规标记 ----------
    @property
    def violates_c7_l(self) -> bool:
        """RESOLVED 但来源不是 resolver（自报 / 未知 / 缺失）→ C7 违规，不计入 L"""
        return self.locator == LOC_RESOLVED and self.locator_source != SRC_RESOLVER

    @property
    def violates_c7_r(self) -> bool:
        return self.recompute == RC_CONSISTENT and self.recompute_by != SRC_VERIFIER

    @property
    def counts_for_f(self) -> bool:
        # 证据缺失（None）按"过期"计 —— 不得剔除，否则 F 分母可被做小
        return self.evidence_fresh is True

    # ---------- 归因 ----------
    @property
    def locator_fixable(self) -> bool:
        return self.locator in LOCATOR_FIXABLE

    @property
    def locator_structural(self) -> bool:
        return self.locator == LOC_NO_LOCATOR

    @property
    def needs_add_field(self) -> bool:
        return (
            self.recompute == RC_INDETERMINATE
            and self.reason_code == REASON_MISSING_FIELD
        )

    @property
    def needs_rerun_check(self) -> bool:
        return (
            self.recompute == RC_INDETERMINATE
            and self.reason_code in RERUN_CHECK_REASONS
        )

    @property
    def needs_rerun_stale(self) -> bool:
        return (
            self.evidence_fresh is False and self.reason_code == REASON_STALE_INPUT
        )

    @property
    def is_refuted(self) -> bool:
        return self.recompute == RC_INCONSISTENT

    # ---------- 构造 ----------
    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ClaimSignal":
        locator = _canon(raw.get("locator"), LOCATOR_VALUES, LOC_NOT_RUN)
        recompute = _canon(raw.get("recompute"), RECOMPUTE_VALUES, RC_NOT_RUN)
        locator_source = _source_canon(raw.get("locator_source"), (SRC_RESOLVER, SRC_DECLARED))
        recompute_by = _source_canon(raw.get("recompute_by"), (SRC_VERIFIER, SRC_SELF))
        claim_id = raw.get("claim_id")
        if claim_id is None:
            claim_id = raw.get("id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValueError(f"claim 必须提供非空 claim_id，收到 {claim_id!r}")
        return cls(
            claim_id=claim_id,
            locator=locator,
            recompute=recompute,
            reason_code=raw.get("reason_code"),
            # 严格布尔校验在 __post_init__ 里做：bool 或 None 之外的取值一律拒绝
            evidence_fresh=raw.get("evidence_fresh"),
            locator_source=locator_source,
            recompute_by=recompute_by,
        )


@dataclass(frozen=True)
class CheckSignal:
    """
    单个检查的确定性信号 —— D 的分子来源

    double_run_consistent：同输入跑两次结果是否一致。
      True  → 计入 D
      False → 不计入
      None  → 未双跑（MISSING 的味道），按"不确定"计，并推高债务
      只接受真 bool 或 None：字符串 / 数字一律拒绝（from_dict 与直接构造共用校验）。
    """

    check_id: str
    double_run_consistent: Optional[bool] = None

    def __post_init__(self) -> None:
        if not isinstance(self.check_id, str) or not self.check_id.strip():
            raise ValueError(f"check_id 必须是非空字符串，收到 {self.check_id!r}")
        if self.double_run_consistent is not None and type(self.double_run_consistent) is not bool:
            raise ValueError(
                f"double_run_consistent 只接受 bool 或 None，收到 "
                f"{type(self.double_run_consistent).__name__}：{self.double_run_consistent!r}"
            )

    @property
    def counts_for_d(self) -> bool:
        return self.double_run_consistent is True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CheckSignal":
        check_id = raw.get("check_id")
        if check_id is None:
            check_id = raw.get("id")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ValueError(f"check 必须提供非空 check_id，收到 {check_id!r}")
        # 严格布尔校验在 __post_init__ 里做
        return cls(check_id=check_id, double_run_consistent=raw.get("double_run_consistent"))


@dataclass
class CaseSignals:
    """
    一次评分的完整输入（JSON ↔ 对象）

    rules 为 None 表示"规则编译结果未产出" → C 记 MISSING；
    claims / checks 为 None 表示"该段未跑" → 对应维度 MISSING。
    """

    rules: Optional[List[Mapping[str, Any]]] = None
    claims: Optional[List[ClaimSignal]] = None
    checks: Optional[List[CheckSignal]] = None
    skill_version: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_case(cls, case: Mapping[str, Any]) -> "CaseSignals":
        rules = case.get("rules")
        claims_raw = case.get("claims")
        checks_raw = case.get("checks")
        return cls(
            rules=None if rules is None else list(rules),
            claims=None if claims_raw is None else [ClaimSignal.from_dict(c) for c in claims_raw],
            checks=None if checks_raw is None else [CheckSignal.from_dict(c) for c in checks_raw],
            skill_version=str(case.get("skill_version") or ""),
            meta=dict(case.get("meta") or {}),
        )

    def claim_signals(self) -> Sequence[ClaimSignal]:
        return tuple(self.claims or ())

    def check_signals(self) -> Sequence[CheckSignal]:
        return tuple(self.checks or ())


# ============================================================
# 内部
# ============================================================
def _canon(value: Any, allowed: Sequence[str], default: str) -> str:
    """认不出的取值一律降级为 default —— 不让脏数据把分算高"""
    if isinstance(value, str):
        upper = value.strip().upper()
        for item in allowed:
            if upper == item:
                return item
    return default


def _source_canon(value: Any, allowed: Sequence[str]) -> str:
    """
    来源白名单：只认 allowed 里的精确取值（大小写/空白归一后）。

    缺失或认不出的来源一律返回空串 —— **绝不默认信任**。
    空串不被 counts_for_l / counts_for_r 承认，且会被 violates_c7_* 记为违规。
    """
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in allowed:
            return lower
    return ""
