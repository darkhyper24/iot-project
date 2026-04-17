#!/usr/bin/env python3
"""Render 10 floor-specific Node-RED flows.json files from the template.

For each floor (1..10):
  - Replaces __FLOOR_ID__ / __FLOOR_NUM__ tokens.
  - Injects 10 `coap request` observer nodes wired to the republisher, resolved
    from config/coap_registry.json (written by the simulator at startup).
  - Writes config/gateways/floor_XX/flows.json alongside a settings.js.

Re-run any time the room count or CoAP port base changes.
"""
import json
import os
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG_YAML = ROOT / "config" / "config.yaml"
TEMPLATE = ROOT / "config" / "gateways" / "_template" / "flows.json"
REGISTRY = ROOT / "config" / "coap_registry.json"


def build_observer_nodes(floor_num: int, cfg: dict, registry: dict | None) -> list[dict]:
    building_id = cfg["building"]["id"]
    coap_rooms = cfg.get("transport", {}).get("coap_rooms_per_floor", 10)
    rooms_per_floor = cfg["building"]["rooms_per_floor"]
    advertise_host = cfg["coap"].get("advertise_host", "simulator")
    base_port = cfg["coap"].get("base_port", 5683)

    observers: list[dict] = []
    for idx, r in enumerate(range(1, coap_rooms + 1), start=1):
        room_num = floor_num * 100 + r
        room_id = f"{building_id}-f{floor_num:02d}-r{room_num:03d}"
        room_slug = f"r{room_num:03d}"
        # Must match Room.__init__ global_index: (floor-1)*rooms_per_floor + (room_num-1).
        global_index = (floor_num - 1) * rooms_per_floor + (r - 1)
        port = base_port + global_index
        if registry:
            entry = next((e for e in registry.get("rooms", []) if e["room_id"] == room_id), None)
            if entry:
                port = entry["port"]
                advertise_host = entry.get("host", advertise_host)

        observers.append({
            "id": f"coap-obs-{idx}",
            "type": "coap request",
            "name": f"observe {room_slug}/telemetry",
            "observe": True,
            "method": "GET",
            "url": f"coap://{advertise_host}:{port}/f{floor_num:02d}/{room_slug}/telemetry",
            "contentFormat": "application/json",
            "rawbuffer": False,
            "multicast": "",
            "wires": [["coap-observe-republish"]],
        })
    return observers


def render_floor(floor_num: int, cfg: dict, registry: dict | None) -> list[dict]:
    template = json.loads(
        TEMPLATE.read_text()
            .replace("__FLOOR_ID__", f"f{floor_num:02d}")
            .replace("__FLOOR_NUM__", f"{floor_num:02d}")
    )
    flows = deepcopy(template)
    flows.extend(build_observer_nodes(floor_num, cfg, registry))
    return flows


def write_settings(out_dir: Path) -> None:
    (out_dir / "settings.js").write_text(
        "module.exports = {\n"
        "    flowFile: 'flows.json',\n"
        "    flowFilePretty: true,\n"
        "    credentialSecret: process.env.NODE_RED_CREDENTIAL_SECRET || 'gw-secret',\n"
        "    functionGlobalContext: {},\n"
        "    uiPort: process.env.PORT || 1880,\n"
        "    httpAdminRoot: '/admin',\n"
        "    httpNodeRoot: '/',\n"
        "};\n"
    )


def main() -> None:
    cfg = yaml.safe_load(CFG_YAML.read_text())
    registry = None
    if REGISTRY.exists():
        try:
            registry = json.loads(REGISTRY.read_text())
        except Exception as e:
            print(f"warn: failed to read {REGISTRY}: {e}")

    floors = cfg["building"]["floors"]
    for f in range(1, floors + 1):
        out_dir = ROOT / "config" / "gateways" / f"floor_{f:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        flows = render_floor(f, cfg, registry)
        (out_dir / "flows.json").write_text(json.dumps(flows, indent=2))
        write_settings(out_dir)
        print(f"wrote {out_dir/'flows.json'} ({len(flows)} nodes)")


if __name__ == "__main__":
    main()
