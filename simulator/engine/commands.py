import json
import logging
from typing import Any, Callable

from simulator import addressing
from simulator.models.room import Room

logger = logging.getLogger(__name__)


class CommandHandler:
    """Routes and validates MQTT commands to room targets (Phase 2: .../cmd, cmd_id dedup, QoS2/DUP logs)."""

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
        self._last_cmd_id: dict[str, str | int | float] = {}

    async def on_message(self, client, topic, payload, qos, properties):
        del client

        dup = False
        if isinstance(properties, dict):
            dup = bool(properties.get("dup"))
        if dup:
            logger.info("MQTT command recv dup=1 topic=%s qos=%s", topic, qos)

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

        modified: list[Room] = []
        for room in targets:
            if not room.uses_mqtt:
                continue
            if not self.consume_cmd_id(room, command):
                logger.info("Skipping duplicate cmd_id=%s for room %s", command.get("cmd_id"), room.id)
                continue
            room.apply_command(command)
            room.last_update = self._get_time()
            modified.append(room)

        if not modified:
            return

        try:
            if len(modified) == 1:
                await self.db.save_room(modified[0])
            else:
                await self.db.save_states(modified)
        except Exception:
            logger.exception("Failed to persist command save point for topic %s", topic)
            return

        logger.info("Applied command to %d MQTT room(s) from topic %s", len(modified), topic)

    def consume_cmd_id(self, room: Room, command: dict) -> bool:
        """Return True if command should be applied (idempotent cmd_id dedup)."""
        if "cmd_id" not in command:
            return True
        cid = command["cmd_id"]
        prev = self._last_cmd_id.get(room.id)
        if prev is not None and prev == cid:
            return False
        self._last_cmd_id[room.id] = cid
        return True

    def resolve_targets(self, topic: str) -> list[Room]:
        parts = topic.split("/")
        if len(parts) < 3:
            return []

        suffix = parts[-1]
        if suffix not in ("cmd", "command"):
            return []

        prefix = addressing.campus_prefix(self.config)
        bslug = addressing.building_slug(self.config)
        if parts[0] != prefix or parts[1] != bslug:
            return []

        # campus/b01/cmd — all MQTT rooms
        if len(parts) == 3:
            return [room for room in self.rooms if room.uses_mqtt]

        # campus/b01/f02/cmd
        if len(parts) == 4 and parts[2].startswith("f"):
            floor = int(parts[2][1:])
            return [room for room in self.rooms if room.uses_mqtt and room.floor_number == floor]

        # campus/b01/f02/r201/cmd
        if len(parts) == 5 and parts[2].startswith("f") and parts[3].startswith("r"):
            rn = int(parts[3][1:])
            rid = self._room_id_from_global_room_number(rn)
            room = self._rooms_by_id.get(rid)
            if room and room.uses_mqtt:
                return [room]
            return []

        return []

    def _room_id_from_global_room_number(self, room_number: int) -> str:
        for room in self.rooms:
            if room.room_number == room_number:
                return room.id
        return ""

    @staticmethod
    def is_valid_command(command: dict) -> bool:
        allowed_keys = {"hvac_mode", "target_temp", "lighting_dimmer", "cmd_id"}
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
