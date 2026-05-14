"""Subscribe to desired-state updates and queue them on in-memory rooms."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from gmqtt import Client as MQTTClient

from simulator.config.system_clients import load_system_clients
from simulator.domain.room import Room
from simulator.networking.mqtt import connect_mqtt_client
from simulator.routing import addressing

logger = logging.getLogger(__name__)


def _attrs_topic_desired_pattern(config: dict) -> str:
    return f"{addressing.campus_prefix(config)}/{addressing.building_slug(config)}/+/+/attrs/desired"


class DesiredStateSubscriber:
    """Subscribes to ``campus/b01/+/+/attrs/desired`` and applies to in-memory Rooms."""

    def __init__(self, config: dict, rooms: Iterable[Room]):
        self.config = config
        self._rooms_by_id: dict[str, Room] = {room.id: room for room in rooms}
        self._client: MQTTClient | None = None
        self._sys_creds = load_system_clients(config)

    async def start(self) -> None:
        cred = self._sys_creds.get("reporter")
        if not cred:
            logger.warning("Desired-state subscriber disabled (no credentials).")
            return
        client = MQTTClient(client_id="sim-desired")
        client.set_auth_credentials(cred["username"], cred["password"])
        client.on_message = self._on_message
        await connect_mqtt_client(client, self.config, "Desired-state subscriber")
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
