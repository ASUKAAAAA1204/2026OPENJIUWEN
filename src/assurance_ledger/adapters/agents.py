"""OpenAI Agents SDK v2.7 执行前阻断适配入口；锁定版本需 M0 spike 后确定"""
from __future__ import annotations

UNAVAILABLE_REASON = "OpenAI Agents SDK v2.7 执行前阻断适配尚未实现"


def sdk_installed() -> bool:
    try:
        import agents  # noqa: F401
    except ImportError:
        return False
    return True


def create_adapter():
    return None


__all__ = ["UNAVAILABLE_REASON", "create_adapter", "sdk_installed"]
