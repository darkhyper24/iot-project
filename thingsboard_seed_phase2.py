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
#login as admin in thingsboard then create a tenant with email admin@gmail.com and password admins then login as the tenant then create 2 device profiles MQTT_Room_Device and CoAP_Room_Device and both with transport type mqtt after that u can run this seeder file
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from tb_rest_client.rest_client_ce import RestClientCE
from tb_rest_client.rest import ApiException
from tb_rest_client.rest_client_ce import Asset, EntityRelation


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


def make_relation(parent_id, child_id, relation_type="Contains") -> EntityRelation:
    return EntityRelation(_from=parent_id, to=child_id, type=relation_type)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080", help="ThingsBoard base URL")
    parser.add_argument("--username", default="admin@gmail.com", help="Tenant admin email")
    parser.add_argument("--password", default="admins", help="Tenant admin password")
    args = parser.parse_args()

    # 1) Login and discover profile IDs
    auth = login_for_jwt(args.url, args.username, args.password)

    mqtt_profile_id = find_profile_id(args.url, auth.token, "device", MQTT_PROFILE_NAME)
    coap_profile_id = find_profile_id(args.url, auth.token, "device", COAP_PROFILE_NAME)

    print(f"Found MQTT device profile id: {mqtt_profile_id}")
    print(f"Found CoAP device profile id:  {coap_profile_id}")

    # 2) Use the supported ThingsBoard REST client to create / update entities
    with RestClientCE(base_url=args.url) as rest_client:
        try:
            rest_client.login(username=args.username, password=args.password)

            # default asset profile is enough for this phase unless you want custom asset profiles
            default_asset_profile_id = rest_client.get_default_asset_profile_info().id

            # 3) Create assets
            campus = rest_client.save_asset(Asset(name="Campus", asset_profile_id=default_asset_profile_id))
            building = rest_client.save_asset(Asset(name="b01", asset_profile_id=default_asset_profile_id))

            floors = []
            for floor_no in range(1, 11):
                floor_name = f"b01-f{floor_no:02d}"
                floors.append(
                    rest_client.save_asset(Asset(name=floor_name, asset_profile_id=default_asset_profile_id))
                )

            rooms = []
            for room_no in range(1, 201):
                floor_no = (room_no - 1) // 20 + 1
                room_name = f"b01-f{floor_no:02d}-r{room_no:03d}"
                rooms.append(
                    rest_client.save_asset(Asset(name=room_name, asset_profile_id=default_asset_profile_id))
                )

            print("Assets created/updated.")

            # 4) Link asset hierarchy
            rest_client.save_relation(make_relation(campus.id, building.id))
            for floor in floors:
                rest_client.save_relation(make_relation(building.id, floor.id))

            for idx, room in enumerate(rooms, start=1):
                floor_no = (idx - 1) // 20
                rest_client.save_relation(make_relation(floors[floor_no].id, room.id))

            print("Asset relations created/updated.")

            # 5) Create devices and link each device to its room
            headers = {
                "X-Authorization": f"Bearer {auth.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            for room_no in range(1, 201):
                floor_no = (room_no - 1) // 20 + 1
                room_name = f"b01-f{floor_no:02d}-r{room_no:03d}"
                # rooms 1-10 of each floor → MQTT, rooms 11-20 → CoAP
                profile_id = mqtt_profile_id if (room_no - 1) % 20 < 10 else coap_profile_id

                device_payload = {
                    "name": room_name,
                    "label": room_name,
                    "deviceProfileId": {"id": profile_id, "entityType": "DEVICE_PROFILE"},
                }
                dev_resp = requests.post(
                    f"{args.url.rstrip('/')}/api/device",
                    json=device_payload,
                    headers=headers,
                    timeout=30,
                )
                dev_resp.raise_for_status()
                device_id = dev_resp.json()["id"]["id"]

                # room asset -> device relation
                room_asset = rooms[room_no - 1]
                room_asset_id = room_asset.id.id if hasattr(room_asset.id, "id") else room_asset.id["id"]
                rel_payload = {
                    "from": {"id": room_asset_id, "entityType": "ASSET"},
                    "to": {"id": device_id, "entityType": "DEVICE"},
                    "type": "Contains",
                }
                requests.post(
                    f"{args.url.rstrip('/')}/api/relation",
                    json=rel_payload,
                    headers=headers,
                    timeout=30,
                ).raise_for_status()

            print("Devices created/updated and linked to rooms.")
            print("Done.")
            return 0

        except ApiException as e:
            print("ThingsBoard API error:", e, file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
