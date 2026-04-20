import asyncio
import json
import logging
import random
import time
from typing import Any

from gmqtt import Client as MQTTClient

from simulator import addressing
from simulator.coap_server import CampusCoAPSite
from simulator.engine.commands import CommandHandler
from simulator.models.room import Room

logger = logging.getLogger(__name__)


class WorldEngine:
    def __init__(
        self,
        config: dict,
        db: Any,
        mqtt_clients: dict[str, MQTTClient],
        coap: CampusCoAPSite | None,
    ):
        self.config = config
        self.db = db
        self.mqtt_clients = mqtt_clients
        self.coap = coap
        self.rooms: list[Room] = []
        self._tasks: list[asyncio.Task] = []
        self._last_heartbeats: dict[str, float] = {}
        self._rooms_by_id: dict[str, Room] = {}
        self._sim_real_start = time.perf_counter()
        self._sim_epoch_start = int(time.time())
        self._cmd_handler: CommandHandler | None = None

    async def initialize(self) -> None:
        building_id = self.config["building"]["id"]
        floors = self.config["building"]["floors"]
        rooms_per_floor = self.config["building"]["rooms_per_floor"]

        saved_states = await self.db.load_states()
        restored = 0

        for f in range(1, floors + 1):
            for r in range(1, rooms_per_floor + 1):
                room_number = f * 100 + r
                room_id = f"{building_id}-f{f:02d}-r{room_number:03d}"
                state = saved_states.get(room_id)
                room = Room(building_id, f, r, self.config, state=state)
                self.rooms.append(room)
                self._rooms_by_id[room.id] = room
                if state:
                    restored += 1

        now = time.time()
        for room in self.rooms:
            self._last_heartbeats[room.id] = now

        total = len(self.rooms)
        mqtt_n = sum(1 for x in self.rooms if x.uses_mqtt)
        coap_n = sum(1 for x in self.rooms if x.uses_coap)
        logger.info(
            "Fleet initialized: %d rooms (%d MQTT, %d CoAP; %d restored from DB, %d fresh)",
            total, mqtt_n, coap_n, restored, total - restored,
        )

    def setup_mqtt(self, cmd_handler: CommandHandler) -> None:
        self._cmd_handler = cmd_handler
        qos_cmd = 2

        for room in self.rooms:
            if not room.uses_mqtt:
                continue
            client = self.mqtt_clients.get(room.id)
            if client is None:
                logger.error("Missing MQTT client for room %s", room.id)
                continue
            client.on_message = cmd_handler.on_message
            cmd_topic = addressing.mqtt_cmd_topic(self.config, room.floor_number, room.room_number)
            client.subscribe(cmd_topic, qos=qos_cmd)
            logger.debug("MQTT subscribe qos=%s %s", qos_cmd, cmd_topic)

        logger.info("MQTT per-room cmd subscriptions registered (%d clients)", len(self.mqtt_clients))

    def _fleet_monitoring_topic(self) -> str:
        return addressing.fleet_monitoring_topic(self.config)

    async def run(self) -> None:
        for room in self.rooms:
            task = asyncio.create_task(self._room_loop(room))
            self._tasks.append(task)

        self._tasks.append(asyncio.create_task(self._sync_loop()))
        self._tasks.append(asyncio.create_task(self._fleet_health_loop()))

        logger.info("World engine running: %d room tasks + sync + fleet health", len(self.rooms))
        await asyncio.gather(*self._tasks)

    async def _room_loop(self, room: Room) -> None:
        tick_interval = self.config["simulation"]["tick_interval"]
        max_jitter = self.config["simulation"]["max_jitter"]
        heartbeat_interval = self.config["heartbeat"]["interval"]
        last_heartbeat_at = 0

        await asyncio.sleep(random.uniform(0, max_jitter))

        while True:
            start = time.perf_counter()
            timestamp = self._simulation_time()

            room.tick(self.config, timestamp)
            room.maybe_inject_fault(self.config)

            if room.active_fault == "node_dropout":
                elapsed = time.perf_counter() - start
                await asyncio.sleep(max(0, tick_interval - elapsed))
                continue

            if room.active_fault == "telemetry_delay":
                delay_ticks = room.fault_data.get("delay_ticks", 1)
                await asyncio.sleep(delay_ticks * tick_interval * 0.1)

            payload_dict = room.to_telemetry(timestamp)
            payload_json = json.dumps(payload_dict)

            if room.uses_mqtt:
                client = self.mqtt_clients.get(room.id)
                if client is None:
                    logger.error("No MQTT client for %s", room.id)
                else:
                    topic_t = addressing.mqtt_telemetry_topic(
                        self.config, room.floor_number, room.room_number,
                    )
                    client.publish(topic_t, payload_json, qos=0)

            elif room.uses_coap and self.coap is not None:
                self.coap.notify_telemetry(room, payload_json.encode("utf-8"))
                self.coap.notify_sentinel(room, timestamp)

            if timestamp - last_heartbeat_at >= heartbeat_interval:
                self._last_heartbeats[room.id] = time.time()
                last_heartbeat_at = timestamp
                if room.uses_mqtt:
                    client = self.mqtt_clients.get(room.id)
                    if client is not None:
                        hb = json.dumps(room.heartbeat_payload(timestamp))
                        client.publish(
                            addressing.mqtt_heartbeat_topic(
                                self.config, room.floor_number, room.room_number,
                            ),
                            hb,
                            qos=0,
                        )
                        client.publish(self._fleet_monitoring_topic(), hb, qos=0)

            elapsed = time.perf_counter() - start
            await asyncio.sleep(max(0, tick_interval - elapsed))

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

        while True:
            await asyncio.sleep(hb_interval)
            now = time.time()

            for room in self.rooms:
                last_seen = self._last_heartbeats.get(room.id, 0)
                if now - last_seen > hb_timeout:
                    logger.warning(
                        "fleet_health_warning %s",
                        json.dumps(
                            {
                                "room_id": room.id,
                                "seconds_silent": round(now - last_seen, 2),
                                "timeout": hb_timeout,
                                "event": "node_silent",
                            }
                        ),
                    )

    async def shutdown(self) -> None:
        logger.info("Shutting down world engine...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.db.save_states(self.rooms)
        logger.info("Final state saved. Shutdown complete.")

    def _simulation_time(self) -> int:
        acceleration = self.config["simulation"].get("time_acceleration", 1.0)
        elapsed_real = time.perf_counter() - self._sim_real_start
        return int(self._sim_epoch_start + (elapsed_real * acceleration))
