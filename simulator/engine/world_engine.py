import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from simulator.engine.coap_node import CoapNode
from simulator.engine.commands import CommandHandler
from simulator.engine.core import SimulationCore, TickResult, serialize
from simulator.engine.dedup import LRUDedup
from simulator.engine.mqtt_node import MqttNode
from simulator.models.room import Room

logger = logging.getLogger(__name__)


class WorldEngine:
    """Orchestrator for 100 MQTT nodes + 100 CoAP nodes sharing one core."""

    def __init__(self, config: dict, db: Any, admin_mqtt_client):
        self.config = config
        self.db = db
        self.admin = admin_mqtt_client   # broker-admin client for fleet + broadcast subscribe
        self.rooms: list[Room] = []
        self.mqtt_nodes: dict[str, MqttNode] = {}
        self.coap_nodes: dict[str, CoapNode] = {}
        self._tasks: list[asyncio.Task] = []
        self._last_heartbeats: dict[str, float] = {}
        self._rooms_by_id: dict[str, Room] = {}
        self._sim_real_start = time.perf_counter()
        self._sim_epoch_start = int(time.time())
        self._cmd_handler: CommandHandler | None = None
        self._dedup = LRUDedup()
        self._core = SimulationCore(config, self._simulation_time)

    async def initialize(self) -> None:
        building_id = self.config["building"]["id"]
        floors = self.config["building"]["floors"]
        rooms_per_floor = self.config["building"]["rooms_per_floor"]

        saved_states = await self.db.load_states()
        restored = 0

        for f in range(1, floors + 1):
            for r in range(1, rooms_per_floor + 1):
                # Room constructs id deterministically; derive for lookup.
                room_id = f"{building_id}-f{f:02d}-r{f * 100 + r:03d}"
                state = saved_states.get(room_id)
                room = Room(building_id, f, r, self.config, state=state)
                self.rooms.append(room)
                self._rooms_by_id[room.id] = room
                if state:
                    restored += 1

        now = time.time()
        for room in self.rooms:
            self._last_heartbeats[room.id] = now

        mqtt_count = sum(1 for r in self.rooms if r.protocol == "mqtt")
        coap_count = sum(1 for r in self.rooms if r.protocol == "coap")
        logger.info(
            "Fleet initialized: %d rooms (%d MQTT + %d CoAP, %d restored from DB)",
            len(self.rooms), mqtt_count, coap_count, restored,
        )

        self._write_coap_registry()

    def _write_coap_registry(self) -> None:
        """Emit /app/config/coap_registry.json so gateways can resolve CoAP endpoints."""
        entries = []
        host = self.config["coap"].get("advertise_host", "simulator")
        for room in self.rooms:
            if room.protocol != "coap":
                continue
            entries.append({
                "room_id": room.id,
                "floor": room.mqtt_floor,
                "room": room.mqtt_room,
                "host": host,
                "port": room.coap_port,
                "telemetry_uri": f"coap://{host}:{room.coap_port}/{room.mqtt_floor}/{room.mqtt_room}/telemetry",
                "hvac_uri": f"coap://{host}:{room.coap_port}/{room.mqtt_floor}/{room.mqtt_room}/actuators/hvac",
            })
        out_path = Path(os.environ.get("COAP_REGISTRY_PATH", "config/coap_registry.json"))
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({"rooms": entries}, indent=2))
            logger.info("CoAP registry written: %s (%d rooms)", out_path, len(entries))
        except OSError as e:
            logger.warning("Could not write CoAP registry at %s: %s", out_path, e)

    def setup_command_handler(self) -> None:
        self._cmd_handler = CommandHandler(
            self.config,
            self.rooms,
            self._rooms_by_id,
            self.db,
            self._simulation_time,
            publish_response=self._publish_response_via_mqtt,
            dedup=self._dedup,
        )
        # Admin client owns the wildcard command subscriptions so building/floor broadcasts
        # fan-out through a single on_message handler. Per-room MQTT nodes also subscribe
        # to their own cmd topic; dedup ensures no double-actuation.
        self.admin.on_message = self._cmd_handler.on_message
        prefix = self.config["mqtt"]["topic_prefix"]
        building = self.config["building"]["id"]
        cmd_qos = self.config["mqtt"].get("cmd_qos", 2)
        self.admin.subscribe(f"{prefix}/{building}/cmd", qos=cmd_qos)
        self.admin.subscribe(f"{prefix}/{building}/+/cmd", qos=cmd_qos)
        self.admin.subscribe(f"{prefix}/{building}/+/+/cmd", qos=cmd_qos)
        logger.info("Admin client subscribed to building/floor/room cmd topics at QoS %d", cmd_qos)

    def _publish_response_via_mqtt(self, topic: str, payload: dict) -> None:
        self.admin.publish(topic, serialize(payload), qos=self.config["mqtt"].get("response_qos", 2))

    async def run(self) -> None:
        await self._start_nodes()

        for room in self.rooms:
            self._tasks.append(asyncio.create_task(self._core.run_room(room, self._on_tick)))

        self._tasks.append(asyncio.create_task(self._sync_loop()))
        self._tasks.append(asyncio.create_task(self._fleet_health_loop()))

        logger.info("World engine running: %d room tasks + %d MQTT nodes + %d CoAP nodes",
                    len(self.rooms), len(self.mqtt_nodes), len(self.coap_nodes))
        await asyncio.gather(*self._tasks)

    async def _start_nodes(self) -> None:
        mqtt_connects = []
        for room in self.rooms:
            if room.protocol == "mqtt":
                node = MqttNode(room, self.config, self._cmd_handler)
                self.mqtt_nodes[room.id] = node
                mqtt_connects.append(node.connect())
            else:
                node = CoapNode(room, self.config, self._on_coap_command)
                self.coap_nodes[room.id] = node
                await node.start()

        # Stagger MQTT connects in batches of 20 to avoid thundering herd on HiveMQ.
        batch = 20
        for i in range(0, len(mqtt_connects), batch):
            chunk = mqtt_connects[i:i + batch]
            results = await asyncio.gather(*chunk, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error("mqtt_node connect failed: %s", r)
            await asyncio.sleep(0.25)

        logger.info("Transport nodes started: %d MQTT, %d CoAP",
                    len(self.mqtt_nodes), len(self.coap_nodes))

    async def _on_tick(self, room: Room, result: TickResult) -> None:
        if room.protocol == "mqtt":
            node = self.mqtt_nodes.get(room.id)
            if not node:
                return
            await node.publish_telemetry(result.telemetry)
            if result.heartbeat:
                await node.publish_heartbeat(result.heartbeat)
                self._last_heartbeats[room.id] = time.time()
        else:
            node = self.coap_nodes.get(room.id)
            if not node:
                return
            node.publish_telemetry(result.telemetry)
            if result.heartbeat:
                node.publish_heartbeat(result.heartbeat)
                self._last_heartbeats[room.id] = time.time()

    async def _on_coap_command(self, room: Room, _payload: dict) -> None:
        try:
            await self.db.save_room(room)
        except Exception:
            logger.exception("failed saving room after coap command %s", room.id)

    async def _sync_loop(self) -> None:
        interval = self.config["simulation"]["db_sync_interval"]
        while True:
            await asyncio.sleep(interval)
            try:
                await self.db.save_states(self.rooms)
                logger.info("State synced to PostgreSQL (%d rooms)", len(self.rooms))
            except Exception:
                logger.exception("Failed to sync states to PostgreSQL")

    async def _fleet_health_loop(self) -> None:
        hb_interval = self.config["heartbeat"]["interval"]
        hb_timeout = self.config["heartbeat"]["timeout"]
        fleet_topic = self.config.get("admin", {}).get("fleet_topic", "campus/b01/fleet/health")

        while True:
            await asyncio.sleep(hb_interval)
            now = time.time()
            silent = []

            for room in self.rooms:
                last_seen = self._last_heartbeats.get(room.id, 0)
                if now - last_seen > hb_timeout:
                    silent.append(room.id)
                    logger.warning(
                        "fleet_health_warning %s",
                        json.dumps({
                            "room_id": room.id,
                            "protocol": room.protocol,
                            "seconds_silent": round(now - last_seen, 2),
                            "timeout": hb_timeout,
                            "event": "node_silent",
                        }),
                    )

            try:
                self.admin.publish(
                    fleet_topic,
                    serialize({
                        "timestamp": int(now),
                        "total": len(self.rooms),
                        "silent": silent,
                        "silent_count": len(silent),
                        "dedup": self._dedup.metrics(),
                    }),
                    qos=1,
                )
            except Exception:
                logger.exception("failed to publish fleet health summary")

    async def shutdown(self) -> None:
        logger.info("Shutting down world engine...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        disconnects = [n.disconnect() for n in self.mqtt_nodes.values()]
        await asyncio.gather(*disconnects, return_exceptions=True)
        stops = [n.stop() for n in self.coap_nodes.values()]
        await asyncio.gather(*stops, return_exceptions=True)

        try:
            await self.db.save_states(self.rooms)
        except Exception:
            logger.exception("final state save failed")
        logger.info("Final state saved. Shutdown complete.")

    def _simulation_time(self) -> int:
        acceleration = self.config["simulation"].get("time_acceleration", 1.0)
        elapsed_real = time.perf_counter() - self._sim_real_start
        return int(self._sim_epoch_start + (elapsed_real * acceleration))
