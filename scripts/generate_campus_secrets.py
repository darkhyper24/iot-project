#!/usr/bin/env python3
"""Generate per-node MQTT passwords + HiveMQ file-rbac credentials.xml + CoAP PSK map.

Run from repo root: python scripts/generate_campus_secrets.py

Outputs (default paths):
  config/secrets/mqtt_nodes.json
  config/secrets/coap_psk.json
  config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml

Usernames must not contain MQTT wildcards # or + (HiveMQ File RBAC rule).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Match simulator/routing/addressing split: rooms 1–10 per floor MQTT, 11–20 CoAP
DEFAULT_FLOORS = 10
DEFAULT_ROOMS_PER_FLOOR = 20
DEFAULT_MQTT_PER_FLOOR = 10


def _mqtt_username(room_number: int) -> str:
    return f"m{room_number}"


def _coap_identity(room_number: int) -> str:
    return f"c{room_number}"


def _topic_prefix(floor: int, room_number: int) -> str:
    return f"campus/b01/f{floor:02d}/r{room_number:03d}"


def build_mqtt_users(
    floors: int,
    rooms_per_floor: int,
    mqtt_per_floor: int,
) -> list[dict]:
    mqtt_nodes: list[dict] = []

    for floor in range(1, floors + 1):
        for r_on_floor in range(1, mqtt_per_floor + 1):
            room_number = floor * 100 + r_on_floor
            room_id = f"b01-f{floor:02d}-r{room_number:03d}"
            user = _mqtt_username(room_number)
            password = secrets.token_urlsafe(24)
            role_id = f"role_m{room_number}"
            topic = f"{_topic_prefix(floor, room_number)}/#"
            mqtt_nodes.append(
                {
                    "room_id": room_id,
                    "floor": floor,
                    "room_number": room_number,
                    "username": user,
                    "password": password,
                    "role_id": role_id,
                    "topic_filter": topic,
                }
            )

    return mqtt_nodes


def build_coap_psk(
    floors: int,
    rooms_per_floor: int,
    mqtt_per_floor: int,
) -> list[dict]:
    out: list[dict] = []
    for floor in range(1, floors + 1):
        for r_on_floor in range(mqtt_per_floor + 1, rooms_per_floor + 1):
            room_number = floor * 100 + r_on_floor
            room_id = f"b01-f{floor:02d}-r{room_number:03d}"
            identity = _coap_identity(room_number)
            key = secrets.token_bytes(32)
            out.append(
                {
                    "room_id": room_id,
                    "floor": floor,
                    "room_number": room_number,
                    "identity": identity,
                    "key_hex": key.hex(),
                }
            )
    return out


def write_file_rbac_credentials(
    path: Path,
    mqtt_nodes: list[dict],
) -> tuple[str, str, dict[str, str]]:
    """Emit HiveMQ file-rbac credentials.xml (PLAIN passwords).

    Returns (observer_user, observer_password, system_creds) where system_creds maps
    role-name -> password for the Phase 3 simulator-side clients (fanout / reporter / ota).
    """
    root = ET.Element("file-rbac")

    users_el = ET.SubElement(root, "users")
    for node in mqtt_nodes:
        u = ET.SubElement(users_el, "user")
        ET.SubElement(u, "name").text = node["username"]
        ET.SubElement(u, "password").text = node["password"]
        roles = ET.SubElement(u, "roles")
        ET.SubElement(roles, "id").text = node["role_id"]

    # Optional broad read-only style user for host testing (mosquitto wildcards); not used by the simulator.
    obs_user = "campus_observer"
    obs_pw = secrets.token_urlsafe(24)
    obs_role = "role_campus_observer"
    u_obs = ET.SubElement(users_el, "user")
    ET.SubElement(u_obs, "name").text = obs_user
    ET.SubElement(u_obs, "password").text = obs_pw
    roles_obs = ET.SubElement(u_obs, "roles")
    ET.SubElement(roles_obs, "id").text = obs_role

    # Phase 3 system clients (Plan B.2 / B.5 / A.4).
    system_creds: dict[str, str] = {}
    system_users = [
        # name           role_id                 system_creds key
        ("simulator_fanout",   "role_simulator_fanout",   "fanout"),
        ("simulator_reporter", "role_simulator_reporter", "reporter"),
        ("simulator_ota",      "role_simulator_ota",      "ota"),
        ("ota_publisher",      "role_ota_publisher",      "ota_publisher"),
    ]
    for uname, rid, key in system_users:
        pw = secrets.token_urlsafe(24)
        system_creds[key] = pw
        u = ET.SubElement(users_el, "user")
        ET.SubElement(u, "name").text = uname
        ET.SubElement(u, "password").text = pw
        roles_sys = ET.SubElement(u, "roles")
        ET.SubElement(roles_sys, "id").text = rid
        # remember username alongside password for downstream consumers
        system_creds[f"{key}_user"] = uname

    roles_el = ET.SubElement(root, "roles")
    fleet_topic = "campus/b01/fleet_monitoring/heartbeat"
    for node in mqtt_nodes:
        role = ET.SubElement(roles_el, "role")
        ET.SubElement(role, "id").text = node["role_id"]
        perms = ET.SubElement(role, "permissions")

        p1 = ET.SubElement(perms, "permission")
        ET.SubElement(p1, "topic").text = node["topic_filter"]

        p2 = ET.SubElement(perms, "permission")
        ET.SubElement(p2, "topic").text = fleet_topic
        ET.SubElement(p2, "activity").text = "PUBLISH"

    obs_role_el = ET.SubElement(roles_el, "role")
    ET.SubElement(obs_role_el, "id").text = obs_role
    obs_perms = ET.SubElement(obs_role_el, "permissions")
    p_obs = ET.SubElement(obs_perms, "permission")
    ET.SubElement(p_obs, "topic").text = "campus/b01/#"

    # Fanout: SUBSCRIBE on broad cmd topics; PUBLISH on per-room cmd topics.
    fanout_role = ET.SubElement(roles_el, "role")
    ET.SubElement(fanout_role, "id").text = "role_simulator_fanout"
    fanout_perms = ET.SubElement(fanout_role, "permissions")
    for topic, activity in (
        ("campus/b01/cmd",       "SUBSCRIBE"),
        ("campus/b01/+/cmd",     "SUBSCRIBE"),
        ("campus/b01/+/+/cmd",   "PUBLISH"),
    ):
        perm = ET.SubElement(fanout_perms, "permission")
        ET.SubElement(perm, "topic").text = topic
        ET.SubElement(perm, "activity").text = activity

    # Reporter: PUBLISH on attrs/reported across all rooms.
    rep_role = ET.SubElement(roles_el, "role")
    ET.SubElement(rep_role, "id").text = "role_simulator_reporter"
    rep_perms = ET.SubElement(rep_role, "permissions")
    for topic, activity in (
        ("campus/b01/+/+/attrs/reported", "PUBLISH"),
        ("campus/b01/+/+/attrs/desired",  "SUBSCRIBE"),
    ):
        perm = ET.SubElement(rep_perms, "permission")
        ET.SubElement(perm, "topic").text = topic
        ET.SubElement(perm, "activity").text = activity

    # OTA: SUBSCRIBE on OTA tree, PUBLISH on tamper.
    ota_role = ET.SubElement(roles_el, "role")
    ET.SubElement(ota_role, "id").text = "role_simulator_ota"
    ota_perms = ET.SubElement(ota_role, "permissions")
    for topic, activity in (
        ("campus/b01/ota/config",         "SUBSCRIBE"),
        ("campus/b01/+/ota/config",       "SUBSCRIBE"),
        ("campus/b01/+/+/ota/config",     "SUBSCRIBE"),
        ("campus/b01/security/tamper",    "PUBLISH"),
    ):
        perm = ET.SubElement(ota_perms, "permission")
        ET.SubElement(perm, "topic").text = topic
        ET.SubElement(perm, "activity").text = activity

    # OTA publisher tool credential: PUBLISH on the OTA tree.
    pub_role = ET.SubElement(roles_el, "role")
    ET.SubElement(pub_role, "id").text = "role_ota_publisher"
    pub_perms = ET.SubElement(pub_role, "permissions")
    for topic in (
        "campus/b01/ota/config",
        "campus/b01/+/ota/config",
        "campus/b01/+/+/ota/config",
    ):
        perm = ET.SubElement(pub_perms, "permission")
        ET.SubElement(perm, "topic").text = topic
        ET.SubElement(perm, "activity").text = "PUBLISH"

    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return obs_user, obs_pw, system_creds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--floors", type=int, default=DEFAULT_FLOORS)
    ap.add_argument("--rooms-per-floor", type=int, default=DEFAULT_ROOMS_PER_FLOOR)
    ap.add_argument("--mqtt-per-floor", type=int, default=DEFAULT_MQTT_PER_FLOOR)
    ap.add_argument(
        "--mqtt-json",
        type=Path,
        default=Path("config/secrets/mqtt_nodes.json"),
    )
    ap.add_argument(
        "--coap-json",
        type=Path,
        default=Path("config/secrets/coap_psk.json"),
    )
    ap.add_argument(
        "--rbac-xml",
        type=Path,
        default=Path(
            "config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml",
        ),
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    mqtt_path = root / args.mqtt_json
    coap_path = root / args.coap_json
    rbac_path = root / args.rbac_xml

    mqtt_nodes = build_mqtt_users(
        args.floors, args.rooms_per_floor, args.mqtt_per_floor,
    )
    coap = build_coap_psk(args.floors, args.rooms_per_floor, args.mqtt_per_floor)

    mqtt_path.parent.mkdir(parents=True, exist_ok=True)
    mqtt_json_nodes = [
        {k: v for k, v in n.items() if k != "topic_filter"}
        for n in mqtt_nodes
    ]
    with open(mqtt_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": mqtt_json_nodes}, f, indent=2)

    coap_export = [
        {
            "room_id": c["room_id"],
            "floor": c["floor"],
            "room_number": c["room_number"],
            "identity": c["identity"],
            "key_hex": c["key_hex"],
        }
        for c in coap
    ]
    with open(coap_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": coap_export}, f, indent=2)

    obs_user, obs_pw, system_creds = write_file_rbac_credentials(rbac_path, mqtt_nodes)

    sys_path = mqtt_path.parent / "system_clients.json"
    with open(sys_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "fanout":        {"username": system_creds["fanout_user"],        "password": system_creds["fanout"]},
                "reporter":      {"username": system_creds["reporter_user"],      "password": system_creds["reporter"]},
                "ota":           {"username": system_creds["ota_user"],           "password": system_creds["ota"]},
                "ota_publisher": {"username": system_creds["ota_publisher_user"], "password": system_creds["ota_publisher"]},
            },
            f,
            indent=2,
        )

    print(f"Wrote {len(mqtt_nodes)} MQTT nodes -> {mqtt_path}")
    print(f"Wrote {len(coap_export)} CoAP PSK entries -> {coap_path}")
    print(f"Wrote HiveMQ File RBAC credentials -> {rbac_path}")
    print(f"Wrote Phase 3 simulator system clients (fanout/reporter/ota/ota_publisher) -> {sys_path}")
    print(
        f"Broad-subscribe test user (not for simulator): {obs_user} / {obs_pw} "
        f"(topic campus/b01/#; use for mosquitto wildcards / manual ACL checks)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
