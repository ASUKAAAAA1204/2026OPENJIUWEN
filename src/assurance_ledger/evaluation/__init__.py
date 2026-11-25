"""C 板块 v2.7 离线评测适配层（本地输入契约 -> B 引擎公开 API）。"""

from .v27 import (
    INPUT_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    EvaluationError,
    evaluate_files,
)

__all__ = [
    "INPUT_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "EvaluationError",
    "evaluate_files",
]
