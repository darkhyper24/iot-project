"""aiocoap resources for telemetry observe, actuator PUT, and sentinel observe."""

from __future__ import annotations

import json
import logging

from aiocoap import resource
from aiocoap.message import Message
from aiocoap.numbers.codes import Code
from aiocoap.numbers.contentformat import ContentFormat

from simulator.domain.room import Room
from simulator.engine.commands import CommandHandler

logger = logging.getLogger(__name__)


class TelemetryResource(resource.ObservableResource):
    def __init__(self, room: Room):
        super().__init__()
        self.room = room
        self._payload = b"{}"

    def set_telemetry(self, payload: bytes) -> None:
        self._payload = payload
        self.updated_state()

    async def render_get(self, request):
        return Message(payload=self._payload, content_format=ContentFormat.JSON)


class HvacPutResource(resource.Resource):
    """CON PUT JSON commands (same keys as MQTT cmd)."""

    def __init__(self, room: Room, cmd_handler: CommandHandler):
        super().__init__()
        self.room = room
        self._cmd = cmd_handler

    async def render_put(self, request):
        if not self.room.uses_coap:
            return Message(code=Code.FORBIDDEN, payload=b"not a coap room")
        try:
            text = request.payload.decode("utf-8")
            command = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Message(code=Code.BAD_REQUEST, payload=b"invalid json")

        if not CommandHandler.is_valid_command(command):
            return Message(code=Code.BAD_REQUEST, payload=b"invalid command")

        if not self._cmd.consume_cmd_id(self.room, command):
            return Message(code=Code.CONTENT, payload=json.dumps({"status": "duplicate_cmd_id"}).encode())

        self.room.apply_command(command)
        self.room.last_update = self._cmd._get_time()
        try:
            await self._cmd.db.save_room(self.room)
        except Exception:
            logger.exception("CoAP HVAC persist failed for %s", self.room.id)
            return Message(code=Code.INTERNAL_SERVER_ERROR, payload=b"db error")

        return Message(code=Code.CHANGED, payload=json.dumps({"status": "ok"}).encode())


class SentinelResource(resource.ObservableResource):
    def __init__(self, room: Room):
        super().__init__()
        self.room = room
        self._payload = json.dumps({"active": False}).encode()

    def refresh(self, timestamp: int) -> None:
        active = self.room.active_fault == "sentinel_trip"
        self._payload = json.dumps(
            {
                "active": bool(active),
                "room_id": self.room.id,
                "timestamp": timestamp,
            }
        ).encode()
        self.updated_state()

    async def render_get(self, request):
        return Message(payload=self._payload, content_format=ContentFormat.JSON)
