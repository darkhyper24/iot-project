"""Publish reported client attributes for all simulator rooms."""

from __future__ import annotations

import json
import logging
import random
import time
import asyncio
from collections.abc import Iterable

from gmqtt import Client as MQTTClient

from simulator.config.system_clients import load_system_clients
from simulator.domain.room import Room
from simulator.networking.mqtt import connect_mqtt_client
from simulator.routing import addressing

logger = logging.getLogger(__name__)


def _attrs_topic_reported(config: dict, room: Room) -> str:
    base = addressing.mqtt_topic_base(config, room.floor_number, room.room_number)
    return f"{base}/attrs/reported"


class TwinReporter:
    """Publishes client attributes for all 200 rooms via the simulator-reporter cred."""

    def __init__(self, config: dict, rooms: Iterable[Room]):
        self.config = config
        self.rooms = list(rooms)
        self._client: MQTTClient | None = None
        self._sys_creds = load_system_clients(config)
        self._last_reported_by_room: dict[str, dict] = {}
        ph3 = config.get("phase3", {})
        self.last_seen_interval = float(ph3.get("reporter_last_seen_interval", 15.0))

    async def start(self) -> None:
        cred = self._sys_creds.get("reporter")
        if not cred:
            logger.warning("Twin reporter disabled (no simulator-reporter credentials).")
            return
        self._client = MQTTClient(client_id="sim-reporter")
        self._client.set_auth_credentials(cred["username"], cred["password"])
        await connect_mqtt_client(self._client, self.config, "Twin reporter")
        logger.info("Twin reporter connected (200 rooms covered).")

    def publish_reported(self, room: Room, timestamp: int, force: bool = False) -> None:
        if self._client is None:
            return
        snap = room.reported_attrs(timestamp)
        actuator_keys = ("hvac_mode_reported", "lighting_dimmer_reported", "target_temp_reported", "current_version")
        last_reported = self._last_reported_by_room.get(room.id)
        if not force and last_reported is not None:
            if all(snap[k] == last_reported.get(k) for k in actuator_keys):
                payload = {"last_seen": snap["last_seen"]}
                self._client.publish(_attrs_topic_reported(self.config, room), json.dumps(payload), qos=0)
                return
        self._client.publish(_attrs_topic_reported(self.config, room), json.dumps(snap), qos=0)
        self._last_reported_by_room[room.id] = snap

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
