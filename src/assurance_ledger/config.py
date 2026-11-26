"""v2.7 平台配置：TOML 文件 + 环境变量覆盖

路径默认相对进程工作目录解析；显式配置优先。缺失字段使用安全默认值，
但不提供默认账号或默认密钥。
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORKS = ("openJiuwen", "LangGraph", "OpenAI Agents SDK")


class ConfigError(ValueError):
    """配置缺失或非法"""


@dataclass(frozen=True)
class ServerConfig:
    bind: str = "127.0.0.1:8000"
    internal_bind: str = "127.0.0.1:8000"
    public_origin: str = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class StorageConfig:
    database: Path = Path("data/platform.db")
    outputs: Path = Path("outputs")
    keys: Path = Path("data/keys")
    backups: Path = Path("data/backups")


@dataclass(frozen=True)
class SecurityConfig:
    allowed_root: Path = Path("workspaces")
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8
    approval_timeout_seconds: int = 30
    secure_cookies: bool = False
    session_secret: str = ""
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    worker_bootstrap_token: str = ""
    operator_skills: tuple[str, ...] = ("code-assistant",)


@dataclass(frozen=True)
class WorkerConfig:
    name: str
    framework: str
    python: str
    enabled: bool = True


@dataclass(frozen=True)
class WorkspaceConfig:
    id: str
    path: Path


@dataclass(frozen=True)
class SkillConfig:
    id: str
    version: str


@dataclass(frozen=True)
class PlatformConfig:
    root: Path
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    workers: tuple[WorkerConfig, ...] = ()
    workspaces: tuple[WorkspaceConfig, ...] = ()
    skills: tuple[SkillConfig, ...] = ()
    policy_file: Path | None = None
    outputs_max_export_bytes: int = 128 * 1024 * 1024

    @property
    def database(self) -> Path:
        return self.storage.database

    @property
    def allowed_root(self) -> Path:
        return self.security.allowed_root

    def worker(self, framework: str) -> WorkerConfig | None:
        return next((w for w in self.workers if w.framework == framework), None)

    def workspace(self, workspace_id: str) -> WorkspaceConfig | None:
        return next((w for w in self.workspaces if w.id == workspace_id), None)

    @classmethod
    def load(cls, path: str | Path | None = None, *, root: Path | None = None,
             environ: Mapping[str, str] | None = None) -> "PlatformConfig":
        env = dict(os.environ if environ is None else environ)
        chosen = path or env.get("TEMIS_CONFIG")
        base = Path(root) if root is not None else Path.cwd()
        document: dict[str, Any] = {}
        config_path: Path | None = None
        if chosen:
            config_path = Path(chosen).expanduser()
            if not config_path.is_absolute():
                config_path = base / config_path
            try:
                document = tomllib.loads(config_path.read_text(encoding="utf-8"))
            except FileNotFoundError as error:
                raise ConfigError(f"配置文件不存在：{config_path}") from error
            except tomllib.TOMLDecodeError as error:
                raise ConfigError(f"配置文件不是合法 TOML：{error}") from error
        config = cls.from_mapping(document, base=base, config_path=config_path)
        return config.with_env(env)

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any], *, base: Path,
                     config_path: Path | None = None) -> "PlatformConfig":
        server = document.get("server", {})
        storage = document.get("storage", {})
        security = document.get("security", {})
        if not isinstance(server, Mapping) or not isinstance(storage, Mapping) or not isinstance(security, Mapping):
            raise ConfigError("server/storage/security 必须是表")
        storage_cfg = StorageConfig(
            database=_path(storage.get("database", "data/platform.db"), base),
            outputs=_path(storage.get("outputs", "outputs"), base),
            keys=_path(storage.get("keys", "data/keys"), base),
            backups=_path(storage.get("backups", "data/backups"), base),
        )
        security_cfg = SecurityConfig(
            allowed_root=_path(security.get("allowed_root", "workspaces"), base),
            session_idle_minutes=_positive_int(security.get("session_idle_minutes", 30), "session_idle_minutes"),
            session_absolute_hours=_positive_int(security.get("session_absolute_hours", 8), "session_absolute_hours"),
            approval_timeout_seconds=_positive_int(security.get("approval_timeout_seconds", 30), "approval_timeout_seconds"),
            secure_cookies=bool(security.get("secure_cookies", False)),
            session_secret=str(security.get("session_secret", "")),
            login_max_attempts=_positive_int(security.get("login_max_attempts", 5), "login_max_attempts"),
            login_window_seconds=_positive_int(security.get("login_window_seconds", 300), "login_window_seconds"),
            worker_bootstrap_token=str(security.get("worker_bootstrap_token", "")),
            operator_skills=_string_tuple(security.get("operator_skills", ("code-assistant",)), "operator_skills"),
        )
        workers = []
        raw_workers = document.get("workers", {})
        if not isinstance(raw_workers, Mapping):
            raise ConfigError("workers 必须是表")
        names = {"openjiuwen": "openJiuwen", "langgraph": "LangGraph", "agents": "OpenAI Agents SDK"}
        for key, framework in names.items():
            item = raw_workers.get(key, {})
            if not isinstance(item, Mapping):
                raise ConfigError(f"workers.{key} 必须是表")
            python = str(item.get("python", f".venvs/{key}/Scripts/python.exe"))
            workers.append(WorkerConfig(
                name=key, framework=framework, python=python,
                enabled=bool(item.get("enabled", True)),
            ))
        workspaces = []
        raw_workspaces = document.get("workspaces")
        if raw_workspaces is None:
            workspaces.append(WorkspaceConfig(id="workspace", path=security_cfg.allowed_root / "workspace"))
        else:
            if not isinstance(raw_workspaces, list):
                raise ConfigError("workspaces 必须是数组")
            for item in raw_workspaces:
                if not isinstance(item, Mapping):
                    raise ConfigError("workspaces 项必须是表")
                wid = str(item.get("id", "")).strip()
                if not wid:
                    raise ConfigError("workspace.id 必填")
                workspaces.append(WorkspaceConfig(id=wid, path=_path(item.get("path", ""), base)))
        if not workspaces:
            raise ConfigError("至少需要一个授权工作目录")
        if len({w.id for w in workspaces}) != len(workspaces):
            raise ConfigError("workspace.id 必须唯一")
        skills = []
        raw_skills = document.get("skills")
        if raw_skills is None:
            skills.append(SkillConfig(id="code-assistant", version="1.0.0"))
        else:
            if not isinstance(raw_skills, list):
                raise ConfigError("skills 必须是数组")
            for item in raw_skills:
                if not isinstance(item, Mapping):
                    raise ConfigError("skills 项必须是表")
                sid, version = str(item.get("id", "")).strip(), str(item.get("version", "")).strip()
                if not sid or not version:
                    raise ConfigError("skill.id 与 skill.version 必填")
                skills.append(SkillConfig(id=sid, version=version))
        if len({(s.id, s.version) for s in skills}) != len(skills):
            raise ConfigError("skill id+version 必须唯一")
        policy_file = document.get("policy_file")
        return cls(
            root=base,
            server=ServerConfig(
                bind=str(server.get("bind", "127.0.0.1:8000")),
                internal_bind=str(server.get("internal_bind", server.get("bind", "127.0.0.1:8000"))),
                public_origin=str(server.get("public_origin", "http://127.0.0.1:8000")).rstrip("/"),
            ),
            storage=storage_cfg,
            security=security_cfg,
            workers=tuple(workers),
            workspaces=tuple(workspaces),
            skills=tuple(skills),
            policy_file=_path(policy_file, base) if policy_file else None,
            outputs_max_export_bytes=_positive_int(
                storage.get("max_export_bytes", 128 * 1024 * 1024), "max_export_bytes"),
        )

    def with_env(self, env: Mapping[str, str]) -> "PlatformConfig":
        from dataclasses import replace
        storage = self.storage
        security = self.security
        server = self.server
        if env.get("TEMIS_DB_PATH"):
            storage = replace(storage, database=_path(env["TEMIS_DB_PATH"], self.root))
        if env.get("TEMIS_OUTPUTS"):
            storage = replace(storage, outputs=_path(env["TEMIS_OUTPUTS"], self.root))
        if env.get("TEMIS_ALLOWED_ROOT"):
            security = replace(security, allowed_root=_path(env["TEMIS_ALLOWED_ROOT"], self.root))
        if env.get("TEMIS_PUBLIC_ORIGIN"):
            server = replace(server, public_origin=env["TEMIS_PUBLIC_ORIGIN"].rstrip("/"))
        if env.get("TEMIS_SESSION_SECRET"):
            security = replace(security, session_secret=env["TEMIS_SESSION_SECRET"])
        if env.get("TEMIS_WORKER_BOOTSTRAP_TOKEN"):
            security = replace(security, worker_bootstrap_token=env["TEMIS_WORKER_BOOTSTRAP_TOKEN"])
        if env.get("TEMIS_SECURE_COOKIES"):
            security = replace(security, secure_cookies=env["TEMIS_SECURE_COOKIES"].lower() in ("1", "true", "yes"))
        return replace(self, storage=storage, security=security, server=server)

    def prepare_directories(self) -> None:
        for path in (self.storage.database.parent, self.storage.outputs,
                     self.storage.keys, self.storage.backups, self.allowed_root):
            path.mkdir(parents=True, exist_ok=True)
        for workspace in self.workspaces:
            workspace.path.mkdir(parents=True, exist_ok=True)


def _path(value: Any, base: Path) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ConfigError(f"路径必须是字符串：{value!r}")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} 必须是正整数：{value!r}")
    return value


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"{name} 必须是非空字符串数组")
    return tuple(value)


__all__ = [
    "ConfigError",
    "FRAMEWORKS",
    "PlatformConfig",
    "ServerConfig",
    "SecurityConfig",
    "SkillConfig",
    "StorageConfig",
    "WorkerConfig",
    "WorkspaceConfig",
]
