import math
import random


class Room:
    def __init__(self, building_id: str, floor: int, room_num: int, config: dict, state: dict | None = None):
        self.building_id = building_id
        self.floor_number = floor
        self.room_number = floor * 100 + room_num
        self.floor_id = f"f{floor:02d}"
        self.room_id = f"r{self.room_number:03d}"
        self.id = f"{building_id}-{self.floor_id}-{self.room_id}"
        self.mqtt_floor = f"floor_{floor:02d}"
        self.mqtt_room = f"room_{self.room_number:03d}"
        self.mqtt_building = self._format_building_slug(building_id)
        self.mqtt_path = f"{config['mqtt']['topic_prefix']}/{self.mqtt_building}/{self.mqtt_floor}/{self.mqtt_room}"

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

        # Fault state
        self.active_fault: str | None = None
        self.fault_data: dict = {}
        self._fault_ticks_remaining = 0

    def tick(self, config: dict, timestamp: int) -> None:
        thermal = config["thermal"]
        alpha = thermal["alpha"]
        beta = thermal["beta"]
        outside_temp = self._get_outside_temp(thermal["outside_temp"], timestamp)

        # 1. Thermal leakage (Newton's Law of Cooling)
        leakage = alpha * (outside_temp - self.temperature)

        self._update_occupancy(timestamp)

        # 2. HVAC actuator impact
        hvac_power = {"ON": 1.0, "OFF": 0.0, "ECO": 0.5}.get(self.hvac_mode, 0.0)
        hvac_effect = 0.0
        if hvac_power > 0 and abs(self.target_temp - self.temperature) > 0.01:
            direction = 1.0 if self.target_temp > self.temperature else -1.0
            hvac_effect = beta * hvac_power * direction

        # 3. Occupancy heat contribution
        occupancy_effect = thermal["occupancy_heat"] if self.occupancy else 0.0

        # 4. Update temperature
        self.temperature += leakage + hvac_effect + occupancy_effect

        # 5. Environmental correlations
        self._update_light(thermal["light_threshold"], timestamp)
        self._update_humidity(outside_temp)

        # 6. Clamp to spec ranges
        self.temperature = max(15.0, min(50.0, self.temperature))
        self.humidity = max(0.0, min(100.0, self.humidity))
        self.light_level = max(0, min(1000, self.light_level))
        self.lighting_dimmer = max(0, min(100, self.lighting_dimmer))

        self.last_update = timestamp

    def maybe_inject_fault(self, config: dict) -> None:
        faults_cfg = config["faults"]
        if not faults_cfg["enabled"]:
            return

        # If a fault is active, decrement its duration
        if self.active_fault:
            self._fault_ticks_remaining -= 1
            if self._fault_ticks_remaining <= 0:
                self.active_fault = None
                self.fault_data = {}
            else:
                self._apply_active_fault()
                return

        # Probabilistic fault injection
        if random.random() >= faults_cfg["probability"]:
            return

        available = [t for t, enabled in faults_cfg["types"].items() if enabled]
        if not available:
            return

        fault = random.choice(available)
        self.active_fault = fault
        self._fault_ticks_remaining = random.randint(3, 20)

        if fault == "sensor_drift":
            self.fault_data = {"drift_bias": random.uniform(-0.05, 0.05)}
        elif fault == "frozen_sensor":
            self.fault_data = {"frozen_temp": self.temperature}
        elif fault == "telemetry_delay":
            self.fault_data = {"delay_ticks": random.randint(1, 3)}
        elif fault == "node_dropout":
            self.fault_data = {"silent": True}

        self._apply_active_fault()

    def _apply_active_fault(self) -> None:
        if self.active_fault == "sensor_drift":
            self.fault_data["drift_bias"] += random.uniform(-0.02, 0.02)
            self.temperature += self.fault_data["drift_bias"]
        elif self.active_fault == "frozen_sensor":
            self.temperature = self.fault_data["frozen_temp"]

    def to_telemetry(self, timestamp: int) -> dict:
        return {
            "metadata": {
                "sensor_id": self.id,
                "building": self.building_id,
                "floor": self.floor_number,
                "room": self.room_number,
                "timestamp": timestamp,
                "fault": self.active_fault or "none",
            },
            "sensors": {
                "temperature": round(self.temperature, 2),
                "humidity": round(self.humidity, 2),
                "occupancy": self.occupancy,
                "light_level": self.light_level,
            },
            "actuators": {
                "hvac_mode": self.hvac_mode,
                "lighting_dimmer": self.lighting_dimmer,
                "target_temp": round(self.target_temp, 2),
            },
        }

    def heartbeat_payload(self, timestamp: int) -> dict:
        return {
            "room_id": self.id,
            "status": "alive",
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

    def apply_command(self, command: dict) -> None:
        if "hvac_mode" in command:
            self.hvac_mode = command["hvac_mode"]
        if "target_temp" in command:
            self.target_temp = float(command["target_temp"])
        if "lighting_dimmer" in command:
            self.lighting_dimmer = int(command["lighting_dimmer"])

        self.target_temp = max(15.0, min(50.0, self.target_temp))
        self.lighting_dimmer = max(0, min(100, self.lighting_dimmer))

    def _get_outside_temp(self, base_outside: float, timestamp: int) -> float:
        hour = self._hour_of_day(timestamp)
        # Sinusoidal day/night cycle: peak at 14:00, trough at 02:00
        variation = 5.0 * math.sin(math.pi * (hour - 2) / 12)
        return base_outside + variation

    def _update_occupancy(self, timestamp: int) -> None:
        hour = self._hour_of_day(timestamp)
        quarter_slot = (timestamp // 900 + self._occupancy_offset) % 8
        if 8 <= hour < 18:
            self.occupancy = quarter_slot in {1, 2, 3, 5, 6}
        elif 18 <= hour < 22:
            self.occupancy = quarter_slot == 0
        else:
            self.occupancy = False

    def _update_light(self, threshold: int, timestamp: int) -> None:
        daylight = self._is_daylight(timestamp)
        if self.occupancy:
            self.light_level = max(threshold, threshold + self._light_offset)
            self.lighting_dimmer = max(self.lighting_dimmer, 65)
        elif daylight:
            self.light_level = 180 + (self._light_offset // 2)
            self.lighting_dimmer = min(self.lighting_dimmer, 25)
        else:
            self.light_level = 20 + (self._light_offset // 8)
            self.lighting_dimmer = min(self.lighting_dimmer, 10)

    def _update_humidity(self, outside_temp: float) -> None:
        target_humidity = 42.0 + ((outside_temp - self.temperature) * 0.2)
        if self.occupancy:
            target_humidity += 3.0
        self.humidity += 0.15 * (target_humidity - self.humidity)

    def _hour_of_day(self, timestamp: int) -> int:
        return (timestamp // 3600) % 24

    def _is_daylight(self, timestamp: int) -> bool:
        hour = self._hour_of_day(timestamp)
        return 7 <= hour < 18

    def _format_building_slug(self, building_id: str) -> str:
        digits = "".join(ch for ch in building_id if ch.isdigit()) or "01"
        return f"bldg_{int(digits):02d}"
