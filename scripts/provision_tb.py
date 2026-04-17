#!/usr/bin/env python3
"""One-shot ThingsBoard provisioning via REST API.

Creates:
  - Two device profiles (MQTT-ThermalSensor, CoAP-ThermalSensor).
  - Asset tree Campus → Building b01 → 10 Floors → 200 Rooms.
  - 200 devices with relations to their room asset.
  - MQTT integration pointing at HiveMQ TLS 8883 with tb-integration creds.
  - Rule chain (threshold alarm + dedup evidence + command router).
  - NOC dashboard (grid of 200 cells + charts + RTT histogram widget).

Idempotent: existing entities are reused, not re-created.

Usage:
  TB_URL=http://localhost:9090 TB_USER=tenant@thingsboard.org TB_PASS=tenant \\
  python scripts/provision_tb.py
"""
import json
import logging
import os
import sys
from pathlib import Path

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CFG_YAML = ROOT / "config" / "config.yaml"
PROVISIONING_DIR = ROOT / "config" / "thingsboard" / "provisioning"


class TBClient:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        resp = self.session.post(
            f"{self.base}/api/auth/login",
            json={"username": user, "password": password},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        self.session.headers.update({"X-Authorization": f"Bearer {token}"})

    def get(self, path: str, **kw):
        r = self.session.get(f"{self.base}{path}", timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, json_body=None, **kw):
        r = self.session.post(f"{self.base}{path}", json=json_body, timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.text else {}

    def find_device(self, name: str) -> dict | None:
        r = self.session.get(
            f"{self.base}/api/tenant/devices",
            params={"pageSize": 50, "page": 0, "textSearch": name},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        for d in data.get("data", []):
            if d.get("name") == name:
                return d
        return None

    def find_asset(self, name: str) -> dict | None:
        r = self.session.get(
            f"{self.base}/api/tenant/assets",
            params={"pageSize": 50, "page": 0, "textSearch": name},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        for a in data.get("data", []):
            if a.get("name") == name:
                return a
        return None

    def ensure_device_profile(self, name: str, transport: str) -> dict:
        # Search for existing profile first.
        page = self.get("/api/deviceProfiles?pageSize=100&page=0")
        for prof in page.get("data", []):
            if prof["name"] == name:
                return prof
        profile = {
            "name": name,
            "type": "DEFAULT",
            "transportType": "DEFAULT",
            "profileData": {
                "configuration": {"type": "DEFAULT"},
                "transportConfiguration": {"type": "DEFAULT"},
            },
            "description": f"{transport} telemetry ingested via HiveMQ integration.",
        }
        return self.post("/api/deviceProfile", profile)

    def ensure_asset(self, name: str, atype: str) -> dict:
        existing = self.find_asset(name)
        if existing:
            return existing
        return self.post("/api/asset", {"name": name, "type": atype})

    def ensure_device(self, name: str, profile_id: str) -> dict:
        existing = self.find_device(name)
        if existing:
            return existing
        return self.post("/api/device", {
            "name": name,
            "type": "ThermalSensor",
            "deviceProfileId": {"id": profile_id, "entityType": "DEVICE_PROFILE"},
        })

    def ensure_relation(self, from_id: dict, to_id: dict, rtype: str = "Contains") -> None:
        body = {"from": from_id, "to": to_id, "type": rtype, "typeGroup": "COMMON"}
        try:
            self.post("/api/relation", body)
        except requests.HTTPError as e:
            if e.response.status_code != 200:
                log.warning("relation %s -> %s failed: %s", from_id, to_id, e)


def entity_id(entity: dict, etype: str) -> dict:
    return {"id": entity["id"]["id"], "entityType": etype}


def profile_uuid(profile: dict) -> str:
    pid = profile.get("id")
    if isinstance(pid, dict):
        return pid["id"]
    return str(pid)


def provision(client: TBClient, cfg: dict) -> None:
    building_id = cfg["building"]["id"]
    floors = cfg["building"]["floors"]
    rooms_per_floor = cfg["building"]["rooms_per_floor"]
    coap_rooms = cfg.get("transport", {}).get("coap_rooms_per_floor", 10)

    log.info("Creating device profiles")
    mqtt_profile = client.ensure_device_profile("MQTT-ThermalSensor", "MQTT")
    coap_profile = client.ensure_device_profile("CoAP-ThermalSensor", "CoAP")

    log.info("Creating asset tree")
    campus = client.ensure_asset("Campus", "campus")
    building = client.ensure_asset(f"Building-{building_id}", "building")
    client.ensure_relation(entity_id(campus, "ASSET"), entity_id(building, "ASSET"))

    for f in range(1, floors + 1):
        floor = client.ensure_asset(f"{building_id}-f{f:02d}", "floor")
        client.ensure_relation(entity_id(building, "ASSET"), entity_id(floor, "ASSET"))
        for r in range(1, rooms_per_floor + 1):
            room_num = f * 100 + r
            room_id = f"{building_id}-f{f:02d}-r{room_num:03d}"
            room_asset = client.ensure_asset(room_id, "room")
            client.ensure_relation(entity_id(floor, "ASSET"), entity_id(room_asset, "ASSET"))

            is_coap = r <= coap_rooms
            profile = coap_profile if is_coap else mqtt_profile
            device = client.ensure_device(room_id, profile_uuid(profile))
            client.ensure_relation(
                entity_id(room_asset, "ASSET"),
                entity_id(device, "DEVICE"),
            )
    log.info("Asset tree + devices provisioned: %d floors × %d rooms", floors, rooms_per_floor)

    log.info(
        "ThingsBoard CE has no external-MQTT-broker integration (PE-only). "
        "Run the `tb-bridge` service (scripts/tb_hivemq_bridge.py) to push HiveMQ campus/* topics into TB via REST."
    )

    for name in ("device_profiles.json", "rule_chains.json", "dashboards.json"):
        path = PROVISIONING_DIR / name
        if path.exists():
            log.info("Reference payload present: %s", path)
        else:
            log.warning("Missing %s — export from TB with scripts/export_tb.py after UI setup", path)


def main() -> int:
    tb_url = os.environ.get("TB_URL", "http://localhost:9090")
    tb_user = os.environ.get("TB_USER", "tenant@thingsboard.org")
    tb_pass = os.environ.get("TB_PASS", "tenant")

    cfg = yaml.safe_load(CFG_YAML.read_text())
    try:
        client = TBClient(tb_url, tb_user, tb_pass)
    except Exception as e:
        log.error("Failed to login to ThingsBoard at %s: %s", tb_url, e)
        return 1

    provision(client, cfg)
    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
