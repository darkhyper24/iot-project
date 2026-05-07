"""aiocoap site for Observe telemetry, CON PUT HVAC, Sentinel Observe, and DTLS."""

from __future__ import annotations

import logging
import socket


def _outbound_ip() -> str:
    """Return the container's actual network IP. tinydtls rejects 0.0.0.0."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.254.254.254", 1))
        return s.getsockname()[0]


from aiocoap import resource
from aiocoap.credentials import CredentialsMap, DTLS
from aiocoap.protocol import Context

from simulator.config.psk import load_coap_psk_map
from simulator.domain.room import Room
from simulator.engine.commands import CommandHandler
from simulator.networking.coap.resources import HvacPutResource, SentinelResource, TelemetryResource
from simulator.routing import addressing

logger = logging.getLogger(__name__)


def _dtls_credentials_from_psk_map(psk_map: dict[str, tuple[bytes, bytes]]) -> CredentialsMap:
    m = CredentialsMap()
    for idx, (_rid, (ident, psk)) in enumerate(psk_map.items()):
        m[f":coappsk{idx}"] = DTLS(psk=psk, client_identity=ident)
    return m


class CampusCoAPSite:
    """Holds resources and CoAP context for CoAP-designated rooms."""

    def __init__(self, config: dict, rooms: list[Room], cmd_handler: CommandHandler):
        self.config = config
        self.rooms = [r for r in rooms if r.uses_coap]
        self._cmd_handler = cmd_handler
        self._telemetry: dict[str, TelemetryResource] = {}
        self._sentinel: dict[str, SentinelResource] = {}
        self._context: Context | None = None
        self._dtls_context: Context | None = None
        self.root = resource.Site()

    def build(self) -> resource.Site:
        site = resource.Site()
        for room in self.rooms:
            t_path = addressing.coap_path_segments_telemetry(room.floor_number, room.room_number)
            h_path = addressing.coap_path_segments_hvac(room.floor_number, room.room_number)
            s_path = addressing.coap_path_segments_sentinel(room.floor_number, room.room_number)

            t_res = TelemetryResource(room)
            self._telemetry[room.id] = t_res
            site.add_resource(t_path, t_res)

            site.add_resource(h_path, HvacPutResource(room, self._cmd_handler))

            s_res = SentinelResource(room)
            self._sentinel[room.id] = s_res
            site.add_resource(s_path, s_res)

        self.root = site
        return site

    async def start(self) -> Context:
        coap_cfg = self.config.get("phase2", {}).get("coap") or {}
        host = coap_cfg.get("bind_host", "0.0.0.0")
        plain_port = int(coap_cfg.get("bind_port", 5683))
        dtls = bool(coap_cfg.get("dtls_enabled"))
        _cfg_dtls_host = coap_cfg.get("dtls_bind_host", "")
        dtls_host = _cfg_dtls_host if _cfg_dtls_host not in ("", "0.0.0.0", "::") else _outbound_ip()
        dtls_port = int(coap_cfg.get("dtls_bind_port", 5684))

        # Always start plain CoAP on 5683 — used by Node-RED gateways inside the Docker network
        self._context = await Context.create_server_context(
            self.root,
            bind=(host, plain_port),
            transports=["udp6"],
        )
        logger.info("CoAP plain server listening on udp %s:%s (Observe + PUT HVAC + Sentinel)", host, plain_port)

        if dtls:
            psk_map = load_coap_psk_map(self.config)
            if not psk_map:
                raise RuntimeError(
                    "CoAP DTLS is enabled but no PSK entries loaded. Run: python scripts/generate_campus_secrets.py",
                )
            cred = _dtls_credentials_from_psk_map(psk_map)
            self._dtls_context = await Context.create_server_context(
                self.root,
                bind=(dtls_host, dtls_port),
                transports=["tinydtls_server"],
                server_credentials=cred,
            )
            logger.info("CoAP DTLS server listening on %s:%s", dtls_host, dtls_port)

        return self._context

    def notify_telemetry(self, room: Room, payload: bytes) -> None:
        res = self._telemetry.get(room.id)
        if res:
            res.set_telemetry(payload)

    def notify_sentinel(self, room: Room, timestamp: int) -> None:
        res = self._sentinel.get(room.id)
        if res:
            res.refresh(timestamp)

    async def shutdown(self) -> None:
        if self._dtls_context is not None:
            await self._dtls_context.shutdown()
            self._dtls_context = None
        if self._context is not None:
            await self._context.shutdown()
            self._context = None
