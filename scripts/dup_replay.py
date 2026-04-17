#!/usr/bin/env python3
"""Publish the same command_id twice to a room cmd topic; expect a single actuation (dedup).

Subscribe to .../response and count matching command_id replies (expect 1).

Requires simulator + HiveMQ + MQTT_PASSWORD_SECRET matching gen_credentials (room user or admin
for publish; use a room that accepts commands — here we use admin credentials for publish).

Example:
  ADMIN_MQTT_USER=admin ADMIN_MQTT_PASSWORD=admin-secret \\
  MQTT_PASSWORD_SECRET=... python scripts/dup_replay.py --room b01-f05-r512
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import uuid
from pathlib import Path

import yaml
from gmqtt import Client as MQTTClient

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "config.yaml").read_text())


def build_ssl() -> ssl.SSLContext:
    cfg = load_config()
    security = cfg.get("security", {})
    ca = str(ROOT / "config" / "certs" / "ca.crt")
    ctx = ssl.create_default_context(cafile=ca if os.path.exists(ca) else None)
    if security.get("mqtt_insecure_skip_verify", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config()
    prefix = cfg["mqtt"]["topic_prefix"]
    building = cfg["building"]["id"]
    host = os.environ.get("MQTT_BROKER_HOST", cfg["mqtt"]["broker_host"])
    port = int(os.environ.get("MQTT_BROKER_PORT", str(cfg["mqtt"]["broker_port"])))

    parts = args.room.split("-")
    if len(parts) != 3:
        print("room must be like b01-f05-r512")
        return 1
    f = parts[1]
    r = parts[2]
    topic_cmd = f"{prefix}/{building}/{f}/{r}/cmd"
    topic_resp = f"{prefix}/{building}/{f}/{r}/response"

    cmd_id = f"dup-test-{uuid.uuid4().hex}"
    cmd = json.dumps({"command_id": cmd_id, "action": "set_hvac", "value": "ON"})

    responses: list[str] = []

    client = MQTTClient(f"dup-replay-{uuid.uuid4().hex[:8]}")

    def on_message(c, topic, payload, qos, props):
        del c, qos, props
        try:
            body = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            return
        if body.get("command_id") == cmd_id:
            responses.append(topic)

    client.on_message = on_message
    user = os.environ.get("ADMIN_MQTT_USER", "admin")
    password = os.environ.get("ADMIN_MQTT_PASSWORD", "admin-secret")
    client.set_auth_credentials(user, password)

    ssl_ctx = build_ssl()
    await client.connect(host, port, ssl=ssl_ctx, keepalive=30)
    client.subscribe(topic_resp, qos=2)

    client.publish(topic_cmd, cmd, qos=2)
    await asyncio.sleep(0.15)
    client.publish(topic_cmd, cmd, qos=2)

    await asyncio.sleep(2.0)
    await client.disconnect()

    print(f"command_id={cmd_id} response_count={len(responses)} (expected 1)")
    return 0 if len(responses) == 1 else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--room", default="b01-f05-r512", help="Target room id slug")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
