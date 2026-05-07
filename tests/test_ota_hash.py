"""Plan B.5 — canonical OTA hash + signature round-trip."""

import json

from simulator.engine.ota import (
    canonical_unsigned_bytes,
    compute_signature,
    verify_signature,
    version_not_newer,
)


def test_canonical_excludes_sha256_only():
    payload = {"version": "2", "params": {"alpha": 0.02}, "ts": 100, "sha256": "deadbeef"}
    body = canonical_unsigned_bytes(payload)
    decoded = json.loads(body.decode("utf-8"))
    assert "sha256" not in decoded
    assert decoded == {"version": "2", "params": {"alpha": 0.02}, "ts": 100}


def test_signature_round_trip():
    payload = {"version": "3", "params": {"beta": 0.3, "alpha": 0.05}, "ts": 1700000000}
    payload["sha256"] = compute_signature(payload)
    assert verify_signature(payload)


def test_signature_detects_tamper():
    payload = {"version": "1", "params": {"alpha": 0.01}, "ts": 1, "sha256": "0" * 64}
    assert not verify_signature(payload)


def test_key_order_does_not_matter():
    a = {"version": "1", "params": {"a": 1, "b": 2}, "ts": 100}
    b = {"ts": 100, "params": {"b": 2, "a": 1}, "version": "1"}
    assert compute_signature(a) == compute_signature(b)


def test_version_guard_skips_equal_or_older_versions():
    assert version_not_newer("2", "2")
    assert version_not_newer("1.9", "2.0")
    assert not version_not_newer("2.1", "2.0")
