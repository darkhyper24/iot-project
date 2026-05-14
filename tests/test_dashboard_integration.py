"""Phase 3 dashboard integration test.

Opt-in. Requires a running ThingsBoard with seed scripts already executed.
Set RUN_TB_DASHBOARD_TEST=1 to execute. Example:

    RUN_TB_DASHBOARD_TEST=1 \
    TB_URL=http://localhost:9090 \
    TB_USER=tenant@thingsboard.org \
    TB_PASS=tenant \
    python -m pytest -q tests/test_dashboard_integration.py
"""
from __future__ import annotations

import os

import pytest
import requests


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TB_DASHBOARD_TEST") != "1",
    reason="requires running ThingsBoard with seeded dashboard",
)


DASHBOARD_TITLE = "Campus Phase 3 Digital Twin"


def _env() -> tuple[str, str, str]:
    return (
        os.environ.get("TB_URL", "http://localhost:9090"),
        os.environ.get("TB_USER", "tenant@thingsboard.org"),
        os.environ.get("TB_PASS", "tenant"),
    )


def _jwt(base: str, user: str, pwd: str) -> str:
    r = requests.post(
        f"{base.rstrip('/')}/api/auth/login",
        json={"username": user, "password": pwd},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def _hdrs(jwt: str) -> dict:
    return {"X-Authorization": f"Bearer {jwt}"}


def _find_dashboard(base: str, jwt: str, title: str) -> dict | None:
    page = 0
    while True:
        r = requests.get(
            f"{base.rstrip('/')}/api/tenant/dashboards?pageSize=200&page={page}",
            headers=_hdrs(jwt),
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        for item in body.get("data", []):
            if item.get("title") == title:
                return item
        if not body.get("hasNext"):
            return None
        page += 1


def _device_id(base: str, jwt: str, name: str) -> str | None:
    r = requests.get(
        f"{base.rstrip('/')}/api/tenant/devices?pageSize=500&page=0&textSearch={name}",
        headers=_hdrs(jwt),
        timeout=15,
    )
    r.raise_for_status()
    for item in r.json().get("data", []):
        if item.get("name") == name:
            raw = item.get("id", {})
            return raw.get("id") if isinstance(raw, dict) else raw
    return None


def test_dashboard_exists_by_title():
    base, user, pwd = _env()
    jwt = _jwt(base, user, pwd)
    found = _find_dashboard(base, jwt, DASHBOARD_TITLE)
    assert found is not None, f"dashboard '{DASHBOARD_TITLE}' missing"


def test_dashboard_has_eleven_states_and_floor_aggregates():
    base, user, pwd = _env()
    jwt = _jwt(base, user, pwd)
    summary = _find_dashboard(base, jwt, DASHBOARD_TITLE)
    assert summary is not None
    raw = summary.get("id", {})
    dash_id = raw.get("id") if isinstance(raw, dict) else raw

    r = requests.get(
        f"{base.rstrip('/')}/api/dashboard/{dash_id}",
        headers=_hdrs(jwt),
        timeout=15,
    )
    r.raise_for_status()
    full = r.json()
    cfg = full.get("configuration", {})
    states = cfg.get("states", {})
    assert len(states) == 11

    aliases = cfg.get("entityAliases", {})
    floor_alias = next(
        (a for a in aliases.values() if a.get("alias") == "Floor aggregates"),
        None,
    )
    assert floor_alias is not None, "Floor aggregates alias missing"
    entity_ids = floor_alias["filter"].get("entityList", [])
    assert len(entity_ids) == 10

    expected_floor_ids = []
    for f in range(1, 11):
        did = _device_id(base, jwt, f"b01-f{f:02d}-floor")
        assert did is not None, f"floor aggregate device for floor {f} missing"
        expected_floor_ids.append(did)

    assert set(entity_ids) == set(expected_floor_ids)


def test_room_devices_have_normalized_map_attrs():
    base, user, pwd = _env()
    jwt = _jwt(base, user, pwd)
    did = _device_id(base, jwt, "b01-f01-r001")
    assert did is not None

    r = requests.get(
        f"{base.rstrip('/')}/api/plugins/telemetry/DEVICE/{did}"
        f"/values/attributes/SERVER_SCOPE",
        headers=_hdrs(jwt),
        timeout=15,
    )
    r.raise_for_status()
    attrs = {item["key"]: item["value"] for item in r.json()}
    for key in ("map_x", "map_y", "room_type", "floor_no", "room_on_floor"):
        assert key in attrs, f"device server attr {key} missing"
    assert 0.0 <= float(attrs["map_x"]) <= 1.0
    assert 0.0 <= float(attrs["map_y"]) <= 1.0
