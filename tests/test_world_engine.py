import asyncio
import json
import unittest
from unittest.mock import patch

from simulator.engine.world_engine import WorldEngine


def make_config() -> dict:
    return {
        "building": {"id": "b01", "floors": 2, "rooms_per_floor": 2},
        "simulation": {
            "tick_interval": 0.01,
            "max_jitter": 0,
            "db_sync_interval": 60,
            "time_acceleration": 1.0,
        },
        "thermal": {
            "alpha": 0.01,
            "beta": 0.2,
            "default_temp": 22.0,
            "outside_temp": 35.0,
            "occupancy_heat": 0.05,
            "light_threshold": 300,
        },
        "mqtt": {
            "topic_prefix": "campus",
            "broker_host": "mqtt-broker",
            "broker_port": 1883,
        },
        "faults": {
            "enabled": False,
            "probability": 0.0,
            "types": {
                "sensor_drift": True,
                "frozen_sensor": True,
                "telemetry_delay": True,
                "node_dropout": True,
            },
        },
        "heartbeat": {"interval": 1, "timeout": 2},
    }


class FakeDB:
    def __init__(self):
        self.saved_rooms = []
        self.saved_batches = []

    async def load_states(self):
        return {}

    async def save_room(self, room):
        self.saved_rooms.append(room.id)

    async def save_states(self, rooms):
        self.saved_batches.append([room.id for room in rooms])


class FakeMQTT:
    def __init__(self):
        self.subscriptions = []
        self.published = []
        self.on_message = None

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))


class WorldEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = make_config()
        self.db = FakeDB()
        self.mqtt = FakeMQTT()
        self.engine = WorldEngine(self.config, self.db, self.mqtt)
        await self.engine.initialize()
        self.engine.setup_mqtt()

    async def test_setup_mqtt_subscribes_to_building_floor_and_room_commands(self):
        self.assertEqual(
            self.mqtt.subscriptions,
            [
                ("campus/bldg_01/command", 1),
                ("campus/bldg_01/+/command", 1),
                ("campus/bldg_01/+/+/command", 1),
            ],
        )
        self.assertIsNotNone(self.mqtt.on_message)

    async def test_building_command_updates_all_rooms_and_persists_save_point(self):
        payload = json.dumps({"hvac_mode": "ECO", "target_temp": 24, "lighting_dimmer": 40})
        await self.engine._on_message(None, "campus/bldg_01/command", payload, 1, None)

        self.assertEqual(len(self.db.saved_batches), 1)
        self.assertEqual(len(self.db.saved_batches[0]), 4)
        self.assertTrue(all(room.hvac_mode == "ECO" for room in self.engine.rooms))
        self.assertTrue(all(room.target_temp == 24 for room in self.engine.rooms))
        self.assertTrue(all(room.lighting_dimmer == 40 for room in self.engine.rooms))

    async def test_room_command_targets_only_one_room(self):
        payload = json.dumps({"hvac_mode": "ON", "target_temp": 26})
        await self.engine._on_message(None, "campus/bldg_01/floor_01/room_101/command", payload, 1, None)

        self.assertEqual(self.db.saved_rooms, ["b01-f01-r101"])
        target = next(room for room in self.engine.rooms if room.id == "b01-f01-r101")
        untouched = next(room for room in self.engine.rooms if room.id == "b01-f01-r102")
        self.assertEqual(target.hvac_mode, "ON")
        self.assertEqual(target.target_temp, 26)
        self.assertEqual(untouched.hvac_mode, "OFF")

    async def test_node_dropout_stays_silent(self):
        room = self.engine.rooms[0]
        room.active_fault = "node_dropout"
        room.fault_data = {"silent": True}

        task = asyncio.create_task(self.engine._room_loop(room))
        await asyncio.sleep(0.03)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(self.mqtt.published, [])

    async def test_fleet_health_logs_silent_node_warning(self):
        room = self.engine.rooms[0]
        self.engine._last_heartbeats[room.id] = 0

        with patch("simulator.engine.world_engine.logger.warning") as warning:
            task = asyncio.create_task(self.engine._fleet_health_loop())
            await asyncio.sleep(1.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(warning.called)
        self.assertIn("fleet_health_warning", warning.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
