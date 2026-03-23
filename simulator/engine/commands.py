import json
import logging
from typing import Any, Callable

from simulator.models.room import Room

logger = logging.getLogger(__name__)


class CommandHandler:
    """Routes and validates MQTT commands to room targets."""

    def __init__(
        self,
        config: dict,
        rooms: list[Room],
        rooms_by_id: dict[str, Room],
        db: Any,
        get_time_fn: Callable[[], int],
    ):
        self.config = config
        self.rooms = rooms
        self._rooms_by_id = rooms_by_id
        self.db = db
        self._get_time = get_time_fn

    async def on_message(self, client, topic, payload, qos, properties):
        del client, qos, properties

        try:
            command = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            logger.warning("Rejected malformed command payload on topic %s", topic)
            return

        targets = self.resolve_targets(topic)
        if not targets:
            logger.warning("No simulator targets matched command topic %s", topic)
            return

        if not self.is_valid_command(command):
            logger.warning("Rejected invalid command payload on topic %s: %s", topic, command)
            return

        for room in targets:
            room.apply_command(command)
            room.last_update = self._get_time()

        try:
            if len(targets) == 1:
                await self.db.save_room(targets[0])
            else:
                await self.db.save_states(targets)
        except Exception:
            logger.exception("Failed to persist command save point for topic %s", topic)
            return

        logger.info("Applied command to %d room(s) from topic %s", len(targets), topic)

    def resolve_targets(self, topic: str) -> list[Room]:
        parts = topic.split("/")
        if len(parts) < 3 or parts[-1] != "command":
            return []

        prefix = self.config["mqtt"]["topic_prefix"]
        building_slug = self.rooms[0].mqtt_building if self.rooms else ""
        if parts[0] != prefix or parts[1] != building_slug:
            return []

        if len(parts) == 3:
            return list(self.rooms)

        if len(parts) == 4:
            floor_slug = parts[2]
            return [room for room in self.rooms if room.mqtt_floor == floor_slug]

        if len(parts) == 5:
            room_key = f"{parts[2]}/{parts[3]}"
            return [room for room in self.rooms if f"{room.mqtt_floor}/{room.mqtt_room}" == room_key]

        return []

    @staticmethod
    def is_valid_command(command: dict) -> bool:
        allowed_keys = {"hvac_mode", "target_temp", "lighting_dimmer"}
        if not isinstance(command, dict) or not (allowed_keys & set(command)):
            return False

        if "hvac_mode" in command and command["hvac_mode"] not in {"ON", "OFF", "ECO"}:
            return False
        if "target_temp" in command:
            try:
                target_temp = float(command["target_temp"])
            except (TypeError, ValueError):
                return False
            if not 15.0 <= target_temp <= 50.0:
                return False
        if "lighting_dimmer" in command:
            try:
                lighting_dimmer = int(command["lighting_dimmer"])
            except (TypeError, ValueError):
                return False
            if not 0 <= lighting_dimmer <= 100:
                return False

        return True
