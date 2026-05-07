"""Shared gmqtt connection helpers."""

from __future__ import annotations

import asyncio
import logging

from gmqtt import Client as MQTTClient
from gmqtt.mqtt.constants import MQTTv311, MQTTv50

from simulator.networking.tls import ssl_context_from_config

logger = logging.getLogger(__name__)


def mqtt_protocol_version(config: dict):
    v = str(config["mqtt"].get("protocol_version", "5"))
    return MQTTv311 if v.startswith("3") else MQTTv50


async def connect_mqtt_client(client: MQTTClient, config: dict, label: str = "MQTT client") -> None:
    host = config["mqtt"]["broker_host"]
    port = int(config["mqtt"]["broker_port"])
    ssl_ctx = ssl_context_from_config(config)
    ssl_arg = ssl_ctx if ssl_ctx is not None else False
    proto = mqtt_protocol_version(config)
    for attempt in range(1, 11):
        try:
            await client.connect(host, port, ssl=ssl_arg, version=proto)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("%s connect attempt %d/10 failed: %s", label, attempt, e)
            if attempt == 10:
                raise
            await asyncio.sleep(2)
