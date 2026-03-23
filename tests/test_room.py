import unittest

from simulator.models.room import Room


def make_config() -> dict:
    return {
        "building": {"id": "b01", "floors": 10, "rooms_per_floor": 20},
        "simulation": {
            "tick_interval": 5,
            "max_jitter": 0,
            "db_sync_interval": 30,
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
        "mqtt": {"topic_prefix": "campus"},
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
        "heartbeat": {"interval": 10, "timeout": 60},
    }


class RoomTests(unittest.TestCase):
    def test_room_uses_required_topic_structure_and_flat_telemetry(self):
        room = Room("b01", 5, 2, make_config())

        self.assertEqual(room.id, "b01-f05-r502")
        self.assertEqual(room.mqtt_path, "campus/bldg_01/floor_05/room_502")

        telemetry = room.to_telemetry(1_700_000_000)
        self.assertEqual(telemetry["sensor_id"], "b01-f05-r502")
        self.assertEqual(telemetry["timestamp"], 1_700_000_000)
        self.assertIn("temperature", telemetry)
        self.assertIn("hvac_mode", telemetry)
        self.assertNotIn("metadata", telemetry)
        self.assertNotIn("sensors", telemetry)
        self.assertNotIn("actuators", telemetry)

    def test_tick_applies_deterministic_environment_correlations(self):
        room = Room("b01", 1, 1, make_config())

        day_timestamp = 14 * 3600
        room.tick(make_config(), day_timestamp)
        occupied_light = room.light_level
        occupied_flag = room.occupancy
        first_temp = room.temperature
        first_humidity = room.humidity

        room_again = Room("b01", 1, 1, make_config())
        room_again.tick(make_config(), day_timestamp)

        self.assertEqual(room_again.occupancy, occupied_flag)
        self.assertEqual(room_again.light_level, occupied_light)
        self.assertAlmostEqual(room_again.temperature, first_temp, places=6)
        self.assertAlmostEqual(room_again.humidity, first_humidity, places=6)

        if occupied_flag:
            self.assertGreaterEqual(occupied_light, make_config()["thermal"]["light_threshold"])


if __name__ == "__main__":
    unittest.main()
