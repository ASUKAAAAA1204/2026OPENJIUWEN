"""中央拉起并守护框架 worker：独立 venv、退避重启、优雅退出"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import PlatformConfig, WorkerConfig

MAX_RESTARTS = 5
RESTART_BACKOFF = (1, 2, 4, 8, 16)


@dataclass
class WorkerStatus:
    name: str
    framework: str
    python: str
    state: str = "pending"
    reason: str = ""
    pid: int | None = None
    restarts: int = 0
    process: subprocess.Popen | None = field(default=None, repr=False)
    next_restart: float = 0.0
    permanent: bool = False
    log: Any = field(default=None, repr=False)

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "framework": self.framework,
                "state": self.state, "reason": self.reason,
                "pid": self.pid, "restarts": self.restarts}


class WorkerSupervisor:
    def __init__(self, config: PlatformConfig, *, bootstrap_token: str,
                 central_url: str):
        self.config = config
        self.bootstrap_token = bootstrap_token
        self.central_url = central_url.rstrip("/")
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.workers = [self._prepare(item) for item in config.workers]

    def _prepare(self, worker: WorkerConfig) -> WorkerStatus:
        python = Path(worker.python)
        if not python.is_absolute():
            python = (self.config.root / python).resolve()
        status = WorkerStatus(name=worker.name, framework=worker.framework,
                              python=str(python))
        if not worker.enabled:
            status.state = "disabled"
            status.reason = "配置禁用"
        elif not python.exists():
            status.state = "unavailable"
            status.reason = f"解释器不存在：{python}"
        return status

    def start(self) -> None:
        if self._thread is not None:
            return
        log_dir = self.config.storage.database.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        for status in self.workers:
            if status.state in ("disabled", "unavailable"):
                continue
            status.log = (log_dir / f"worker-{status.name}.log").open(
                "a", encoding="utf-8")
            self._spawn(status)
        self._thread = threading.Thread(target=self._loop, name="worker-supervisor",
                                        daemon=True)
        self._thread.start()

    def _spawn(self, status: WorkerStatus) -> None:
        if status.log is None:
            return
        env = dict(os.environ)
        env.update({
            "CENTRAL_URL": self.central_url,
            "WORKER_BOOTSTRAP_TOKEN": self.bootstrap_token,
            "RUN_ROOT": str(self.config.allowed_root),
            "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src") +
                          os.pathsep + env.get("PYTHONPATH", ""),
        })
        try:
            status.process = subprocess.Popen(
                [status.python, "-m", "assurance_ledger.workers.main",
                 "--framework", status.framework],
                cwd=str(self.config.root), env=env,
                stdout=status.log, stderr=subprocess.STDOUT)
        except OSError as error:
            status.state = "unavailable"
            status.reason = f"启动失败：{error}"
            status.permanent = True
            return
        status.pid = status.process.pid
        status.state = "starting"
        status.reason = "进程已拉起，等待注册"

    def _loop(self) -> None:
        while not self._stopping.is_set():
            now = time.monotonic()
            with self._lock:
                for status in self.workers:
                    if status.permanent or status.process is None:
                        continue
                    code = status.process.poll()
                    if code is None:
                        if status.state == "starting":
                            status.state = "running"
                            status.reason = "进程运行中"
                        continue
                    status.pid = None
                    status.process = None
                    if code == 3:
                        status.state = "unavailable"
                        status.reason = "适配器不可用（退出码 3），不再重启"
                        status.permanent = True
                        continue
                    status.restarts += 1
                    if status.restarts > MAX_RESTARTS:
                        status.state = "unavailable"
                        status.reason = f"连续退出 {status.restarts} 次，停止重启"
                        status.permanent = True
                        continue
                    delay = RESTART_BACKOFF[min(status.restarts - 1,
                                                len(RESTART_BACKOFF) - 1)]
                    status.state = "backoff"
                    status.reason = f"退出码 {code}，{delay}s 后重启"
                    status.next_restart = now + delay
                for status in self.workers:
                    if (status.state == "backoff" and status.process is None
                            and not status.permanent and now >= status.next_restart):
                        self._spawn(status)
            self._stopping.wait(1.0)

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [status.payload() for status in self.workers]

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            for status in self.workers:
                process = status.process
                if process is None:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                status.process = None
                status.pid = None
                status.state = "stopped"
                status.reason = "中央停止"
                if status.log is not None:
                    status.log.close()
                    status.log = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def central_url(config: PlatformConfig) -> str:
    host = config.server.bind.rsplit(":", 1)[0]
    port = config.server.bind.rsplit(":", 1)[1]
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


__all__ = ["WorkerSupervisor", "WorkerStatus", "central_url"]
