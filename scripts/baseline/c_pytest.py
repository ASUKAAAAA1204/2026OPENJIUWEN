"""Record exact pytest identities and skips for the C regression runner."""
import json
import ipaddress
import socket
from pathlib import Path

STATE = {}


def _loopback(address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode('ascii')
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _offline_guard():
    originals = {name: getattr(socket.socket, name) for name in ('connect', 'connect_ex')}
    original_resolver = socket.getaddrinfo
    def wrap_connect(original):
        def guarded(client, address):
            if client.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
                raise OSError('C offline pytest forbids non-loopback connections')
            return original(client, address)
        return guarded
    def resolve(host, *args, **kwargs):
        if host is not None and not _loopback(host):
            raise OSError('C offline pytest forbids external DNS resolution')
        return original_resolver(host, *args, **kwargs)
    for name, original in originals.items():
        setattr(socket.socket, name, wrap_connect(original))
    socket.getaddrinfo = resolve
    def restore():
        for name, original in originals.items():
            setattr(socket.socket, name, original)
        socket.getaddrinfo = original_resolver
    return restore


def pytest_addoption(parser):
    parser.addoption("--c-collection-file", default=None, help="C baseline evidence JSON")
    parser.addoption("--c-offline", action="store_true", help="Block pytest-process external socket connections and DNS")


def pytest_configure(config):
    STATE.clear()
    STATE.update(path=config.getoption("--c-collection-file"), nodeids=[], skipped=[])
    if config.getoption('--c-offline'):
        config.add_cleanup(_offline_guard())


def pytest_collection_finish(session):
    STATE["nodeids"] = [item.nodeid for item in session.items]


def pytest_runtest_logreport(report):
    if report.skipped:
        STATE["skipped"].append({
            "nodeid": report.nodeid,
            "kind": "xfail" if hasattr(report, "wasxfail") else "skip",
            "reason": str(report.longrepr),
        })


def pytest_sessionfinish(session, exitstatus):
    if STATE.get("path"):
        destination = Path(STATE["path"])
        destination.write_text(json.dumps({
            "nodeids": STATE["nodeids"], "skipped": STATE["skipped"],
            "exitstatus": int(exitstatus),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
