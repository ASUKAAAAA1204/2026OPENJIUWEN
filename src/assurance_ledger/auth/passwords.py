"""Argon2id 密码散列；参数版本化，升级按哈希前缀重算"""
from __future__ import annotations

import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PARAMS = dict(time_cost=3, memory_cost=65536, parallelism=4)
MIN_PASSWORD = 8
MAX_PASSWORD = 1024
MAX_USERNAME = 64


class PasswordError(ValueError):
    pass


_hasher = PasswordHasher(**PARAMS)


def normalize_username(value: str) -> str:
    if not isinstance(value, str):
        raise PasswordError("用户名必须是字符串")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized or len(normalized) > MAX_USERNAME:
        raise PasswordError("用户名长度非法")
    if any(ch.isspace() for ch in normalized) or "/" in normalized or "\\" in normalized:
        raise PasswordError("用户名不能包含空白或路径分隔符")
    return normalized


def validate_password(value: str) -> str:
    if not isinstance(value, str) or not (MIN_PASSWORD <= len(value) <= MAX_PASSWORD):
        raise PasswordError(f"密码长度必须为 {MIN_PASSWORD}..{MAX_PASSWORD}")
    return value


def hash_password(value: str) -> str:
    validate_password(value)
    return _hasher.hash(value)


def verify_password(encoded: str, value: str) -> bool:
    if not isinstance(encoded, str) or not isinstance(value, str):
        return False
    try:
        return _hasher.verify(encoded, value)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(encoded: str) -> bool:
    try:
        return _hasher.check_needs_rehash(encoded)
    except (InvalidHashError, VerificationError):
        return True


__all__ = [
    "MAX_PASSWORD",
    "MAX_USERNAME",
    "MIN_PASSWORD",
    "PARAMS",
    "PasswordError",
    "hash_password",
    "needs_rehash",
    "normalize_username",
    "validate_password",
    "verify_password",
]
