"""
核心枚举类型 —— PRD 冻结的字面常量
改这些值 = 改 PRD 口径，必须版本化
"""

from enum import Enum, auto


class LocatorLevel(str, Enum):
    """locator 三级规范（链路 PRD §4.1）"""
    PRECISE = "precise"        # file:row:field / line:42 → 计入 L
    SEMI = "semi"             # file / section → 不计入 L
    NONE = "none"              # 仅自然语言引用 → 不计入 L


class CompileFailCode(str, Enum):
    """规则编译四类失败码（ARS PRD §6.6）"""
    NO_LOCATOR = "NO_LOCATOR"
    SUBJECTIVE_RULE = "SUBJECTIVE_RULE"
    MISSING_FIELD = "MISSING_FIELD"
    NON_DETERMINISTIC = "NON_DETERMINISTIC"


class ClaimStatus(str, Enum):
    """S4 判定三种状态（链路 PRD §5.1）—— 不引入四态"""
    VERIFIED = "verified"
    REFUTED = "refuted"
    INDETERMINATE = "indeterminate"


class DebtType(str, Enum):
    """未兑现的验证义务三类（主 PRD §5）"""
    UNVERIFIED = "unverified"
    STALE = "stale"
    STRUCTURAL = "structural"


class GradeLevel(str, Enum):
    """单条结论保证等级（主 PRD §5）—— 非概率"""
    A = "A"   # verified + fresh
    B = "B"   # verified but stale / partially verified
    C = "C"   # indeterminate with reason_code
    D = "D"   # structural / unverified


class CheckKind(str, Enum):
    """五类可执行检查（主 PRD §5）"""
    RECOMPUTE = "recompute"          # 回源重算
    CROSS_SOURCE = "cross_source"    # 跨源交叉验证
    LINE_LOOKUP = "line_lookup"      # 行/字段定位
    RULE_REPLAY = "rule_replay"      # 规则重放
    HUMAN = "human"                  # 人工审核
