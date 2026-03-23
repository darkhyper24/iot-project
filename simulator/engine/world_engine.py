import asyncio
import json
import logging
import random
import time
from typing import Any

from simulator.models.room import Room

logger = logging.getLogger(__name__)


class WorldEngine:
    def __init__(self, config: dict, db: Any, mqtt_client):
        self.config = config
        self.db = db
        self.mqtt = mqtt_client
        self.rooms: list[Room] = []
        self._tasks: list[asyncio.Task] = []
        self._last_heartbeats: dict[str, float] = {}
        self._rooms_by_id: dict[str, Room] = {}
        self._sim_real_start = time.perf_counter()
        self._sim_epoch_start = int(time.time())

    async def initialize(self) -> None:
        building_id = self.config["building"]["id"]
        floors = self.config["building"]["floors"]
        rooms_per_floor = self.config["building"]["rooms_per_floor"]

        # Load existing states from PostgreSQL
        saved_states = await self.db.load_states()
        restored = 0

        for f in range(1, floors + 1):
            for r in range(1, rooms_per_floor + 1):
                room_id = f"{building_id}-f{f:02d}-r{f}{r:02d}"
                state = saved_states.get(room_id)
                room = Room(building_id, f, r, self.config, state=state)
                self.rooms.append(room)
                self._rooms_by_id[room.id] = room
                if state:
                    restored += 1

        # Seed heartbeat timestamps so rooms don't immediately appear silent
        now = time.time()
        for room in self.rooms:
            self._last_heartbeats[room.id] = now

        total = len(self.rooms)
        logger.info(
            "Fleet initialized: %d rooms (%d restored from DB, %d fresh)",
            total, restored, total - restored,
        )

    def setup_mqtt(self) -> None:
        self.mqtt.on_message = self._on_message

        prefix = self.config["mqtt"]["topic_prefix"]
        building_slug = self.rooms[0].mqtt_building
        self.mqtt.subscribe(f"{prefix}/{building_slug}/command", qos=1)
        self.mqtt.subscribe(f"{prefix}/{building_slug}/+/command", qos=1)
        self.mqtt.subscribe(f"{prefix}/{building_slug}/+/+/command", qos=1)
        logger.info("MQTT command subscriptions registered for %s", building_slug)

    async def run(self) -> None:
        # Launch one asyncio task per room
        for room in self.rooms:
            task = asyncio.create_task(self._room_loop(room))
            self._tasks.append(task)

        # Launch sync and heartbeat loops
        self._tasks.append(asyncio.create_task(self._sync_loop()))
        self._tasks.append(asyncio.create_task(self._fleet_health_loop()))

        logger.info("World engine running: %d room tasks + sync + fleet health", len(self.rooms))
        await asyncio.gather(*self._tasks)

    async def _room_loop(self, room: Room) -> None:
        tick_interval = self.config["simulation"]["tick_interval"]
        max_jitter = self.config["simulation"]["max_jitter"]
        heartbeat_interval = self.config["heartbeat"]["interval"]
        last_heartbeat_at = 0

        # Startup jitter to prevent thundering herd
        await asyncio.sleep(random.uniform(0, max_jitter))

        while True:
            start = time.perf_counter()
            timestamp = self._simulation_time()

            # Physics update
            room.tick(self.config, timestamp)

            # Fault injection
            room.maybe_inject_fault(self.config)

            # Check for node dropout — skip publishing if active
            if room.active_fault == "node_dropout":
                elapsed = time.perf_counter() - start
                await asyncio.sleep(max(0, tick_interval - elapsed))
                continue

            # Check for telemetry delay
            if room.active_fault == "telemetry_delay":
                delay_ticks = room.fault_data.get("delay_ticks", 1)
                await asyncio.sleep(delay_ticks * tick_interval * 0.1)

            # Publish telemetry
            payload = room.to_telemetry(timestamp)
            topic = f"{room.mqtt_path}/telemetry"
            self.mqtt.publish(topic, json.dumps(payload), qos=0)

            if timestamp - last_heartbeat_at >= heartbeat_interval:
                self.mqtt.publish(
                    f"{room.mqtt_path}/heartbeat",
                    json.dumps(room.heartbeat_payload(timestamp)),
                    qos=0,
                )
                self._last_heartbeats[room.id] = time.time()
                last_heartbeat_at = timestamp

            # Drift compensation
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

    async def _on_message(self, client, topic, payload, qos, properties):
        del client, qos, properties

        try:
            command = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            logger.warning("Rejected malformed command payload on topic %s", topic)
            return

        targets = self._resolve_targets(topic)
        if not targets:
            logger.warning("No simulator targets matched command topic %s", topic)
            return

        if not self._is_valid_command(command):
            logger.warning("Rejected invalid command payload on topic %s: %s", topic, command)
            return

        for room in targets:
            room.apply_command(command)
            room.last_update = self._simulation_time()

        try:
            if len(targets) == 1:
                await self.db.save_room(targets[0])
            else:
                await self.db.save_states(targets)
        except Exception:
            logger.exception("Failed to persist command save point for topic %s", topic)
            return

        logger.info("Applied command to %d room(s) from topic %s", len(targets), topic)

    def _resolve_targets(self, topic: str) -> list[Room]:
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

    def _is_valid_command(self, command: dict) -> bool:
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
