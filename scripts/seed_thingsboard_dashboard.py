#!/usr/bin/env python3
"""ThingsBoard Phase 3 dashboard seeder.

Creates / updates a single Campus Phase 3 Digital Twin dashboard that visualizes
all 200 rooms across 10 floor states plus an overview state. Idempotent: matches
the dashboard by title and updates in place.

Dashboard layout
----------------
- Overview state ("default"):
  - Building status / fleet table (200 rooms with telemetry + reported attrs).
  - Floor aggregate table (10 floor devices).
  - Floor aggregate timeseries chart.
  - Security panel (markdown legend + b01-security tamper telemetry).
- Floor states ("floor-01" ... "floor-10"):
  - Image-map widget backed by the floor SVG, with markers at (map_x, map_y).
  - Per-room entities table for that floor.
  - Markdown legend / control hint.

Marker coordinates come from device SERVER_SCOPE attrs map_x, map_y written by
``seed_thingsboard.py``. Coordinates are normalized 0..1 because ThingsBoard
Image Map widgets expect that range.

Run:
    python scripts/seed_thingsboard_dashboard.py \
        --url http://localhost:9090 \
        --username admin@gmail.com \
        --password admins
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


DASHBOARD_TITLE = "Campus Phase 3 Digital Twin"
ROOT = Path(__file__).resolve().parents[1]
FLOOR_PLAN_DIR = ROOT / "thingsboard" / "assets" / "floor_plans"
WIDGET_DEFAULTS_PATH = (
    Path(__file__).resolve().parent / "widget_defaults" / "system_widget_defaults.json"
)


def _load_widget_defaults() -> Dict[str, dict]:
    if not WIDGET_DEFAULTS_PATH.exists():
        return {}
    return json.loads(WIDGET_DEFAULTS_PATH.read_text(encoding="utf-8"))


WIDGET_DEFAULTS = _load_widget_defaults()

ROOM_TELEMETRY_KEYS = ("temperature", "humidity", "occupancy", "light_level")
ROOM_CLIENT_ATTR_KEYS = (
    "hvac_mode_reported",
    "lighting_dimmer_reported",
    "target_temp_reported",
    "current_version",
    "last_seen",
)
ROOM_SERVER_ATTR_KEYS = ("map_x", "map_y", "room_type", "floor_no", "room_on_floor")
FLOOR_TELEMETRY_KEYS = ("avg_temperature", "room_sample_count")
SECURITY_TELEMETRY_KEYS = (
    "ota_tamper",
    "source_topic",
    "actual_sha256",
    "expected_sha256",
    "client_id",
)


# ---------------------------------------------------------------------------
# Auth + REST helpers
# ---------------------------------------------------------------------------

def login(base_url: str, username: str, password: str) -> str:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def _headers(jwt: str) -> Dict[str, str]:
    return {"X-Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}


def _items(payload: Any) -> List[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "devices", "assets", "dashboards"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def list_devices(base_url: str, jwt: str) -> Dict[str, str]:
    """Return name->deviceId for every tenant device."""
    out: Dict[str, str] = {}
    page = 0
    while True:
        r = requests.get(
            f"{base_url.rstrip('/')}/api/tenant/devices?pageSize=200&page={page}",
            headers={"X-Authorization": f"Bearer {jwt}"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        for item in _items(body):
            name = item.get("name")
            raw = item.get("id", {})
            dev_id = raw.get("id") if isinstance(raw, dict) else raw
            if name and dev_id:
                out[name] = dev_id
        if not body.get("hasNext"):
            break
        page += 1
    return out


def list_assets(base_url: str, jwt: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    page = 0
    while True:
        r = requests.get(
            f"{base_url.rstrip('/')}/api/tenant/assets?pageSize=200&page={page}",
            headers={"X-Authorization": f"Bearer {jwt}"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        for item in _items(body):
            name = item.get("name")
            raw = item.get("id", {})
            asset_id = raw.get("id") if isinstance(raw, dict) else raw
            if name and asset_id:
                out[name] = asset_id
        if not body.get("hasNext"):
            break
        page += 1
    return out


def find_dashboard_id(base_url: str, jwt: str, title: str) -> Optional[str]:
    page = 0
    while True:
        r = requests.get(
            f"{base_url.rstrip('/')}/api/tenant/dashboards?pageSize=200&page={page}",
            headers={"X-Authorization": f"Bearer {jwt}"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        for item in _items(body):
            if item.get("title") == title:
                raw = item.get("id", {})
                return raw.get("id") if isinstance(raw, dict) else raw
        if not body.get("hasNext"):
            return None
        page += 1


def upsert_dashboard(base_url: str, jwt: str, dashboard: dict) -> str:
    existing_id = find_dashboard_id(base_url, jwt, dashboard["title"])
    payload = dict(dashboard)
    if existing_id:
        payload["id"] = {"id": existing_id, "entityType": "DASHBOARD"}
    r = requests.post(
        f"{base_url.rstrip('/')}/api/dashboard",
        json=payload,
        headers=_headers(jwt),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]["id"]


# ---------------------------------------------------------------------------
# Floor-plan loading
# ---------------------------------------------------------------------------

def floor_plan_data_url(floor_no: int) -> str:
    path = FLOOR_PLAN_DIR / f"floor-{floor_no:02d}.svg"
    if not path.exists():
        return ""
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# ---------------------------------------------------------------------------
# Dashboard JSON builders
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


_DEFAULT_COLORS = (
    "#2196F3", "#4CAF50", "#F44336", "#FF9800",
    "#9C27B0", "#00BCD4", "#FFC107", "#607D8B",
    "#3F51B5", "#E91E63",
)


def _data_keys(keys: tuple[str, ...], key_type: str) -> List[dict]:
    out: List[dict] = []
    for i, k in enumerate(keys):
        out.append({
            "name": k,
            "type": key_type,
            "label": k,
            "color": _DEFAULT_COLORS[i % len(_DEFAULT_COLORS)],
            "settings": {},
            "_hash": (i + 1) / 100.0,
        })
    return out


def _widget_template(fqn: str) -> dict:
    """Return a deep copy of the widget defaults for ``fqn``.

    Falls back to a minimal stub if the template was not bundled. Tests pass
    devices/assets directly so they never need network and templates are read
    from the bundled JSON.
    """
    tpl = WIDGET_DEFAULTS.get(fqn)
    if not tpl:
        return {
            "sizeX": 8,
            "sizeY": 5,
            "type": "latest",
            "defaultConfig": {"datasources": [], "settings": {}},
        }
    return copy.deepcopy(tpl)


def _system_fqn(fqn: str) -> str:
    """ThingsBoard stores system-bundle widgets with the ``system.`` prefix."""
    return f"system.{fqn}"


def _entity_alias_single(entity_id: str, entity_type: str, alias_name: str) -> dict:
    return {
        "id": _uuid(),
        "alias": alias_name,
        "filter": {
            "type": "singleEntity",
            "resolveMultiple": False,
            "singleEntity": {"id": entity_id, "entityType": entity_type},
        },
    }


def _entity_alias_list(entity_ids: List[str], entity_type: str, alias_name: str) -> dict:
    return {
        "id": _uuid(),
        "alias": alias_name,
        "filter": {
            "type": "entityList",
            "resolveMultiple": True,
            "entityType": entity_type,
            "entityList": list(entity_ids),
        },
    }


def _build_widget(fqn: str, title: str, config_overrides: dict, *, sizeX: int, sizeY: int) -> dict:
    """Compose a dashboard widget object using the bundled defaultConfig.

    The runtime expects the dashboard to embed a fully-shaped ``config`` dict
    (showTitle, padding, settings, datasources, ...). We start from the system
    widget's defaultConfig, then deep-merge ``config_overrides`` on top.
    """
    tpl = _widget_template(fqn)
    config = tpl.get("defaultConfig") or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = {}
    config = copy.deepcopy(config)

    # Always show our title and pin it as the widget title.
    config["showTitle"] = True
    config["title"] = title

    # Deep-merge overrides at one level deep (settings, datasources, etc.).
    for key, value in config_overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            merged = dict(config[key])
            merged.update(value)
            config[key] = merged
        else:
            config[key] = value

    return {
        "id": _uuid(),
        "typeFullFqn": _system_fqn(fqn),
        "type": tpl.get("type", "latest"),
        "title": title,
        "sizeX": sizeX,
        "sizeY": sizeY,
        "config": config,
        "row": 0,
        "col": 0,
    }


def _widget_markdown(title: str, body_markdown: str) -> dict:
    """Static markdown card. Datasource is empty; text comes from settings."""
    overrides = {
        "datasources": [],
        "settings": {
            "useMarkdownTextFunction": False,
            "markdownTextPattern": body_markdown,
            "applyDefaultMarkdownStyle": True,
            "markdownCss": "",
        },
    }
    return _build_widget(
        "cards.markdown_card", title, overrides, sizeX=24, sizeY=4,
    )


def _widget_entities_table(
    title: str,
    alias_id: str,
    telemetry_keys: tuple[str, ...] = (),
    attribute_keys: tuple[str, ...] = (),
) -> dict:
    keys: List[dict] = []
    keys.extend(_data_keys(telemetry_keys, "timeseries"))
    keys.extend(_data_keys(attribute_keys, "attribute"))
    overrides = {
        "datasources": [
            {
                "type": "entity",
                "name": title,
                "entityAliasId": alias_id,
                "filterId": None,
                "dataKeys": keys,
            }
        ],
        "settings": {
            "entitiesTitle": title,
            "displayEntityName": True,
            "displayPagination": True,
            "defaultPageSize": 25,
            "enableSearch": True,
        },
    }
    return _build_widget(
        "cards.entities_table", title, overrides, sizeX=24, sizeY=10,
    )


def _widget_timeseries_chart(
    title: str,
    alias_id: str,
    telemetry_keys: tuple[str, ...],
) -> dict:
    overrides = {
        "datasources": [
            {
                "type": "entity",
                "name": title,
                "entityAliasId": alias_id,
                "filterId": None,
                "dataKeys": _data_keys(telemetry_keys, "timeseries"),
            }
        ],
        "timewindow": {"realtime": {"timewindowMs": 600000}},
    }
    return _build_widget(
        "charts.basic_timeseries", title, overrides, sizeX=24, sizeY=8,
    )


def _widget_timeseries_chart_multi(
    title: str,
    series: List[tuple[str, str, str]],
    telemetry_key: str,
) -> dict:
    """Multi-series timeseries chart.

    ``series`` is a list of ``(alias_id, datasource_name, series_label)``
    triples. One datasource per series guarantees Flot renders distinct legend
    labels (entityList aliases collapse all series under the same dataKey
    label).
    """
    datasources = []
    for i, (alias_id, ds_name, series_label) in enumerate(series):
        keys = _data_keys((telemetry_key,), "timeseries")
        keys[0]["label"] = series_label
        keys[0]["color"] = _DEFAULT_COLORS[i % len(_DEFAULT_COLORS)]
        datasources.append({
            "type": "entity",
            "name": ds_name,
            "entityAliasId": alias_id,
            "filterId": None,
            "dataKeys": keys,
        })
    overrides = {
        "datasources": datasources,
        "timewindow": {"realtime": {"timewindowMs": 600000}},
    }
    return _build_widget(
        "charts.basic_timeseries", title, overrides, sizeX=24, sizeY=10,
    )


def _widget_shared_attr_input(
    fqn: str,
    title: str,
    alias_id: str,
    attr_key: str,
    *,
    is_integer: bool,
    sizeY: int = 4,
) -> dict:
    """Shared-attribute input widget (string for HVAC mode, integer for dimmer/target temp).

    Spec 3.1.3 / 3.1.4: dashboard pop-up writes shared attrs; simulator
    converges reported state.
    """
    key_type = "shared_attribute"
    data_key = {
        "name": attr_key,
        "type": "attribute",
        "label": attr_key,
        "color": "#2196F3",
        "settings": {},
        "_hash": 0.5,
    }
    overrides = {
        "datasources": [
            {
                "type": "entity",
                "name": title,
                "entityAliasId": alias_id,
                "filterId": None,
                "dataKeys": [data_key],
                "latestDataKeys": [
                    dict(data_key, type=key_type, configMode="basic"),
                ],
                "alarmFilterConfig": {"statusList": ["ACTIVE"]},
            }
        ],
        "settings": {
            "isStringValue": not is_integer,
            "requestTimeout": 500,
            "showResultMessage": True,
        },
    }
    return _build_widget(fqn, title, overrides, sizeX=12, sizeY=sizeY)


def _widget_image_map(
    title: str,
    alias_id: str,
    map_image_data_url: str,
) -> dict:
    """maps_v2.image_map widget bound to room-list alias.

    Marker xPos / yPos come from server-scope attrs ``map_x`` and ``map_y``
    (normalized 0..1). Tooltip shows live telemetry + reported state.
    """
    marker_keys = (
        _data_keys(("temperature", "occupancy"), "timeseries")
        + _data_keys(
            ("map_x", "map_y", "room_type", "hvac_mode_reported", "last_seen"),
            "attribute",
        )
    )
    overrides = {
        "datasources": [
            {
                "type": "entity",
                "name": title,
                "entityAliasId": alias_id,
                "filterId": None,
                "dataKeys": marker_keys,
            }
        ],
        "settings": {
            "provider": "image-map",
            "mapImageUrl": map_image_data_url,
            "xPosKeyName": "map_x",
            "yPosKeyName": "map_y",
            "useDefaultCenterPosition": False,
            "fitMapBounds": True,
            "showLabel": True,
            "label": "${entityName}",
            "showTooltip": True,
            "tooltipPattern": (
                "<b>${entityName}</b><br/>"
                "Temp: ${temperature} °C<br/>"
                "Occupied: ${occupancy}<br/>"
                "HVAC: ${hvac_mode_reported}<br/>"
                "Last seen: ${last_seen}"
            ),
            "useColorFunction": True,
            "colorFunction": (
                "var t = data['temperature'];\n"
                "if (t == null) return '#888';\n"
                "if (t < 20) return '#2196F3';\n"
                "if (t < 24) return '#4CAF50';\n"
                "if (t < 28) return '#FF9800';\n"
                "return '#F44336';"
            ),
            "useMarkerImageFunction": False,
            "markerImageSize": 34,
        },
        "actions": {
            "elementClick": [
                {
                    "id": _uuid(),
                    "name": "Open room control",
                    "icon": "tune",
                    "type": "openDashboardState",
                    "targetDashboardStateId": "room-control",
                    "setEntityId": True,
                    "stateEntityParamName": None,
                    "openInSeparateDialog": False,
                    "openRightLayout": False,
                }
            ]
        },
    }
    return _build_widget(
        "maps_v2.image_map", title, overrides, sizeX=24, sizeY=18,
    )


def _layout_block(widgets_in_order: List[str], widget_objs: Dict[str, dict]) -> Dict[str, dict]:
    """Stack widgets vertically in a 24-col grid using each widget's own sizeY."""
    layout: Dict[str, dict] = {}
    row = 0
    for wid in widgets_in_order:
        w = widget_objs.get(wid, {})
        size_x = int(w.get("sizeX", 24))
        size_y = int(w.get("sizeY", 6))
        layout[wid] = {"sizeX": size_x, "sizeY": size_y, "row": row, "col": 0}
        row += size_y
    return layout


# ---------------------------------------------------------------------------
# Top-level dashboard assembly
# ---------------------------------------------------------------------------

def build_dashboard(
    devices_by_name: Dict[str, str],
    assets_by_name: Dict[str, str],
    floor_plans_by_floor: Optional[Dict[int, str]] = None,
    floor_count: int = 10,
    rooms_per_floor: int = 20,
) -> dict:
    """Pure builder used by tests. No network I/O."""
    floor_plans = floor_plans_by_floor or {}
    widgets: Dict[str, dict] = {}
    aliases: Dict[str, dict] = {}
    states: Dict[str, dict] = {}

    # ---- Aliases ----
    all_room_device_ids: List[str] = []
    for f in range(1, floor_count + 1):
        for r in range(1, rooms_per_floor + 1):
            name = f"b01-f{f:02d}-r{r:03d}"
            dev_id = devices_by_name.get(name)
            if dev_id:
                all_room_device_ids.append(dev_id)

    floor_device_ids = [
        devices_by_name[f"b01-f{f:02d}-floor"]
        for f in range(1, floor_count + 1)
        if f"b01-f{f:02d}-floor" in devices_by_name
    ]

    security_id = devices_by_name.get("b01-security")

    all_rooms_alias = _entity_alias_list(all_room_device_ids, "DEVICE", "All rooms")
    aliases[all_rooms_alias["id"]] = all_rooms_alias

    floor_devices_alias = _entity_alias_list(
        floor_device_ids, "DEVICE", "Floor aggregates"
    )
    aliases[floor_devices_alias["id"]] = floor_devices_alias

    security_alias_id: Optional[str] = None
    if security_id:
        sec_alias = _entity_alias_single(security_id, "DEVICE", "Security tamper")
        aliases[sec_alias["id"]] = sec_alias
        security_alias_id = sec_alias["id"]

    # Per-floor single-entity aliases drive the multi-series floor chart so
    # each Flot legend item shows a distinct floor name. An entityList alias
    # would collapse all 10 series under one shared key label.
    per_floor_alias_ids: List[tuple[int, str]] = []
    for f in range(1, floor_count + 1):
        dev_name = f"b01-f{f:02d}-floor"
        dev_id = devices_by_name.get(dev_name)
        if not dev_id:
            continue
        a = _entity_alias_single(dev_id, "DEVICE", f"Floor {f:02d} aggregate")
        aliases[a["id"]] = a
        per_floor_alias_ids.append((f, a["id"]))

    # State-entity alias resolves to whichever room device the user clicked on
    # the Image Map. The room-control state widgets bind to it.
    current_room_alias = {
        "id": _uuid(),
        "alias": "Current room",
        "filter": {
            "type": "stateEntity",
            "resolveMultiple": False,
            "stateEntityParamName": None,
            "defaultStateEntity": (
                {"id": all_room_device_ids[0], "entityType": "DEVICE"}
                if all_room_device_ids else None
            ),
        },
    }
    aliases[current_room_alias["id"]] = current_room_alias

    # ---- Overview ("default") state ----
    overview_widgets: List[str] = []

    header = _widget_markdown(
        "Campus Phase 3 Digital Twin",
        (
            "## Campus Phase 3 Digital Twin\n\n"
            "Live state of 200 rooms across 10 floors. Use the floor states for the "
            "spatial Image Map. The b01-security panel only shows activity when an OTA "
            "tamper attempt was recently emitted; an empty value here is normal."
        ),
    )
    widgets[header["id"]] = header
    overview_widgets.append(header["id"])

    fleet_table = _widget_entities_table(
        "Fleet status (all rooms)",
        all_rooms_alias["id"],
        telemetry_keys=("temperature", "occupancy"),
        attribute_keys=("hvac_mode_reported", "current_version", "last_seen"),
    )
    widgets[fleet_table["id"]] = fleet_table
    overview_widgets.append(fleet_table["id"])

    # Spec 3.1.5 — Sync Status widget. Surfaces desired-vs-reported divergence
    # plus a computed "Sync Status" column. Operators filter for out-of-sync
    # rooms via the table search input.
    sync_status_keys: List[dict] = []
    sync_status_keys.extend(
        _data_keys(
            ("hvac_mode_desired", "hvac_mode_reported",
             "lighting_dimmer_desired", "lighting_dimmer_reported",
             "target_temp_desired", "target_temp_reported",
             "last_seen"),
            "attribute",
        )
    )
    # Note: dimmer intentionally excluded from sync diff — Phase 1 spec 2.1
    # environmental correlations clamp ``lighting_dimmer`` based on occupancy,
    # so reported can never match desired when the room is empty. Comparing it
    # would mark every empty room as OUT OF SYNC.
    # Use a clean dataKey name (not "f(x)") so TB's server-side sort doesn't
    # 400 with "Invalid entity data page link sort property".
    sync_status_keys.append({
        "name": "sync_status",
        "type": "function",
        "label": "Sync Status",
        "color": "#9C27B0",
        "settings": {
            "useCellContentFunction": True,
            "cellContentFunction": (
                "var d = entity ? entity['hvac_mode_desired'] : null;\n"
                "var r = entity ? entity['hvac_mode_reported'] : null;\n"
                "var td = entity ? entity['target_temp_desired'] : null;\n"
                "var tr = entity ? entity['target_temp_reported'] : null;\n"
                "var diff = (d != null && r != null && d !== r) ||\n"
                "           (td != null && tr != null && Number(td) !== Number(tr));\n"
                "if (diff) return '<span style=\"color:#F44336;font-weight:600\">OUT OF SYNC</span>';\n"
                "if (d == null && td == null) return '<span style=\"color:#999\">no desired</span>';\n"
                "return '<span style=\"color:#4CAF50;font-weight:600\">SYNCED</span>';"
            ),
            "disableSortable": True,
        },
        "_hash": 0.91,
        "funcBody": "return 0;",
    })
    sync_widget = _build_widget(
        "cards.entities_table",
        "Sync Status (Desired vs Reported)",
        {
            "datasources": [
                {
                    "type": "entity",
                    "name": "Sync status",
                    "entityAliasId": all_rooms_alias["id"],
                    "filterId": None,
                    "dataKeys": sync_status_keys,
                }
            ],
            "settings": {
                "entitiesTitle": "Sync Status",
                "displayEntityName": True,
                "displayPagination": True,
                "defaultPageSize": 25,
                "enableSearch": True,
                "enableSelectColumnDisplay": True,
                "defaultSortOrder": "entityName",
            },
        },
        sizeX=24, sizeY=10,
    )
    widgets[sync_widget["id"]] = sync_widget
    overview_widgets.append(sync_widget["id"])

    # Spec 3.2.3 — Fleet evolution / OTA status table.
    evolution_widget = _widget_entities_table(
        "Fleet evolution (firmware versions)",
        all_rooms_alias["id"],
        attribute_keys=("current_version", "last_seen"),
    )
    widgets[evolution_widget["id"]] = evolution_widget
    overview_widgets.append(evolution_widget["id"])

    floor_table = _widget_entities_table(
        "Floor aggregates",
        floor_devices_alias["id"],
        telemetry_keys=FLOOR_TELEMETRY_KEYS,
    )
    widgets[floor_table["id"]] = floor_table
    overview_widgets.append(floor_table["id"])

    floor_chart = _widget_timeseries_chart_multi(
        "Floor average temperature",
        [
            (alias_id, f"Floor {f:02d}", f"Floor {f:02d}")
            for f, alias_id in per_floor_alias_ids
        ],
        "avg_temperature",
    )
    widgets[floor_chart["id"]] = floor_chart
    overview_widgets.append(floor_chart["id"])

    if security_alias_id:
        sec_table = _widget_entities_table(
            "OTA tamper events (b01-security)",
            security_alias_id,
            telemetry_keys=SECURITY_TELEMETRY_KEYS,
        )
        widgets[sec_table["id"]] = sec_table
        overview_widgets.append(sec_table["id"])

        sec_md = _widget_markdown(
            "Security panel",
            (
                "**b01-security** receives tamper telemetry only when an OTA payload "
                "fails SHA-256 verification. Inactive is normal. Recent fields: "
                + ", ".join(SECURITY_TELEMETRY_KEYS)
                + "."
            ),
        )
        widgets[sec_md["id"]] = sec_md
        overview_widgets.append(sec_md["id"])

    states["default"] = {
        "name": "Overview",
        "root": True,
        "layouts": {"main": {"widgets": _layout_block(overview_widgets, widgets)}},
    }

    # ---- Floor states ----
    for f in range(1, floor_count + 1):
        floor_room_ids = [
            devices_by_name[f"b01-f{f:02d}-r{r:03d}"]
            for r in range(1, rooms_per_floor + 1)
            if f"b01-f{f:02d}-r{r:03d}" in devices_by_name
        ]
        floor_alias = _entity_alias_list(
            floor_room_ids, "DEVICE", f"Floor {f:02d} rooms"
        )
        aliases[floor_alias["id"]] = floor_alias

        floor_state_widgets: List[str] = []

        legend = _widget_markdown(
            f"Floor {f:02d} legend",
            (
                f"### Floor {f:02d}\n\n"
                "Markers are colored by current temperature: "
                "blue (<20°C), green (20-23), orange (24-27), red (28+).\n\n"
                "Click a marker to see live telemetry. Use the device control panel "
                "in ThingsBoard to write `hvac_mode_desired`, `lighting_dimmer_desired`, "
                "and `target_temp_desired` — the simulator will converge reported state."
            ),
        )
        widgets[legend["id"]] = legend
        floor_state_widgets.append(legend["id"])

        plan_url = floor_plans.get(f, "")
        img = _widget_image_map(
            f"Floor {f:02d} map",
            floor_alias["id"],
            plan_url,
        )
        widgets[img["id"]] = img
        floor_state_widgets.append(img["id"])

        room_table = _widget_entities_table(
            f"Floor {f:02d} rooms",
            floor_alias["id"],
            telemetry_keys=ROOM_TELEMETRY_KEYS,
            attribute_keys=ROOM_CLIENT_ATTR_KEYS + ROOM_SERVER_ATTR_KEYS,
        )
        widgets[room_table["id"]] = room_table
        floor_state_widgets.append(room_table["id"])

        states[f"floor-{f:02d}"] = {
            "name": f"Floor {f:02d}",
            "root": False,
            "layouts": {"main": {"widgets": _layout_block(floor_state_widgets, widgets)}},
        }

    # ---- Room-control state (spec 3.1.3) ----
    rc_widgets: List[str] = []

    rc_header = _widget_markdown(
        "Room control",
        (
            "### Room control\n\n"
            "Editing the values below writes them as **shared attributes** "
            "(`hvac_mode_desired`, `lighting_dimmer_desired`, `target_temp_desired`). "
            "The simulator subscribes to these and converges the reported state. "
            "Watch the live telemetry panel to confirm. To navigate back, click "
            "**Overview** in the dashboard state selector."
        ),
    )
    widgets[rc_header["id"]] = rc_header
    rc_widgets.append(rc_header["id"])

    hvac_input = _widget_shared_attr_input(
        "input_widgets.update_shared_string_attribute",
        "Set HVAC mode (ON/OFF/ECO)",
        current_room_alias["id"],
        "hvac_mode_desired",
        is_integer=False,
        sizeY=4,
    )
    widgets[hvac_input["id"]] = hvac_input
    rc_widgets.append(hvac_input["id"])

    dimmer_input = _widget_shared_attr_input(
        "input_widgets.update_shared_integer_attribute",
        "Set lighting dimmer (0-100)",
        current_room_alias["id"],
        "lighting_dimmer_desired",
        is_integer=True,
        sizeY=4,
    )
    widgets[dimmer_input["id"]] = dimmer_input
    rc_widgets.append(dimmer_input["id"])

    target_temp_input = _widget_shared_attr_input(
        "input_widgets.update_shared_integer_attribute",
        "Set target temperature (°C)",
        current_room_alias["id"],
        "target_temp_desired",
        is_integer=True,
        sizeY=4,
    )
    widgets[target_temp_input["id"]] = target_temp_input
    rc_widgets.append(target_temp_input["id"])

    rc_live_table = _widget_entities_table(
        "Live room telemetry & reported state",
        current_room_alias["id"],
        telemetry_keys=ROOM_TELEMETRY_KEYS,
        attribute_keys=ROOM_CLIENT_ATTR_KEYS,
    )
    widgets[rc_live_table["id"]] = rc_live_table
    rc_widgets.append(rc_live_table["id"])

    states["room-control"] = {
        "name": "Room control",
        "root": False,
        "layouts": {"main": {"widgets": _layout_block(rc_widgets, widgets)}},
    }

    return {
        "title": DASHBOARD_TITLE,
        "name": DASHBOARD_TITLE,
        "configuration": {
            "description": "Phase 3 Digital Twin: 1 overview state + 10 floor states.",
            "widgets": widgets,
            "states": states,
            "entityAliases": aliases,
            "filters": {},
            "timewindow": {"realtime": {"timewindowMs": 60000}},
            "settings": {"stateControllerId": "entity"},
        },
    }


# ---------------------------------------------------------------------------
# Self-check helpers (used by unit tests)
# ---------------------------------------------------------------------------

def dashboard_uses_normalized_coords(dashboard: dict) -> bool:
    blob = json.dumps(dashboard)
    if "coordinates_x" in blob or "coordinates_y" in blob:
        return False
    return "map_x" in blob and "map_y" in blob


def dashboard_state_count(dashboard: dict) -> int:
    return len(dashboard["configuration"]["states"])


def dashboard_floor_room_count(dashboard: dict, floor_no: int) -> int:
    state = dashboard["configuration"]["states"].get(f"floor-{floor_no:02d}")
    if not state:
        return 0
    aliases = dashboard["configuration"]["entityAliases"]
    widgets = dashboard["configuration"]["widgets"]
    layout_widget_ids = list(state["layouts"]["main"]["widgets"].keys())
    seen = 0
    for wid in layout_widget_ids:
        widget = widgets.get(wid, {})
        for ds in widget.get("config", {}).get("datasources", []):
            alias_id = ds.get("entityAliasId")
            alias = aliases.get(alias_id, {})
            entity_list = alias.get("filter", {}).get("entityList", [])
            seen = max(seen, len(entity_list))
    return seen


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--username", default="admin@gmail.com")
    parser.add_argument("--password", default="admins")
    args = parser.parse_args()

    jwt = login(args.url, args.username, args.password)
    devices = list_devices(args.url, jwt)
    assets = list_assets(args.url, jwt)

    missing_floors = [
        f for f in range(1, 11) if f"b01-f{f:02d}-floor" not in devices
    ]
    if missing_floors:
        print(f"[warn] missing floor aggregate devices: {missing_floors}")
    if "b01-security" not in devices:
        print("[warn] b01-security device not found; tamper panel will be skipped")

    floor_plans = {f: floor_plan_data_url(f) for f in range(1, 11)}

    dashboard = build_dashboard(devices, assets, floor_plans)
    dashboard_id = upsert_dashboard(args.url, jwt, dashboard)

    print(f"Upserted dashboard '{DASHBOARD_TITLE}' id={dashboard_id}")
    print(
        f"  states={dashboard_state_count(dashboard)}, "
        f"widgets={len(dashboard['configuration']['widgets'])}, "
        f"aliases={len(dashboard['configuration']['entityAliases'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
