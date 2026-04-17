#!/usr/bin/env python3
"""Generate HiveMQ file-RBAC credentials + JSON client-secret lookup for the simulator.

Reads the master secret from $MQTT_PASSWORD_SECRET (or the --master flag) and derives
per-room passwords as HMAC-SHA256(master, username) printed in hex. This matches
Room.mqtt_password() in simulator/models/room.py so the broker + device agree.

Outputs:
  config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml
  config/client_secrets.json              (kept gitignored; used by tools/tests)

Usage:
  MQTT_PASSWORD_SECRET=... python scripts/gen_credentials.py
"""
import argparse
import hmac
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_YAML = ROOT / "config" / "config.yaml"
CREDENTIALS_XML = ROOT / "config" / "hivemq" / "extensions" / "hivemq-file-rbac-extension" / "conf" / "credentials.xml"
SECRETS_JSON = ROOT / "config" / "client_secrets.json"


def derive_password(master: str, username: str) -> str:
    return hmac.new(master.encode(), username.encode(), hashlib.sha256).hexdigest()


def build_xml(users_and_roles: list[tuple[str, str, str]], role_perms: dict[str, list[tuple[str, str]]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<file-rbac xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        '    <users>',
    ]
    for username, password, role in users_and_roles:
        lines.append(
            f'        <user><name>{username}</name><password>{password}</password>'
            f'<roles><id>{role}</id></roles></user>'
        )
    lines.append('    </users>')
    lines.append('    <roles>')
    for role, perms in role_perms.items():
        lines.append(f'        <role><id>{role}</id><permissions>')
        for topic, activity in perms:
            lines.append(
                f'            <permission><topic>{topic}</topic><activity>{activity}</activity></permission>'
            )
        lines.append('        </permissions></role>')
    lines.append('    </roles>')
    lines.append('</file-rbac>')
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", default=os.environ.get("MQTT_PASSWORD_SECRET", "dev-master-secret"))
    parser.add_argument("--admin-password", default=os.environ.get("ADMIN_MQTT_PASSWORD", "admin-secret"))
    parser.add_argument("--integration-password", default="tb-integration-secret")
    parser.add_argument("--bench-password", default="rtt-bench-secret")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG_YAML.read_text())
    building_id = cfg["building"]["id"]
    floors = cfg["building"]["floors"]
    rooms_per_floor = cfg["building"]["rooms_per_floor"]
    coap_rooms = cfg.get("transport", {}).get("coap_rooms_per_floor", 10)
    user_prefix = cfg.get("security", {}).get("mqtt_user_prefix", "room-")

    users: list[tuple[str, str, str]] = []
    secrets: dict[str, str] = {}
    role_perms: dict[str, list[tuple[str, str]]] = {
        "admin-role": [("#", "ALL")],
        "integration-role": [
            ("campus/#", "SUBSCRIBE"),
            (f"campus/{building_id}/+/+/cmd", "PUBLISH"),
            (f"campus/{building_id}/+/cmd", "PUBLISH"),
            (f"campus/{building_id}/cmd", "PUBLISH"),
        ],
        "bench-role": [
            ("campus/#", "SUBSCRIBE"),
            (f"campus/{building_id}/+/+/cmd", "PUBLISH"),
        ],
    }

    users.append(("admin", args.admin_password, "admin-role"))
    users.append(("tb-integration", args.integration_password, "integration-role"))
    users.append(("rtt-bench", args.bench_password, "bench-role"))
    secrets["admin"] = args.admin_password
    secrets["tb-integration"] = args.integration_password
    secrets["rtt-bench"] = args.bench_password

    # Gateway users per floor.
    for f in range(1, floors + 1):
        uname = f"gw-f{f:02d}"
        role = f"{uname}-role"
        password = derive_password(args.master, uname)
        users.append((uname, password, role))
        secrets[uname] = password
        role_perms[role] = [
            (f"campus/{building_id}/f{f:02d}/#", "ALL"),
            (f"campus/{building_id}/f{f:02d}/floor_summary", "PUBLISH"),
        ]

    # Room users. Only MQTT rooms truly need broker creds, but we mint all 200 so
    # a room can flip protocols without regenerating credentials.
    for f in range(1, floors + 1):
        for r in range(1, rooms_per_floor + 1):
            room_id = f"{building_id}-f{f:02d}-r{f * 100 + r:03d}"
            uname = f"{user_prefix}f{f:02d}-r{f * 100 + r:03d}"
            role = f"room-f{f:02d}-role"
            password = derive_password(args.master, uname)
            users.append((uname, password, role))
            secrets[room_id] = password
            if role not in role_perms:
                role_perms[role] = [
                    (f"campus/{building_id}/f{f:02d}/+/telemetry", "PUBLISH"),
                    (f"campus/{building_id}/f{f:02d}/+/heartbeat", "PUBLISH"),
                    (f"campus/{building_id}/f{f:02d}/+/status", "PUBLISH"),
                    (f"campus/{building_id}/f{f:02d}/+/response", "PUBLISH"),
                    (f"campus/{building_id}/f{f:02d}/+/cmd", "SUBSCRIBE"),
                    (f"campus/{building_id}/cmd", "SUBSCRIBE"),
                    (f"campus/{building_id}/f{f:02d}/cmd", "SUBSCRIBE"),
                ]

    CREDENTIALS_XML.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_XML.write_text(build_xml(users, role_perms))
    SECRETS_JSON.write_text(json.dumps(secrets, indent=2))
    print(f"Wrote {len(users)} users to {CREDENTIALS_XML}")
    print(f"Wrote secrets map to {SECRETS_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
