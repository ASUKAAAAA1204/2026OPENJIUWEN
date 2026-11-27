"""单次运行导出清单的 Ed25519 签名与独立验证

清单绑定 run_id、导出字节摘要、字节数、事件数与创建时间；侧车文件与 JSONL
一起离线保存，才能发现尾部截断。私钥保管与留存是运维职责。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import digest_json

DOMAIN = "temisguard-export-checkpoint-v1"
SIGNED_FIELDS = ("domain", "run_id", "sha256", "byte_size", "event_count",
                 "created_at")
MANIFEST_FIELDS = frozenset(SIGNED_FIELDS) | {"signature"}


class ExportCheckpointError(ValueError):
    code = "CHECKPOINT_INVALID"


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("domain") != DOMAIN:
        raise ExportCheckpointError("清单 domain 不正确")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ExportCheckpointError("清单 run_id 必填")
    sha256 = payload.get("sha256")
    if (not isinstance(sha256, str) or len(sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in sha256)):
        raise ExportCheckpointError("清单 sha256 必须是 64 位小写十六进制")
    for key in ("byte_size", "event_count"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExportCheckpointError(f"清单 {key} 必须是非负整数")
    created_at = payload.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ExportCheckpointError("清单 created_at 必填")


def build_manifest(*, run_id: str, sha256: str, byte_size: int,
                   event_count: int, created_at: str) -> dict[str, Any]:
    payload = {
        "domain": DOMAIN, "run_id": run_id, "sha256": sha256,
        "byte_size": byte_size, "event_count": event_count,
        "created_at": created_at,
    }
    _validate_payload(payload)
    return payload


def sign_manifest(manifest: dict[str, Any],
                  private_key: Ed25519PrivateKey) -> dict[str, Any]:
    payload = {key: manifest[key] for key in SIGNED_FIELDS}
    _validate_payload(payload)
    signature = private_key.sign(_canonical(payload))
    signed = dict(payload)
    signed["signature"] = base64.b64encode(signature).decode("ascii")
    return signed


def verify_manifest(manifest: dict[str, Any],
                    public_key: Ed25519PublicKey) -> dict[str, Any]:
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ExportCheckpointError("清单字段不完整或含额外字段")
    payload = {key: manifest[key] for key in SIGNED_FIELDS}
    _validate_payload(payload)
    try:
        signature = base64.b64decode(manifest["signature"], validate=True)
        public_key.verify(signature, _canonical(payload))
    except (InvalidSignature, ValueError, TypeError) as error:
        raise ExportCheckpointError("清单签名无效") from error
    return payload


def manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(manifest)).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (TypeError, ValueError) as error:
        raise ExportCheckpointError(f"清单无法规范化：{error}") from error


def sidecar_path(jsonl_path: str | Path) -> Path:
    return Path(str(jsonl_path) + ".checkpoint.json")


def write_sidecar(jsonl_path: str | Path, manifest: dict[str, Any]) -> Path:
    target = sidecar_path(jsonl_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n",
                      encoding="utf-8", newline="\n")
    return target


def read_sidecar(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExportCheckpointError(f"检查点无法读取：{error}") from error
    if not isinstance(value, dict):
        raise ExportCheckpointError("检查点必须是对象")
    return value


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(),
                                                 password=None)
    except (OSError, ValueError, TypeError) as error:
        raise ExportCheckpointError(f"私钥无法读取：{error}") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise ExportCheckpointError("私钥必须是 Ed25519")
    return key


def load_public_key(path: str | Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(Path(path).read_bytes())
    except (OSError, ValueError, TypeError) as error:
        raise ExportCheckpointError(f"公钥无法读取：{error}") from error
    if not isinstance(key, Ed25519PublicKey):
        raise ExportCheckpointError("公钥必须是 Ed25519")
    return key


def public_key_path_for(private_path: str | Path) -> Path:
    path = Path(private_path)
    return path.with_name(path.name.replace("private", "public"))


def verify_export_file(jsonl_path: str | Path, manifest: dict[str, Any],
                       public_key: Ed25519PublicKey) -> dict[str, Any]:
    """校验签名、字节摘要/大小、事件数与逐行内容哈希、快照摘要"""
    payload = verify_manifest(manifest, public_key)
    data = Path(jsonl_path).read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != payload["sha256"]:
        raise ExportCheckpointError("导出字节摘要与清单不符")
    if len(data) != payload["byte_size"]:
        raise ExportCheckpointError("导出字节数与清单不符")
    try:
        lines = [json.loads(line) for line in data.decode("utf-8").splitlines()
                 if line]
    except (UnicodeDecodeError, ValueError) as error:
        raise ExportCheckpointError(f"导出不是合法 JSONL：{error}") from error
    if not lines or lines[0].get("record_type") != "run":
        raise ExportCheckpointError("导出缺少运行头")
    events = 0
    previous_seq = 0
    for line in lines:
        kind = line.get("record_type")
        if kind == "snapshot":
            if digest_json(line.get("body")) != line.get("ref"):
                raise ExportCheckpointError("快照内容指纹不一致")
        elif kind == "event":
            seq = line.get("seq")
            if not isinstance(seq, int) or seq <= previous_seq:
                raise ExportCheckpointError("导出事件序号不单调")
            previous_seq = seq
            recomputed = digest_json({
                "seq": seq, "event_id": line.get("event_id"),
                "body": line.get("body"), "prev_hash": line.get("prev_hash"),
            })
            if recomputed != line.get("content_hash"):
                raise ExportCheckpointError("事件内容哈希不一致")
            events += 1
        elif kind != "run":
            raise ExportCheckpointError("导出含未知记录类型")
    if events != payload["event_count"]:
        raise ExportCheckpointError("导出事件数与清单不符")
    return dict(payload, verified=True)


__all__ = [
    "DOMAIN",
    "ExportCheckpointError",
    "MANIFEST_FIELDS",
    "SIGNED_FIELDS",
    "build_manifest",
    "load_private_key",
    "load_public_key",
    "manifest_digest",
    "public_key_path_for",
    "read_sidecar",
    "sidecar_path",
    "sign_manifest",
    "verify_export_file",
    "verify_manifest",
    "write_sidecar",
]
