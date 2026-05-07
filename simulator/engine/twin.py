"""Phase 3 — central twin reporter, desired-state subscriber, and broad-cmd fanout.

One MQTT client per role (not per room) so all 200 rooms (MQTT + CoAP) are covered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Iterable

from gmqtt import Client as MQTTClient
from gmqtt.client import Message as MQTTWillMessage
from gmqtt.mqtt.constants import MQTTv311, MQTTv50

from simulator import addressing
from simulator.engine.commands import CommandHandler
from simulator.models.room import Room
from simulator.mqtt_tls import ssl_context_from_config
from simulator.system_clients import load_system_clients

logger = logging.getLogger(__name__)


def _proto(config: dict):
    v = str(config["mqtt"].get("protocol_version", "5"))
    return MQTTv311 if v.startswith("3") else MQTTv50


async def _connect(client: MQTTClient, config: dict) -> None:
    host = config["mqtt"]["broker_host"]
    port = int(config["mqtt"]["broker_port"])
    ssl_ctx = ssl_context_from_config(config)
    ssl_arg = ssl_ctx if ssl_ctx is not None else False
    proto = _proto(config)
    for attempt in range(1, 11):
        try:
            await client.connect(host, port, ssl=ssl_arg, version=proto)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("system client connect attempt %d/10 failed: %s", attempt, e)
            if attempt == 10:
                raise
            await asyncio.sleep(2)


def _attrs_topic_reported(config: dict, room: Room) -> str:
    base = addressing.mqtt_topic_base(config, room.floor_number, room.room_number)
    return f"{base}/attrs/reported"


def _attrs_topic_desired_pattern(config: dict) -> str:
    return f"{addressing.campus_prefix(config)}/{addressing.building_slug(config)}/+/+/attrs/desired"


class TwinReporter:
    """Publishes client attributes for all 200 rooms via the simulator-reporter cred."""

    def __init__(self, config: dict, rooms: Iterable[Room]):
        self.config = config
        self.rooms = list(rooms)
        self._client: MQTTClient | None = None
        self._sys_creds = load_system_clients(config)
        # 15 s default; each room gets its own due time to avoid a 200-message burst.
        ph3 = config.get("phase3", {})
        self.last_seen_interval = float(ph3.get("reporter_last_seen_interval", 15.0))

    async def start(self) -> None:
        cred = self._sys_creds.get("reporter")
        if not cred:
            logger.warning("Twin reporter disabled (no simulator-reporter credentials).")
            return
        self._client = MQTTClient(client_id="sim-reporter")
        self._client.set_auth_credentials(cred["username"], cred["password"])
        await _connect(self._client, self.config)
        logger.info("Twin reporter connected (200 rooms covered).")

    def publish_reported(self, room: Room, timestamp: int, force: bool = False) -> None:
        if self._client is None:
            return
        snap = room.reported_attrs(timestamp)
        # Skip if unchanged (excluding last_seen) unless forced.
        actuator_keys = ("hvac_mode_reported", "lighting_dimmer_reported", "target_temp_reported", "current_version")
        if not force and room._last_reported is not None:
            if all(snap[k] == room._last_reported.get(k) for k in actuator_keys):
                # Only last_seen changed; still publish a lightweight last_seen-only payload.
                payload = {"last_seen": snap["last_seen"]}
                self._client.publish(_attrs_topic_reported(self.config, room), json.dumps(payload), qos=0)
                return
        self._client.publish(_attrs_topic_reported(self.config, room), json.dumps(snap), qos=0)
        room._last_reported = snap

    async def periodic_loop(self) -> None:
        if self._client is None:
            return
        next_due = {
            room.id: time.monotonic() + random.uniform(0, self.last_seen_interval)
            for room in self.rooms
        }
        while True:
            now_monotonic = time.monotonic()
            now_epoch = int(time.time())
            for room in self.rooms:
                if now_monotonic >= next_due[room.id]:
                    self.publish_reported(room, now_epoch, force=False)
                    next_due[room.id] = now_monotonic + self.last_seen_interval
            await asyncio.sleep(0.25)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("Twin reporter disconnect error")


class DesiredStateSubscriber:
    """Subscribes to ``campus/b01/+/+/attrs/desired`` and applies to in-memory Rooms."""

    def __init__(self, config: dict, rooms: Iterable[Room]):
        self.config = config
        self._rooms_by_id: dict[str, Room] = {room.id: room for room in rooms}
        self._client: MQTTClient | None = None
        self._sys_creds = load_system_clients(config)

    async def start(self) -> None:
        cred = self._sys_creds.get("reporter")  # reuse reporter cred (has subscribe perm)
        if not cred:
            logger.warning("Desired-state subscriber disabled (no credentials).")
            return
        client = MQTTClient(client_id="sim-desired")
        client.set_auth_credentials(cred["username"], cred["password"])
        client.on_message = self._on_message
        await _connect(client, self.config)
        topic = _attrs_topic_desired_pattern(self.config)
        client.subscribe(topic, qos=1)
        self._client = client
        logger.info("Desired-state subscriber listening on %s", topic)

    async def _on_message(self, client, topic, payload, qos, properties):  # noqa: ARG002
        try:
            data = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            logger.warning("Malformed desired-state payload on %s", topic)
            return
        # Topic shape: campus/b01/f##/r###/attrs/desired
        parts = topic.split("/")
        if len(parts) < 6:
            return
        try:
            building = parts[1]
            floor = int(parts[2][1:])
            room_num = int(parts[3][1:])
        except (ValueError, IndexError):
            return
        room_id = f"{building}-f{floor:02d}-r{room_num:03d}"
        room = self._rooms_by_id.get(room_id)
        if room is None:
            logger.debug("Desired update for unknown room %s", room_id)
            return
        room.desired_state = data
        logger.info("Queued desired-state for %s: %s", room_id, list(data.keys()))

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("Desired subscriber disconnect error")


class BroadCommandFanout:
    """Subscribes to broad cmd topics and applies via the existing CommandHandler.

    Single simulator-side subscriber covers both MQTT and CoAP rooms (in-process
    sim). Plan A.4 architecture documented in docs/twin_contract.md.
    """

    def __init__(self, config: dict, cmd_handler: CommandHandler):
        self.config = config
        self.cmd_handler = cmd_handler
        self._client: MQTTClient | None = None
        self._sys_creds = load_system_clients(config)

    async def start(self) -> None:
        cred = self._sys_creds.get("fanout")
        if not cred:
            logger.warning("Broad-command fanout disabled (no credentials).")
            return
        client = MQTTClient(client_id="sim-cmd-fanout")
        client.set_auth_credentials(cred["username"], cred["password"])
        client.on_message = self.cmd_handler.on_message
        await _connect(client, self.config)
        broad_topics = [
            f"{addressing.campus_prefix(self.config)}/{addressing.building_slug(self.config)}/cmd",
            f"{addressing.campus_prefix(self.config)}/{addressing.building_slug(self.config)}/+/cmd",
        ]
        for t in broad_topics:
            client.subscribe(t, qos=2)
        self._client = client
        logger.info("Broad-command fanout subscribed: %s", broad_topics)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("Fanout disconnect error")
