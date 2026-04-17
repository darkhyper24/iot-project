"""Per-room gmqtt client with unique client_id, LWT, TLS, per-room auth.

One instance per MQTT room (100 total). Subscribes to that room's
.../cmd at QoS 2 and delegates messages into the shared CommandHandler.
"""
import asyncio
import logging
import os
import ssl

from gmqtt import Client as MQTTClient
from gmqtt import Message

from simulator.engine.core import serialize
from simulator.engine.commands import CommandHandler
from simulator.models.room import Room

logger = logging.getLogger(__name__)


class MqttNode:
    def __init__(self, room: Room, config: dict, command_handler: CommandHandler):
        self.room = room
        self.config = config
        self.command_handler = command_handler
        self._client: MQTTClient | None = None
        self._connected = asyncio.Event()

    async def connect(self) -> None:
        security = self.config.get("security", {})
        tls_enabled = bool(security.get("mqtt_tls", True))
        broker_host = self.config["mqtt"]["broker_host"]
        broker_port = (
            self.config["mqtt"]["broker_port"] if tls_enabled
            else self.config["mqtt"].get("plaintext_port", 1883)
        )

        will = Message(
            self.room.topic("status"),
            b"offline",
            qos=self.config["mqtt"].get("status_qos", 1),
            retain=True,
        )
        client = MQTTClient(
            client_id=self.room.id,
            will_message=will,
            clean_session=True,
        )
        master_secret_env = security.get("mqtt_password_secret_env", "MQTT_PASSWORD_SECRET")
        master = os.environ.get(master_secret_env, "dev-secret")
        client.set_auth_credentials(self.room.mqtt_username, self.room.mqtt_password(master))
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        ssl_ctx: ssl.SSLContext | bool = False
        if tls_enabled:
            ca_path = security.get("mqtt_ca_path") or "/certs/ca.crt"
            ssl_ctx = ssl.create_default_context(cafile=ca_path if os.path.exists(ca_path) else None)
            if security.get("mqtt_insecure_skip_verify", True):
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        await client.connect(broker_host, broker_port, ssl=ssl_ctx, keepalive=30)
        self._client = client

    def _on_connect(self, client, flags, rc, properties):
        del flags, rc, properties
        logger.info("mqtt_node connected room=%s", self.room.id)
        # Retained online status
        client.publish(
            self.room.topic("status"),
            b"online",
            qos=self.config["mqtt"].get("status_qos", 1),
            retain=True,
        )
        # Subscribe to room's cmd channel at QoS 2
        client.subscribe(self.room.topic("cmd"), qos=self.config["mqtt"].get("cmd_qos", 2))
        self._connected.set()

    async def _on_message(self, client, topic, payload, qos, properties):
        await self.command_handler.on_message(client, topic, payload, qos, properties)

    def _on_disconnect(self, client, packet, exc=None):
        del client, packet
        logger.warning("mqtt_node disconnected room=%s exc=%s", self.room.id, exc)
        self._connected.clear()

    async def publish_telemetry(self, payload: dict) -> None:
        if not self._client:
            return
        self._client.publish(self.room.topic("telemetry"), serialize(payload), qos=0)

    async def publish_heartbeat(self, payload: dict) -> None:
        if not self._client:
            return
        self._client.publish(
            self.room.topic("heartbeat"),
            serialize(payload),
            qos=self.config["mqtt"].get("heartbeat_qos", 1),
        )

    async def publish_response(self, payload: dict) -> None:
        if not self._client:
            return
        self._client.publish(
            self.room.topic("response"),
            serialize(payload),
            qos=self.config["mqtt"].get("response_qos", 2),
        )

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            # Publish offline explicitly so retained status is correct on graceful shutdown.
            self._client.publish(
                self.room.topic("status"),
                b"offline",
                qos=self.config["mqtt"].get("status_qos", 1),
                retain=True,
            )
            await self._client.disconnect()
        except Exception:
            logger.exception("mqtt_node disconnect error room=%s", self.room.id)
