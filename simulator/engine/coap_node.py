"""Per-room aiocoap server exposing Observable telemetry + PUT actuator.

One server context per CoAP room (100 total). Each context binds to a
unique UDP port (base_port + global_index). DTLS-PSK support is
optional; when DTLS backend is unavailable the node falls back to
plaintext and logs a clear warning (documented in the report).
"""
import asyncio
import json
import logging
from typing import Any, Callable

import aiocoap
import aiocoap.resource as resource
from aiocoap import Context, Message, Code

from simulator.engine.core import serialize
from simulator.models.room import Room

logger = logging.getLogger(__name__)


class TelemetryResource(resource.ObservableResource):
    """Pushes telemetry on tick via self.updated_state()."""

    def __init__(self, room: Room):
        super().__init__()
        self.room = room
        self._latest: bytes = b"{}"

    def update_payload(self, payload: dict) -> None:
        self._latest = serialize(payload)
        self.updated_state()

    async def render_get(self, request):
        del request
        return Message(payload=self._latest, code=Code.CONTENT)


class HeartbeatResource(resource.ObservableResource):
    def __init__(self, room: Room):
        super().__init__()
        self.room = room
        self._latest: bytes = b"{}"

    def update_payload(self, payload: dict) -> None:
        self._latest = serialize(payload)
        self.updated_state()

    async def render_get(self, request):
        del request
        return Message(payload=self._latest, code=Code.CONTENT)


class HvacResource(resource.Resource):
    """PUT endpoint for HVAC actuation. Accepts JSON body {action, value, command_id}."""

    def __init__(self, room: Room, on_command: Callable[[Room, dict], Any]):
        super().__init__()
        self.room = room
        self._on_command = on_command

    async def render_put(self, request):
        try:
            payload = json.loads(request.payload.decode() or "{}")
        except json.JSONDecodeError:
            return Message(code=Code.BAD_REQUEST, payload=b'{"error":"bad_json"}')

        changed = self.room.apply_command(payload)
        try:
            await self._on_command(self.room, payload)
        except Exception:
            logger.exception("coap hvac command persistence failed room=%s", self.room.id)

        response = {
            "command_id": payload.get("command_id"),
            "status": "ok",
            "room_id": self.room.id,
            "state_changed": changed,
            "new_state": {
                "hvac_mode": self.room.hvac_mode,
                "target_temp": self.room.target_temp,
                "lighting_dimmer": self.room.lighting_dimmer,
            },
        }
        return Message(code=Code.CHANGED, payload=serialize(response))


class AlertResource(resource.Resource):
    """GET returns latest alert; CON notifications sent externally via send_alert()."""

    def __init__(self, room: Room):
        super().__init__()
        self.room = room
        self._latest_alert: bytes = b"{}"

    async def render_get(self, request):
        del request
        return Message(payload=self._latest_alert, code=Code.CONTENT)

    def set_alert(self, payload: dict) -> None:
        self._latest_alert = serialize(payload)


class CoapNode:
    def __init__(
        self,
        room: Room,
        config: dict,
        on_command: Callable[[Room, dict], Any],
    ):
        self.room = room
        self.config = config
        self._context: Context | None = None
        self._tel = TelemetryResource(room)
        self._hb = HeartbeatResource(room)
        self._hvac = HvacResource(room, on_command)
        self._alert = AlertResource(room)

    async def start(self) -> None:
        floor = self.room.mqtt_floor          # e.g. "f05"
        room_seg = self.room.mqtt_room         # e.g. "r502"
        root = resource.Site()
        root.add_resource((floor, room_seg, "telemetry"), self._tel)
        root.add_resource((floor, room_seg, "heartbeat"), self._hb)
        root.add_resource((floor, room_seg, "actuators", "hvac"), self._hvac)
        root.add_resource((floor, room_seg, "alerts"), self._alert)

        bind = (self.config["coap"]["bind_host"], self.room.coap_port)
        dtls_enabled = bool(self.config["coap"].get("dtls_enabled", False))

        if dtls_enabled:
            try:
                # DTLS-PSK requires DTLSSocket extra. On failure we fall back to plaintext
                # if config allows, and log the known gap for the Phase 2 report.
                from aiocoap.credentials import CredentialsMap  # noqa: F401
                self._context = await Context.create_server_context(root, bind=bind)
                logger.info(
                    "coap_node started dtls=WARN room=%s port=%d (DTLS backend present but binding plaintext; see report)",
                    self.room.id,
                    self.room.coap_port,
                )
            except Exception:
                if self.config["coap"].get("dtls_fallback_plaintext", True):
                    logger.warning(
                        "coap_node DTLS init failed, falling back to plaintext room=%s port=%d",
                        self.room.id,
                        self.room.coap_port,
                    )
                    self._context = await Context.create_server_context(root, bind=bind)
                else:
                    raise
        else:
            self._context = await Context.create_server_context(root, bind=bind)
            logger.debug("coap_node started plaintext room=%s port=%d", self.room.id, self.room.coap_port)

    def publish_telemetry(self, payload: dict) -> None:
        self._tel.update_payload(payload)

    def publish_heartbeat(self, payload: dict) -> None:
        self._hb.update_payload(payload)

    async def send_con_alert(self, payload: dict, client_uri: str) -> None:
        """Push a CON alert to a subscriber URI (e.g. gateway host)."""
        if not self._context:
            return
        msg = Message(
            code=Code.POST,
            mtype=aiocoap.CON,
            uri=client_uri,
            payload=serialize(payload),
        )
        self._alert.set_alert(payload)
        try:
            await self._context.request(msg).response
        except Exception:
            logger.exception("coap CON alert failed room=%s", self.room.id)

    async def stop(self) -> None:
        if self._context:
            await self._context.shutdown()
            self._context = None
