"""A 的模型配置：只读取指定文件/环境变量，不输出凭证。"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


class ModelConfigError(ValueError):
    """可以直接展示给用户的配置错误；消息不含配置值。"""


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout_seconds: float = 60
    max_tokens: int = 1024

    def public_summary(self) -> dict:
        return {
            "provider": "DeepSeek", "base_url": self.base_url, "model": self.model,
            "api_key_configured": bool(self.api_key), "thinking": "disabled",
            "timeout_seconds": self.timeout_seconds, "max_tokens": self.max_tokens,
        }


def load_deepseek_settings(env_file: Path, *, environ: Mapping[str, str] | None = None) -> DeepSeekSettings:
    """系统环境变量优先；不展开 ${变量}，也不将文件内容写入进程环境。"""
    environ = os.environ if environ is None else environ
    try:
        values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False) if env_file.is_file() else {}
    except (OSError, UnicodeError):
        raise ModelConfigError("无法读取模型配置文件，请检查文件路径和 UTF-8 编码。") from None

    def get(name: str, default: str = "") -> str:
        return (environ.get(name, values.get(name, default)) or "").strip()

    key = get("DEEPSEEK_API_KEY")
    if not key or key.lower() in {"your_api_key_here", "sk-your-api-key", "填入你的密钥"}:
        raise ModelConfigError("尚未配置密钥：请在项目 .env 文件的 DEEPSEEK_API_KEY= 后填入 API Key。")
    if any(char.isspace() for char in key) or not key.isascii():
        raise ModelConfigError("DEEPSEEK_API_KEY 格式不正确，请重新粘贴完整密钥，不要带空格或换行。")
    base = get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if base not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        raise ModelConfigError("当前入口仅接入 DeepSeek 官方服务：DEEPSEEK_BASE_URL 应为 https://api.deepseek.com。")
    model = get("DEEPSEEK_MODEL", "deepseek-flash")
    if model not in {"deepseek-flash", "deepseek-v4-pro"}:
        raise ModelConfigError("DEEPSEEK_MODEL 请填写 deepseek-flash 或 deepseek-v4-pro。")
    try:
        timeout = float(get("DEEPSEEK_TIMEOUT_SECONDS", "60"))
        max_tokens = int(get("DEEPSEEK_MAX_TOKENS", "1024"))
    except ValueError:
        raise ModelConfigError("超时和输出上限必须填写数字。") from None
    if not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ModelConfigError("DEEPSEEK_TIMEOUT_SECONDS 必须在 1–120 秒之间。")
    if not 64 <= max_tokens <= 4096:
        raise ModelConfigError("DEEPSEEK_MAX_TOKENS 必须在 64–4096 之间。")
    return DeepSeekSettings(key, base, model, timeout, max_tokens)
