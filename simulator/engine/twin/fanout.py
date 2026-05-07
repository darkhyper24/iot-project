"""Broadcast command fanout for building and floor scopes."""

from __future__ import annotations

import logging

from gmqtt import Client as MQTTClient

from simulator.config.system_clients import load_system_clients
from simulator.engine.commands import CommandHandler
from simulator.networking.mqtt import connect_mqtt_client
from simulator.routing import addressing

logger = logging.getLogger(__name__)


class BroadCommandFanout:
    """Subscribes to broad cmd topics and applies via the existing CommandHandler."""

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
        await connect_mqtt_client(client, self.config, "Broad-command fanout")
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
