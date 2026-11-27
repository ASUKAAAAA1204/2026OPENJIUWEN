from __future__ import annotations

import time

from assurance_ledger.api.limits import TokenBucket


def _worker(platform) -> dict[str, str]:
    return {"X-Worker-Token": platform.worker_token}


def test_internal_body_limit(platform_factory):
    platform = platform_factory(internal_max_body_bytes=1024)
    oversized = platform.client.post(
        "/internal/v1/turns/claim",
        json={"worker_id": "x" * 2000}, headers=_worker(platform))
    assert oversized.status_code == 413, oversized.text
    assert oversized.json()["error"]["code"] == "INTERNAL_BODY_TOO_LARGE"
    healthy = platform.client.get("/internal/v1/health", headers=_worker(platform))
    assert healthy.status_code == 200, healthy.text


def test_internal_body_limit_chunked(platform_factory):
    platform = platform_factory(internal_max_body_bytes=1024)
    chunks = (part for part in (b'{"worker_id":"', b"x" * 2000, b'"}'))
    oversized = platform.client.post(
        "/internal/v1/turns/claim", content=chunks,
        headers={**_worker(platform), "Content-Type": "application/json"})
    assert oversized.status_code == 413, oversized.text
    assert oversized.json()["error"]["code"] == "INTERNAL_BODY_TOO_LARGE"


def test_internal_rate_limit(platform_factory):
    platform = platform_factory(internal_rate_limit_per_second=1,
                                internal_burst=1)
    time.sleep(1.1)
    first = platform.client.get("/internal/v1/health", headers=_worker(platform))
    assert first.status_code == 200, first.text
    second = platform.client.get("/internal/v1/health", headers=_worker(platform))
    assert second.status_code == 429, second.text
    assert second.json()["error"]["code"] == "INTERNAL_RATE_LIMITED"
    assert second.headers.get("retry-after") == "1"


def test_public_routes_not_limited(platform_factory):
    platform = platform_factory(internal_rate_limit_per_second=1,
                                internal_burst=2)
    for _ in range(5):
        response = platform.client.get("/healthz")
        assert response.status_code == 200


def test_token_bucket_refills_and_isolates_keys():
    bucket = TokenBucket(rate=1.0, burst=2.0)
    assert bucket.allow("a", now=0.0)
    assert bucket.allow("a", now=0.0)
    assert not bucket.allow("a", now=0.0)
    assert bucket.allow("b", now=0.0)
    assert bucket.allow("a", now=1.0)
    assert not bucket.allow("a", now=1.0)
