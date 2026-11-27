"""启动门控：等待首个 worker 注册，超时降级为只读就绪"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from assurance_ledger.api.app import _await_first_worker
from assurance_ledger.config import ConfigError, PlatformConfig


def _config(tmp_path, **server):
    document = {"server": {"startup_wait_seconds": 0, **server}}
    return PlatformConfig.from_mapping(document, base=tmp_path)


def test_startup_wait_allows_zero_and_rejects_negative(tmp_path):
    assert _config(tmp_path).server.startup_wait_seconds == 0
    assert _config(tmp_path, startup_wait_seconds=5).server.startup_wait_seconds == 5
    with pytest.raises(ConfigError):
        _config(tmp_path, startup_wait_seconds=-1)


def test_startup_wait_can_be_overridden_from_environment(tmp_path):
    config = _config(tmp_path).with_env({"TEMIS_STARTUP_WAIT_SECONDS": "12"})
    assert config.server.startup_wait_seconds == 12
    with pytest.raises(ConfigError):
        config.with_env({"TEMIS_STARTUP_WAIT_SECONDS": "-2"})
    with pytest.raises(ConfigError):
        config.with_env({"TEMIS_STARTUP_WAIT_SECONDS": "soon"})


def _stop_workers(platform) -> None:
    with sqlite3.connect(platform.config.database) as db:
        db.execute("UPDATE workers SET status='stopped'")


def test_timeout_degrades_to_read_only_readiness(platform):
    _stop_workers(platform)
    platform.state.startup = {
        "gate": "degraded", "waited_seconds": 0.0,
        "workers_observed": 0, "reason": "等待 worker 注册超时",
    }
    ready = platform.client.get("/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["ready"] is True
    assert body["mode"] == "readonly"
    assert body["checks"]["worker_registered"] is False
    assert body["startup"]["gate"] == "degraded"

    platform.state.startup = {"gate": "disabled", "waited_seconds": 0.0,
                              "workers_observed": 0, "reason": "未启用"}
    assert platform.client.get("/readyz").status_code == 503


def test_await_first_worker_reports_degraded_without_registration(platform):
    _stop_workers(platform)
    platform.state.supervisor = None
    result = asyncio.run(_await_first_worker(platform.state, 0))
    assert result["gate"] == "degraded"
    assert result["workers_observed"] == 0


def test_await_first_worker_returns_ok_when_registered(platform):
    result = asyncio.run(_await_first_worker(platform.state, 0))
    assert result["gate"] == "ok"
    assert result["workers_observed"] == 1
