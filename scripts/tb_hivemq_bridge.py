#!/usr/bin/env python3
"""Bridge HiveMQ campus topics into ThingsBoard CE via tenant REST telemetry API.

ThingsBoard Community Edition has no \"subscribe to external MQTT broker\" integration (PE-only).
This service connects to HiveMQ with the tb-integration user, subscribes to campus traffic,
and POSTs telemetry to each room device using the tenant JWT.

Environment:
  TB_URL, TB_USER, TB_PASS — ThingsBoard tenant login
  MQTT_BROKER_HOST, MQTT_BROKER_PORT — HiveMQ (default hivemq:8883)
  TB_INTEGRATION_USER / TB_INTEGRATION_PASSWORD — HiveMQ credentials (default tb-integration / tb-integration-secret)
  SECURITY_MQTT_CA_PATH — CA for TLS (default /certs/ca.crt)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import time
from typing import Any

import requests
from gmqtt import Client as MQTTClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tb_bridge")

TELEMETRY_KEYS = frozenset(
    {
        "temperature",
        "humidity",
        "occupancy",
        "light_level",
        "hvac_mode",
        "lighting_dimmer",
        "target_temp",
        "fault",
        "protocol",
    }
)


class TBSession:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/")
        self._user = user
        self._password = password
        self.session = requests.Session()
        self._device_ids: dict[str, str] = {}
        self._login()

    def _login(self) -> None:
        r = self.session.post(
            f"{self.base}/api/auth/login",
            json={"username": self._user, "password": self._password},
            timeout=30,
        )
        r.raise_for_status()
        token = r.json()["token"]
        self.session.headers["X-Authorization"] = f"Bearer {token}"

    def refresh_device_cache(self) -> None:
        self._device_ids.clear()
        page = 0
        while True:
            r = self.session.get(
                f"{self.base}/api/tenant/devices",
                params={"pageSize": 100, "page": page, "sortProperty": "name", "sortOrder": "ASC"},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            for d in data.get("data", []):
                name = d.get("name")
                if name:
                    self._device_ids[name] = d["id"]["id"]
            if not data.get("hasNext", False):
                break
            page += 1
        log.info("TB device cache: %d devices", len(self._device_ids))

    def post_telemetry(self, device_name: str, payload: dict[str, Any], topic_suffix: str) -> None:
        did = self._device_ids.get(device_name)
        if not did:
            log.debug("skip unknown device %s", device_name)
            return

        if topic_suffix == "status" and not isinstance(payload, dict):
            payload = {"status": str(payload)}

        if not isinstance(payload, dict):
            payload = {}

        ts = payload.get("timestamp")
        if ts is None:
            ts_ms = int(time.time() * 1000)
        elif isinstance(ts, (int, float)):
            ts_ms = int(ts * 1000) if ts < 1e12 else int(ts)
        else:
            ts_ms = int(time.time() * 1000)

        if topic_suffix == "telemetry":
            values = {k: payload[k] for k in TELEMETRY_KEYS if k in payload}
            if not values and payload:
                values = {k: v for k, v in payload.items() if not k.startswith("_")}
        elif topic_suffix == "heartbeat":
            values = {"heartbeat": json.dumps(payload)}
        elif topic_suffix == "status":
            values = {"connection_status": str(payload.get("status", payload))}
        elif topic_suffix == "response":
            values = {"last_command_response": json.dumps(payload)}
        else:
            values = {"raw": json.dumps(payload)}

        body = {"ts": ts_ms, "values": values}
        url = f"{self.base}/api/plugins/telemetry/DEVICE/{did}/timeseries/TELEMETRY"
        try:
            r = self.session.post(url, json=body, timeout=15)
            if r.status_code == 401:
                self._login()
                r = self.session.post(url, json=body, timeout=15)
            r.raise_for_status()
        except Exception:
            log.exception("telemetry POST failed for %s", device_name)


def _parse_device_name(topic: str) -> tuple[str | None, str | None]:
    """campus/b01/f05/r502/telemetry -> (b01-f05-r502, telemetry)."""
    parts = topic.split("/")
    if len(parts) < 5:
        return None, None
    if parts[0] != "campus":
        return None, None
    suffix = parts[-1]
    device_name = f"{parts[1]}-{parts[2]}-{parts[3]}"
    return device_name, suffix


async def _run() -> None:
    tb = TBSession(
        os.environ.get("TB_URL", "http://thingsboard:9090"),
        os.environ.get("TB_USER", "tenant@thingsboard.org"),
        os.environ.get("TB_PASS", "tenant"),
    )
    tb.refresh_device_cache()

    host = os.environ.get("MQTT_BROKER_HOST", "hivemq")
    port = int(os.environ.get("MQTT_BROKER_PORT", "8883"))
    user = os.environ.get("TB_INTEGRATION_USER", "tb-integration")
    password = os.environ.get("TB_INTEGRATION_PASSWORD", "tb-integration-secret")
    ca = os.environ.get("SECURITY_MQTT_CA_PATH", "/certs/ca.crt")

    ssl_ctx: ssl.SSLContext | bool = False
    if os.environ.get("SECURITY_MQTT_TLS", "true").lower() in ("1", "true", "yes"):
        ssl_ctx = ssl.create_default_context(cafile=ca if os.path.exists(ca) else None)
        if os.environ.get("SECURITY_MQTT_INSECURE_SKIP_VERIFY", "true").lower() in ("1", "true", "yes"):
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    client = MQTTClient("thingsboard-hivemq-bridge")

    def on_connect(client, flags, rc, properties):
        del flags, rc, properties
        client.subscribe("campus/+/+/+/telemetry", qos=0)
        client.subscribe("campus/+/+/+/heartbeat", qos=0)
        client.subscribe("campus/+/+/+/status", qos=0)
        client.subscribe("campus/+/+/+/response", qos=0)
        log.info("Subscribed to campus telemetry topics on HiveMQ")

    def on_message(client, topic, payload, qos, properties):
        del client, qos, properties
        name, suffix = _parse_device_name(topic)
        if not name or not suffix:
            return
        raw = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            if suffix == "status":
                body = {"status": raw}
            else:
                log.warning("non-json payload on %s", topic)
                return
        if not isinstance(body, dict):
            body = {"payload": body}
        tb.post_telemetry(name, body, suffix)

    client.on_connect = on_connect
    client.on_message = on_message
    client.set_auth_credentials(user, password)

    await client.connect(host, port, ssl=ssl_ctx, keepalive=30)
    log.info("HiveMQ bridge connected %s:%s user=%s", host, port, user)

    async def refresh_loop():
        while True:
            await asyncio.sleep(120)
            try:
                await loop.run_in_executor(None, tb.refresh_device_cache)
            except Exception:
                log.exception("device cache refresh")

    refresh_task = asyncio.create_task(refresh_loop())
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        refresh_task.cancel()
        await client.disconnect()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
