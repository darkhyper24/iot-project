import json
import logging
from typing import Any, Callable

from simulator.engine.dedup import LRUDedup
from simulator.models.room import Room

logger = logging.getLogger(__name__)


class CommandHandler:
    """Routes, validates, and deduplicates MQTT commands to room targets.

    Topic tree (spec canonical):
        campus/b01/cmd                     -> building-wide
        campus/b01/f05/cmd                 -> floor-wide
        campus/b01/f05/r502/cmd            -> single room
    """

    def __init__(
        self,
        config: dict,
        rooms: list[Room],
        rooms_by_id: dict[str, Room],
        db: Any,
        get_time_fn: Callable[[], int],
        publish_response: Callable[[str, dict], None] | None = None,
        dedup: LRUDedup | None = None,
    ):
        self.config = config
        self.rooms = rooms
        self._rooms_by_id = rooms_by_id
        self.db = db
        self._get_time = get_time_fn
        self._publish_response = publish_response
        self.dedup = dedup or LRUDedup()

    async def on_message(self, client, topic, payload, qos, properties):
        del client, qos, properties

        try:
            command = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            logger.warning("Rejected malformed command payload on topic %s", topic)
            return

        command_id = command.get("command_id") if isinstance(command, dict) else None
        if command_id and self.dedup.seen(command_id, topic):
            logger.info(
                "dup_drop %s",
                json.dumps({"command_id": command_id, "topic": topic, "event": "duplicate_command"}),
            )
            return

        targets = self.resolve_targets(topic)
        if not targets:
            logger.warning("No simulator targets matched command topic %s", topic)
            return

        if not self.is_valid_command(command):
            logger.warning("Rejected invalid command payload on topic %s: %s", topic, command)
            return

        changed = 0
        for room in targets:
            if room.apply_command(command):
                changed += 1
            room.last_update = self._get_time()

        try:
            if len(targets) == 1:
                await self.db.save_room(targets[0])
            else:
                await self.db.save_states(targets)
        except Exception:
            logger.exception("Failed to persist command save point for topic %s", topic)
            return

        logger.info(
            "Applied command to %d room(s) (%d state changes) from topic %s",
            len(targets),
            changed,
            topic,
        )

        if self._publish_response and len(targets) == 1:
            room = targets[0]
            response = {
                "command_id": command_id,
                "status": "ok",
                "applied_at": self._get_time(),
                "room_id": room.id,
                "new_state": {
                    "hvac_mode": room.hvac_mode,
                    "target_temp": room.target_temp,
                    "lighting_dimmer": room.lighting_dimmer,
                },
                "state_changed": changed > 0,
            }
            try:
                self._publish_response(room.topic("response"), response)
            except Exception:
                logger.exception("Failed to publish command response for %s", room.id)

    def return_response(self, room: Room, command_id: str | None, status: str = "ok", extra: dict | None = None) -> None:
        if not self._publish_response:
            return
        payload = {
            "command_id": command_id,
            "status": status,
            "applied_at": self._get_time(),
            "room_id": room.id,
            "new_state": {
                "hvac_mode": room.hvac_mode,
                "target_temp": room.target_temp,
                "lighting_dimmer": room.lighting_dimmer,
            },
        }
        if extra:
            payload.update(extra)
        self._publish_response(room.topic("response"), payload)

    def resolve_targets(self, topic: str) -> list[Room]:
        parts = topic.split("/")
        if len(parts) < 3 or parts[-1] != "cmd":
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
        if not isinstance(command, dict):
            return False

        allowed_keys = {"hvac_mode", "target_temp", "lighting_dimmer"}
        action_map = {
            "set_hvac": "hvac_mode",
            "set_target_temp": "target_temp",
            "set_dimmer": "lighting_dimmer",
        }

        # Translate action/value form to keyed form for validation.
        check = dict(command)
        if "action" in check and check["action"] in action_map and "value" in check:
            check[action_map[check["action"]]] = check["value"]

        if not (allowed_keys & set(check)):
            return False

        if "hvac_mode" in check and check["hvac_mode"] not in {"ON", "OFF", "ECO"}:
            return False
        if "target_temp" in check:
            try:
                target_temp = float(check["target_temp"])
            except (TypeError, ValueError):
                return False
            if not 15.0 <= target_temp <= 50.0:
                return False
        if "lighting_dimmer" in check:
            try:
                lighting_dimmer = int(check["lighting_dimmer"])
            except (TypeError, ValueError):
                return False
            if not 0 <= lighting_dimmer <= 100:
                return False

        return True
