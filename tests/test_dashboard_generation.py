"""Unit tests for the Phase 3 dashboard seeder.

These tests exercise the pure ``build_dashboard`` builder. They never call
ThingsBoard. The integration test in ``test_dashboard_integration.py`` is
opt-in and requires a running stack.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "seed_thingsboard_dashboard",
    ROOT / "scripts" / "seed_thingsboard_dashboard.py",
)
seeder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(seeder)


def _fake_devices() -> dict[str, str]:
    devs: dict[str, str] = {}
    for f in range(1, 11):
        for r in range(1, 21):
            devs[f"b01-f{f:02d}-r{r:03d}"] = f"dev-{f:02d}-{r:03d}"
        devs[f"b01-f{f:02d}-floor"] = f"floor-{f:02d}"
    devs["b01-security"] = "sec-1"
    return devs


def _fake_assets() -> dict[str, str]:
    out: dict[str, str] = {"ZC-Main-Campus": "campus-1", "b01": "bld-1"}
    for f in range(1, 11):
        out[f"b01-f{f:02d}"] = f"asset-floor-{f:02d}"
        for r in range(1, 21):
            out[f"b01-f{f:02d}-r{r:03d}"] = f"asset-room-{f:02d}-{r:03d}"
    return out


def _build():
    return seeder.build_dashboard(_fake_devices(), _fake_assets())


def test_overview_plus_ten_floor_states_plus_room_control():
    dash = _build()
    states = dash["configuration"]["states"]
    assert "default" in states
    assert states["default"]["root"] is True
    assert "room-control" in states
    assert states["room-control"]["root"] is False
    floor_states = [k for k in states if k.startswith("floor-")]
    assert len(floor_states) == 10
    # default + 10 floors + room-control = 12
    assert seeder.dashboard_state_count(dash) == 12


def test_each_floor_state_has_twenty_room_aliases():
    dash = _build()
    for f in range(1, 11):
        assert seeder.dashboard_floor_room_count(dash, f) == 20


def test_dashboard_uses_normalized_map_coords_not_raw():
    dash = _build()
    blob = json.dumps(dash)
    assert "map_x" in blob and "map_y" in blob
    assert "coordinates_x" not in blob
    assert "coordinates_y" not in blob
    assert seeder.dashboard_uses_normalized_coords(dash) is True


def test_overview_includes_security_telemetry_keys():
    dash = _build()
    blob = json.dumps(dash)
    for key in seeder.SECURITY_TELEMETRY_KEYS:
        assert key in blob, f"missing security key {key} in dashboard JSON"


def test_overview_references_all_ten_floor_aggregate_devices():
    dash = _build()
    aliases = dash["configuration"]["entityAliases"]
    floor_alias = next(
        a for a in aliases.values() if a["alias"] == "Floor aggregates"
    )
    entity_ids = floor_alias["filter"]["entityList"]
    assert len(entity_ids) == 10
    assert all(eid.startswith("floor-") for eid in entity_ids)


def test_image_map_widget_uses_map_x_map_y_position_keys():
    dash = _build()
    widgets = dash["configuration"]["widgets"]
    image_maps = [
        w for w in widgets.values()
        if w.get("typeFullFqn") == "system.maps_v2.image_map"
    ]
    assert len(image_maps) == 10  # one per floor
    for w in image_maps:
        settings = w["config"]["settings"]
        assert settings.get("xPosKeyName") == "map_x"
        assert settings.get("yPosKeyName") == "map_y"


def test_widget_types_match_plan():
    dash = _build()
    fqns = {w.get("typeFullFqn") for w in dash["configuration"]["widgets"].values()}
    # Dashboards reference system-bundle widgets with the ``system.`` prefix.
    assert "system.maps_v2.image_map" in fqns
    assert "system.cards.entities_table" in fqns
    assert "system.charts.basic_timeseries" in fqns
    assert "system.cards.markdown_card" in fqns
    assert "system.input_widgets.update_shared_string_attribute" in fqns
    assert "system.input_widgets.update_shared_integer_attribute" in fqns


def test_image_map_has_open_room_control_action():
    dash = _build()
    image_maps = [
        w for w in dash["configuration"]["widgets"].values()
        if w.get("typeFullFqn") == "system.maps_v2.image_map"
    ]
    assert image_maps
    for w in image_maps:
        actions = w["config"].get("actions", {})
        click_actions = actions.get("elementClick", [])
        assert any(
            a.get("type") == "openDashboardState"
            and a.get("targetDashboardStateId") == "room-control"
            and a.get("setEntityId") is True
            for a in click_actions
        ), "image_map missing room-control click action"


def test_room_control_state_has_state_entity_alias_and_inputs():
    dash = _build()
    aliases = dash["configuration"]["entityAliases"]
    state_aliases = [a for a in aliases.values() if a["filter"]["type"] == "stateEntity"]
    assert len(state_aliases) == 1
    rc_alias_id = state_aliases[0]["id"]

    state = dash["configuration"]["states"]["room-control"]
    layout_widget_ids = list(state["layouts"]["main"]["widgets"].keys())
    widgets = dash["configuration"]["widgets"]

    fqns_in_state = {widgets[wid]["typeFullFqn"] for wid in layout_widget_ids}
    assert "system.input_widgets.update_shared_string_attribute" in fqns_in_state
    assert "system.input_widgets.update_shared_integer_attribute" in fqns_in_state

    bound_to_current_room = 0
    for wid in layout_widget_ids:
        for ds in widgets[wid]["config"].get("datasources", []):
            if ds.get("entityAliasId") == rc_alias_id:
                bound_to_current_room += 1
    # 3 input widgets + live telemetry table = 4 datasources bound to currentRoom
    assert bound_to_current_room >= 4


def test_sync_status_widget_present_with_diff_keys_and_function_column():
    dash = _build()
    widgets = dash["configuration"]["widgets"]
    sync = next(
        (w for w in widgets.values() if w.get("title") == "Sync Status (Desired vs Reported)"),
        None,
    )
    assert sync is not None
    keys = sync["config"]["datasources"][0]["dataKeys"]
    names = {k["name"] for k in keys}
    for k in (
        "hvac_mode_desired", "hvac_mode_reported",
        "lighting_dimmer_desired", "lighting_dimmer_reported",
        "target_temp_desired", "target_temp_reported",
    ):
        assert k in names, f"sync table missing {k}"
    func_keys = [k for k in keys if k.get("type") == "function" and k.get("label") == "Sync Status"]
    assert func_keys, "sync table missing computed Sync Status column"
    assert "OUT OF SYNC" in func_keys[0]["settings"]["cellContentFunction"]


def test_evolution_widget_shows_current_version():
    dash = _build()
    widgets = dash["configuration"]["widgets"]
    evo = next(
        (w for w in widgets.values() if w.get("title") == "Fleet evolution (firmware versions)"),
        None,
    )
    assert evo is not None
    keys = evo["config"]["datasources"][0]["dataKeys"]
    assert any(k["name"] == "current_version" for k in keys)


def test_skips_security_panel_when_device_missing():
    devs = _fake_devices()
    devs.pop("b01-security")
    dash = seeder.build_dashboard(devs, _fake_assets())
    blob = json.dumps(dash)
    assert "ota_tamper" not in blob


def test_floor_state_widgets_reference_only_that_floors_devices():
    dash = _build()
    aliases = dash["configuration"]["entityAliases"]
    for f in range(1, 11):
        floor_alias = next(
            a for a in aliases.values() if a["alias"] == f"Floor {f:02d} rooms"
        )
        entity_ids = floor_alias["filter"]["entityList"]
        assert len(entity_ids) == 20
        for eid in entity_ids:
            assert eid.startswith(f"dev-{f:02d}-")


def test_device_dashboard_metadata_normalizes_coords():
    import sys
    spec = importlib.util.spec_from_file_location(
        "seed_thingsboard", ROOT / "scripts" / "seed_thingsboard.py"
    )
    seed_mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["seed_thingsboard"] = seed_mod
    spec.loader.exec_module(seed_mod)

    meta = seed_mod.device_dashboard_metadata(1, 1)
    assert 0.0 <= meta["map_x"] <= 1.0
    assert 0.0 <= meta["map_y"] <= 1.0
    assert meta["floor_no"] == 1
    assert meta["room_on_floor"] == 1
    assert meta["room_type"] in {"lecture_hall", "lab", "office", "corridor"}
