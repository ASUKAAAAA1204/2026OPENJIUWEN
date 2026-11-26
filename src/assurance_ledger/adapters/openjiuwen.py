"""openJiuwen v2.7 执行前阻断适配入口

现有 rail 适配器只覆盖采集，不能作为执行前阻断证据。v2.7 的 guarded_call
适配需要锁定 0.1.20.post1 的真实 SDK 后单独实现与验证。
"""
from __future__ import annotations

UNAVAILABLE_REASON = "openJiuwen v2.7 执行前阻断适配尚未实现"


def sdk_installed() -> bool:
    try:
        import openjiuwen  # noqa: F401
    except ImportError:
        return False
    return True


def create_adapter():
    return None


__all__ = ["UNAVAILABLE_REASON", "create_adapter", "sdk_installed"]
