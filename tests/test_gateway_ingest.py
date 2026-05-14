"""Phase 3 gateway ingest smoke test.

This is intentionally opt-in because it needs a running ThingsBoard stack.
Set RUN_TB_INGEST_TEST=1 to execute.
"""

from __future__ import annotations

import os
import time

import pytest
import requests


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TB_INGEST_TEST") != "1",
    reason="requires running ThingsBoard and simulator",
)


def _jwt(base_url: str, username: str, password: str) -> str:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/auth/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def _find_device_id(base_url: str, jwt: str, name: str) -> str:
    r = requests.get(
        f"{base_url.rstrip('/')}/api/tenant/devices?pageSize=10&page=0&textSearch={name}",
        headers={"X-Authorization": f"Bearer {jwt}"},
        timeout=15,
    )
    r.raise_for_status()
    for item in r.json().get("data", []):
        if item.get("name") == name:
            return item["id"]["id"]
    raise AssertionError(f"Device not found: {name}")


def _latest_timeseries(base_url: str, jwt: str, device_id: str, keys: str) -> dict:
    r = requests.get(
        f"{base_url.rstrip('/')}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries",
        params={"keys": keys},
        headers={"X-Authorization": f"Bearer {jwt}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def test_sample_devices_receive_telemetry_and_reported_attrs():
    base_url = os.environ.get("TB_URL", "http://localhost:9090")
    username = os.environ.get("TB_USERNAME", "admin@gmail.com")
    password = os.environ.get("TB_PASSWORD", "admins")
    token = _jwt(base_url, username, password)

    samples = ["b01-f01-r001", "b01-f01-r020", "b01-f05-r010", "b01-f10-r020"]
    deadline = time.time() + 30
    missing: dict[str, list[str]] = {}
    while time.time() < deadline:
        missing.clear()
        for name in samples:
            dev_id = _find_device_id(base_url, token, name)
            values = _latest_timeseries(
                base_url,
                token,
                dev_id,
                "temperature,last_seen,hvac_mode_reported,current_version",
            )
            absent = [k for k in ("temperature", "last_seen", "hvac_mode_reported", "current_version") if k not in values]
            if absent:
                missing[name] = absent
        if not missing:
            return
        time.sleep(2)
    raise AssertionError(f"Missing telemetry/attrs after 30s: {missing}")
