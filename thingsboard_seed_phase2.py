#!/usr/bin/env python3
"""
ThingsBoard Phase 2 seeder

What it does:
- Logs in to ThingsBoard as your tenant admin
- Finds your two device profiles by name
- Creates / updates:
  * 1 Campus asset
  * 1 Building asset
  * 10 Floor assets
  * 200 Room assets
  * 200 Devices (100 MQTT + 100 CoAP)
- Creates relations:
  * Campus -> Building
  * Building -> Floors
  * Floors -> Rooms
  * Rooms -> Devices

Requirements:
    pip install tb-rest-client requests

Run:
    python thingsboard_seed_phase2.py \
      --url http://localhost:9090 \
      --username admin@gmail.com \
      --password admins

Notes:
- This uses ThingsBoard's supported REST layer, not direct PostgreSQL table edits.
- If you already created some entities manually, saving again will update them.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


MQTT_PROFILE_NAME = "MQTT_Room_Device"
COAP_PROFILE_NAME = "CoAP_Room_Device"
ASSET_PROFILE_NAME_FALLBACK = "default"  # only used for display; default asset profile is fetched from TB


@dataclass
class TbAuth:
    token: str
    refresh_token: str


def login_for_jwt(base_url: str, username: str, password: str) -> TbAuth:
    url = f"{base_url.rstrip('/')}/api/auth/login"
    resp = requests.post(
        url,
        json={"username": username, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return TbAuth(token=data["token"], refresh_token=data.get("refreshToken", ""))


def _extract_items(payload: Any) -> list[dict]:
    """
    ThingsBoard list endpoints can vary a bit by version.
    This function tries common shapes:
    - {"data":[...]}
    - {"deviceProfileInfos":[...]}
    - {"assetProfileInfos":[...]}
    - direct list
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "deviceProfileInfos", "assetProfileInfos", "devices", "assets"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def upsert_device_profile(base_url: str, jwt: str, name: str) -> str:
    """Find or create a device profile with MQTT transport type."""
    try:
        return find_profile_id(base_url, jwt, "device", name)
    except RuntimeError:
        pass
    headers = {"X-Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    payload = {
        "name": name,
        "type": "DEFAULT",
        "transportType": "MQTT",
        "provisionType": "DISABLED",
        "profileData": {
            "configuration": {"type": "DEFAULT"},
            "transportConfiguration": {"type": "MQTT"},
            "provisionConfiguration": {"type": "DISABLED"},
            "alarms": [],
        },
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/deviceProfile",
        json=payload, headers=headers, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]["id"]


def find_profile_id(base_url: str, jwt: str, profile_kind: str, profile_name: str) -> str:
    """
    profile_kind: 'device' or 'asset'
    """
    if profile_kind not in {"device", "asset"}:
        raise ValueError("profile_kind must be 'device' or 'asset'")

    endpoint = (
        f"{base_url.rstrip('/')}/api/deviceProfileInfos?pageSize=100&page=0&textSearch={requests.utils.quote(profile_name)}"
        if profile_kind == "device"
        else f"{base_url.rstrip('/')}/api/assetProfileInfos?pageSize=100&page=0&textSearch={requests.utils.quote(profile_name)}"
    )

    resp = requests.get(
        endpoint,
        headers={"X-Authorization": f"Bearer {jwt}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()

    for item in _extract_items(payload):
        name = item.get("name") or item.get("title") or ""
        if name == profile_name:
            return item.get("id", {}).get("id") if isinstance(item.get("id"), dict) else item.get("id")

    # fall back to exact match by scanning all items from the returned page
    for item in _extract_items(payload):
        if item.get("name") == profile_name:
            return item["id"]["id"] if isinstance(item.get("id"), dict) else item["id"]

    raise RuntimeError(f"Profile not found: {profile_kind} profile '{profile_name}'")


def _find_asset_id(base_url: str, jwt: str, name: str) -> Optional[str]:
    """Return the asset entity ID if one with this exact name exists, else None."""
    r = requests.get(
        f"{base_url.rstrip('/')}/api/tenant/assets?pageSize=500&page=0&textSearch={requests.utils.quote(name)}",
        headers={"X-Authorization": f"Bearer {jwt}", "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    for item in _extract_items(r.json()):
        if item.get("name") == name:
            raw_id = item.get("id", {})
            return raw_id.get("id") if isinstance(raw_id, dict) else raw_id
    return None


def _find_device_id(base_url: str, jwt: str, name: str) -> Optional[str]:
    """Return the device entity ID if one with this exact name exists, else None."""
    r = requests.get(
        f"{base_url.rstrip('/')}/api/tenant/devices?pageSize=500&page=0&textSearch={requests.utils.quote(name)}",
        headers={"X-Authorization": f"Bearer {jwt}", "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    for item in _extract_items(r.json()):
        if item.get("name") == name:
            raw_id = item.get("id", {})
            return raw_id.get("id") if isinstance(raw_id, dict) else raw_id
    return None


def _get_default_asset_profile_id(base_url: str, jwt: str) -> str:
    r = requests.get(
        f"{base_url.rstrip('/')}/api/assetProfileInfos?pageSize=100&page=0",
        headers={"X-Authorization": f"Bearer {jwt}", "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    for item in _extract_items(r.json()):
        if item.get("default") or item.get("name") == "default":
            raw_id = item.get("id", {})
            return raw_id.get("id") if isinstance(raw_id, dict) else raw_id
    raise RuntimeError("Default asset profile not found")


def upsert_asset(base_url: str, jwt: str, name: str, profile_id: str) -> str:
    """Create or update an asset by name; return its entity ID."""
    headers = {"X-Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    existing_id = _find_asset_id(base_url, jwt, name)
    payload: Dict[str, Any] = {
        "name": name,
        "label": name,
        "assetProfileId": {"id": profile_id, "entityType": "ASSET_PROFILE"},
    }
    if existing_id:
        payload["id"] = {"id": existing_id, "entityType": "ASSET"}
    r = requests.post(f"{base_url.rstrip('/')}/api/asset", json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["id"]["id"]


def upsert_device(base_url: str, jwt: str, name: str, profile_id: str) -> str:
    """Create or update a device by name; return its entity ID."""
    headers = {"X-Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    existing_id = _find_device_id(base_url, jwt, name)
    payload: Dict[str, Any] = {
        "name": name,
        "label": name,
        "deviceProfileId": {"id": profile_id, "entityType": "DEVICE_PROFILE"},
    }
    if existing_id:
        payload["id"] = {"id": existing_id, "entityType": "DEVICE"}
    r = requests.post(f"{base_url.rstrip('/')}/api/device", json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["id"]["id"]


def save_relation(base_url: str, jwt: str, from_id: str, from_type: str, to_id: str, to_type: str) -> None:
    headers = {"X-Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    payload = {
        "from": {"id": from_id, "entityType": from_type},
        "to": {"id": to_id, "entityType": to_type},
        "type": "Contains",
    }
    requests.post(f"{base_url.rstrip('/')}/api/relation", json=payload, headers=headers, timeout=30).raise_for_status()



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080", help="ThingsBoard base URL")
    parser.add_argument("--username", default="admin@gmail.com", help="Tenant admin email")
    parser.add_argument("--password", default="admins", help="Tenant admin password")
    args = parser.parse_args()

    # 1) Login and discover profile IDs
    auth = login_for_jwt(args.url, args.username, args.password)

    mqtt_profile_id = upsert_device_profile(args.url, auth.token, MQTT_PROFILE_NAME)
    coap_profile_id = upsert_device_profile(args.url, auth.token, COAP_PROFILE_NAME)

    print(f"MQTT device profile id: {mqtt_profile_id}")
    print(f"CoAP  device profile id: {coap_profile_id}")

    # 2) Default asset profile
    asset_profile_id = _get_default_asset_profile_id(args.url, auth.token)

    # 3) Create / update top-level assets
    campus_id  = upsert_asset(args.url, auth.token, "Campus", asset_profile_id)
    building_id = upsert_asset(args.url, auth.token, "b01",   asset_profile_id)

    floor_ids = []
    for floor_no in range(1, 11):
        fid = upsert_asset(args.url, auth.token, f"b01-f{floor_no:02d}", asset_profile_id)
        floor_ids.append(fid)

    room_ids: list[str] = []
    for floor_no in range(1, 11):
        for room_on_floor in range(1, 21):
            rid = upsert_asset(args.url, auth.token, f"b01-f{floor_no:02d}-r{room_on_floor:03d}", asset_profile_id)
            room_ids.append(rid)
        print(f"  Floor {floor_no:02d} room assets done")

    print("Assets created/updated.")

    # 4) Asset hierarchy relations
    save_relation(args.url, auth.token, campus_id, "ASSET", building_id, "ASSET")
    for fid in floor_ids:
        save_relation(args.url, auth.token, building_id, "ASSET", fid, "ASSET")
    for idx, rid in enumerate(room_ids):
        save_relation(args.url, auth.token, floor_ids[idx // 20], "ASSET", rid, "ASSET")

    print("Asset relations created/updated.")

    # 5) Create devices and link each to its room asset
    for floor_no in range(1, 11):
        for room_on_floor in range(1, 21):
            room_name  = f"b01-f{floor_no:02d}-r{room_on_floor:03d}"
            profile_id = mqtt_profile_id if room_on_floor <= 10 else coap_profile_id
            device_id  = upsert_device(args.url, auth.token, room_name, profile_id)

            room_idx = (floor_no - 1) * 20 + (room_on_floor - 1)
            save_relation(args.url, auth.token, room_ids[room_idx], "ASSET", device_id, "DEVICE")
        print(f"  Floor {floor_no:02d} devices done")

    print("Devices created/updated and linked to rooms.")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
