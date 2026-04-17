import hashlib
import hmac
import os

from simulator import physics
from simulator.faults import FaultInjector


class Room:
    def __init__(self, building_id: str, floor: int, room_num: int, config: dict, state: dict | None = None):
        self.building_id = building_id
        self.floor_number = floor
        self.room_number = floor * 100 + room_num
        self.floor_id = f"f{floor:02d}"
        self.room_id = f"r{self.room_number:03d}"
        self.id = f"{building_id}-{self.floor_id}-{self.room_id}"

        # Spec-canonical MQTT tree: campus/b01/f05/r502
        self.mqtt_building = building_id
        self.mqtt_floor = self.floor_id
        self.mqtt_room = self.room_id
        self.mqtt_path = f"{config['mqtt']['topic_prefix']}/{self.mqtt_building}/{self.mqtt_floor}/{self.mqtt_room}"

        # Protocol assignment per floor: rooms 1-10 -> CoAP, 11-20 -> MQTT.
        transport = config.get("transport", {})
        coap_rooms = transport.get("coap_rooms_per_floor", 10)
        self.protocol = "coap" if room_num <= coap_rooms else "mqtt"

        # Stable 0..N-1 index used for deterministic port + credential derivation.
        rooms_per_floor = config["building"]["rooms_per_floor"]
        self.global_index = (floor - 1) * rooms_per_floor + (room_num - 1)

        # CoAP port (only meaningful when protocol == "coap")
        coap_cfg = config.get("coap", {})
        self.coap_base_port = coap_cfg.get("base_port", 5683)
        self.coap_port = self.coap_base_port + self.global_index

        # MQTT auth identity (derived; master secret only read when requested)
        self.mqtt_username = f"room-{floor:02d}-{room_num:02d}"
        self.psk_identity = self.mqtt_username

        thermal = config["thermal"]
        if state:
            self.temperature = state.get("last_temp", thermal["default_temp"])
            self.humidity = state.get("last_humidity", 45.0)
            self.hvac_mode = state.get("hvac_mode", "OFF")
            self.target_temp = state.get("target_temp", thermal["default_temp"])
        else:
            self.temperature = thermal["default_temp"]
            self.humidity = 45.0
            self.hvac_mode = "OFF"
            self.target_temp = thermal["default_temp"]

        self.occupancy = False
        self.light_level = 200
        self.lighting_dimmer = 50
        self.last_update = 0
        self._occupancy_offset = (floor * 17 + room_num * 7) % 8
        self._light_offset = (floor * 31 + room_num * 11) % 120

        self.fault_injector = FaultInjector()

    @property
    def active_fault(self) -> str | None:
        return self.fault_injector.active_fault

    @active_fault.setter
    def active_fault(self, value: str | None) -> None:
        self.fault_injector.active_fault = value

    @property
    def fault_data(self) -> dict:
        return self.fault_injector.fault_data

    @fault_data.setter
    def fault_data(self, value: dict) -> None:
        self.fault_injector.fault_data = value

    def topic(self, suffix: str) -> str:
        return f"{self.mqtt_path}/{suffix}"

    def mqtt_password(self, master_secret: str | None = None) -> str:
        secret = master_secret if master_secret is not None else os.environ.get("MQTT_PASSWORD_SECRET", "dev-secret")
        return hmac.new(secret.encode(), self.mqtt_username.encode(), hashlib.sha256).hexdigest()

    def psk_key(self, master_secret: str | None = None) -> bytes:
        secret = master_secret if master_secret is not None else os.environ.get("COAP_PSK_MASTER", "dev-psk")
        return hmac.new(secret.encode(), self.psk_identity.encode(), hashlib.sha256).digest()

    def tick(self, config: dict, timestamp: int) -> None:
        thermal = config["thermal"]
        alpha = thermal["alpha"]
        beta = thermal["beta"]
        outside_temp = physics.outside_temperature(thermal["outside_temp"], timestamp)

        leakage = physics.thermal_leakage(alpha, outside_temp, self.temperature)
        self.occupancy = physics.compute_occupancy(timestamp, self._occupancy_offset)
        hvac = physics.hvac_effect(beta, self.hvac_mode, self.target_temp, self.temperature)
        occ_heat = physics.occupancy_heat_gain(self.occupancy, thermal["occupancy_heat"])

        self.temperature += leakage + hvac + occ_heat

        light_level, dimmer_hint = physics.compute_light(
            self.occupancy, timestamp, thermal["light_threshold"], self._light_offset,
        )
        self.light_level = light_level
        if dimmer_hint >= 0:
            self.lighting_dimmer = max(self.lighting_dimmer, dimmer_hint)
        else:
            self.lighting_dimmer = min(self.lighting_dimmer, -dimmer_hint)

        self.humidity = physics.compute_humidity(
            self.humidity, outside_temp, self.temperature, self.occupancy,
        )

        self.temperature = max(15.0, min(50.0, self.temperature))
        self.humidity = max(0.0, min(100.0, self.humidity))
        self.light_level = max(0, min(1000, self.light_level))
        self.lighting_dimmer = max(0, min(100, self.lighting_dimmer))

        self.last_update = timestamp

    def maybe_inject_fault(self, config: dict) -> None:
        self.fault_injector.maybe_inject(config, self.temperature)
        self.temperature = self.fault_injector.apply_to_temperature(self.temperature)

    def to_telemetry(self, timestamp: int) -> dict:
        return {
            "sensor_id": self.id,
            "building": self.building_id,
            "floor": self.floor_number,
            "room": self.room_number,
            "protocol": self.protocol,
            "timestamp": timestamp,
            "fault": self.active_fault or "none",
            "temperature": round(self.temperature, 2),
            "humidity": round(self.humidity, 2),
            "occupancy": self.occupancy,
            "light_level": self.light_level,
            "hvac_mode": self.hvac_mode,
            "lighting_dimmer": self.lighting_dimmer,
            "target_temp": round(self.target_temp, 2),
        }

    def heartbeat_payload(self, timestamp: int) -> dict:
        return {
            "room_id": self.id,
            "status": "alive",
            "protocol": self.protocol,
            "timestamp": timestamp,
        }

    def to_db_row(self) -> tuple:
        return (
            self.id,
            round(self.temperature, 2),
            round(self.humidity, 2),
            self.hvac_mode,
            self.target_temp,
            self.last_update,
        )

    @classmethod
    def from_db_row(cls, row: dict, building_id: str, floor: int, room_num: int, config: dict) -> "Room":
        state = {
            "last_temp": row["last_temp"],
            "last_humidity": row["last_humidity"],
            "hvac_mode": row["hvac_mode"],
            "target_temp": row["target_temp"],
        }
        return cls(building_id, floor, room_num, config, state=state)

    def apply_command(self, command: dict) -> bool:
        """Apply a command. Returns True if state changed, False if no-op (idempotent replay)."""
        before = (self.hvac_mode, round(self.target_temp, 3), self.lighting_dimmer)

        # Accept both direct keys and spec-style {"action": "set_hvac", "value": "ON"} shape.
        action = command.get("action")
        value = command.get("value")
        if action == "set_hvac" and value is not None:
            self.hvac_mode = value
        elif action == "set_target_temp" and value is not None:
            self.target_temp = float(value)
        elif action == "set_dimmer" and value is not None:
            self.lighting_dimmer = int(value)

        if "hvac_mode" in command:
            self.hvac_mode = command["hvac_mode"]
        if "target_temp" in command:
            self.target_temp = float(command["target_temp"])
        if "lighting_dimmer" in command:
            self.lighting_dimmer = int(command["lighting_dimmer"])

        self.target_temp = max(15.0, min(50.0, self.target_temp))
        self.lighting_dimmer = max(0, min(100, self.lighting_dimmer))

        after = (self.hvac_mode, round(self.target_temp, 3), self.lighting_dimmer)
        return before != after
