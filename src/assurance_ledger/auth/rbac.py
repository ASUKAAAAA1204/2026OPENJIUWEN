"""五角色权限：路由级 + 对象级双重校验，默认拒绝

PRD §5.1 是唯一语义；数据库使用英文枚举，接口输出中文角色名。
"""
from __future__ import annotations

from typing import Any, Mapping

from ..config import SecurityConfig

ROLES = ("platform_admin", "on_call", "developer", "auditor", "operator")
ROLE_LABELS = {
    "platform_admin": "平台管理员",
    "on_call": "值班人员",
    "developer": "开发者",
    "auditor": "合规审计",
    "operator": "平台运营",
}
LABEL_ROLES = {label: role for role, label in ROLE_LABELS.items()}
MANAGEMENT_CAPABILITIES = ("release_gate", "remediation_quote", "version_comparison")


def _value(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    try:
        return item[key]
    except (KeyError, IndexError):
        return None


def role_of(user: Any) -> str:
    role = _value(user, "role")
    return role if role in ROLES else ""


def role_label(user: Any) -> str:
    return ROLE_LABELS.get(role_of(user), "")


def scope_text(user: Any, security: SecurityConfig) -> str:
    role = role_of(user)
    if role in ("platform_admin", "on_call"):
        return "全部运行"
    if role == "auditor":
        return "全部只读"
    if role == "developer":
        return "本人运行"
    if role == "operator":
        return "所属 Skill：" + ", ".join(security.operator_skills)
    return "无"


def identity_payload(user: Any, security: SecurityConfig) -> dict[str, Any]:
    return {
        "user_id": _value(user, "user_id"),
        "display_name": _value(user, "display_name"),
        "role": role_label(user),
        "scope": scope_text(user, security),
    }


def can_view_run(user: Any, run: Any, security: SecurityConfig) -> bool:
    role = role_of(user)
    if role in ("platform_admin", "on_call", "auditor"):
        return True
    if role == "developer":
        return _value(run, "owner_user_id") == _value(user, "user_id")
    if role == "operator":
        return _value(run, "skill_id") in security.operator_skills
    return False


def can_create_run(user: Any) -> bool:
    return role_of(user) in ("platform_admin", "developer")


def can_execute_run(user: Any, run: Any, security: SecurityConfig) -> bool:
    if not can_view_run(user, run, security):
        return False
    return role_of(user) in ("platform_admin", "developer")


def can_approve(user: Any) -> bool:
    return role_of(user) in ("platform_admin", "on_call")


def can_export_run(user: Any, run: Any, security: SecurityConfig) -> bool:
    if not can_view_run(user, run, security):
        return False
    if role_of(user) == "auditor":
        return True
    return role_of(user) in ("platform_admin", "on_call", "developer", "operator")


def can_resolve_debt(user: Any, run: Any, security: SecurityConfig) -> bool:
    if not can_view_run(user, run, security):
        return False
    return role_of(user) in ("platform_admin", "on_call", "developer")


def can_manage_policy(user: Any) -> bool:
    return role_of(user) == "platform_admin"


def can_configure_management(user: Any, capability: str) -> bool:
    role = role_of(user)
    if capability not in MANAGEMENT_CAPABILITIES:
        return False
    if role == "platform_admin":
        return True
    return capability == "release_gate" and role == "operator"


def can_execute_management(user: Any, capability: str) -> bool:
    role = role_of(user)
    if capability == "release_gate":
        return role in ("platform_admin", "operator")
    if capability in ("remediation_quote", "version_comparison"):
        return role == "platform_admin"
    return False


__all__ = [
    "LABEL_ROLES",
    "MANAGEMENT_CAPABILITIES",
    "ROLES",
    "ROLE_LABELS",
    "can_approve",
    "can_configure_management",
    "can_create_run",
    "can_execute_management",
    "can_execute_run",
    "can_export_run",
    "can_manage_policy",
    "can_resolve_debt",
    "can_view_run",
    "identity_payload",
    "role_label",
    "role_of",
    "scope_text",
]
