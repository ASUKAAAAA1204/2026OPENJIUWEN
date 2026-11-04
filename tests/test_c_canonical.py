import pytest

from assurance_ledger.ledger.canonical import (
    CanonicalError,
    canonical_bytes,
    digest_json,
    parse_json,
)


def test_canonical_sorts_keys_and_rejects_duplicates():
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    with pytest.raises(CanonicalError):
        parse_json('{"a":1,"a":2}')


def test_canonical_rejects_nan_and_non_json_types():
    with pytest.raises(CanonicalError):
        canonical_bytes({"x": float("nan")})
    with pytest.raises(CanonicalError):
        canonical_bytes({"x": object()})


def test_digest_is_stable_across_key_order():
    first = digest_json({"a": 1, "b": [2, 3]})
    second = digest_json({"b": [2, 3], "a": 1})
    assert first == second
    assert first.startswith("sha256:")


def test_digest_changes_when_business_value_changes():
    assert digest_json({"score": 100}) != digest_json({"score": 99})
