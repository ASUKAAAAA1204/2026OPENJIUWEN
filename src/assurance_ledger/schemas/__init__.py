"""
C1–C6 六个接口契约校验器

三个人并行、七段来自不同来源，唯一能保证拼起来不炸的就是这六条不变量。
启动自检：把 C1–C6 写成启动时的自动校验，失败即拒跑。
"""

from .contracts import (
    check_C1, check_C2, check_C3, check_C4, check_C5, check_C6_readonly_ui,
    ContractViolation, startup_self_test,
)

__all__ = [
    "check_C1", "check_C2", "check_C3", "check_C4", "check_C5", "check_C6_readonly_ui",
    "ContractViolation", "startup_self_test",
]
