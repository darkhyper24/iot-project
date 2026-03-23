import random


class FaultInjector:
    """Manages fault injection state for a single room."""

    def __init__(self):
        self.active_fault: str | None = None
        self.fault_data: dict = {}
        self._ticks_remaining: int = 0

    def maybe_inject(self, config: dict, current_temp: float) -> None:
        faults_cfg = config["faults"]
        if not faults_cfg["enabled"]:
            return

        if self.active_fault:
            self._ticks_remaining -= 1
            if self._ticks_remaining <= 0:
                self.active_fault = None
                self.fault_data = {}
            else:
                self._apply_active_fault(current_temp)
                return

        if random.random() >= faults_cfg["probability"]:
            return

        available = [t for t, enabled in faults_cfg["types"].items() if enabled]
        if not available:
            return

        fault = random.choice(available)
        self.active_fault = fault
        self._ticks_remaining = random.randint(3, 20)

        if fault == "sensor_drift":
            self.fault_data = {"drift_bias": random.uniform(-0.05, 0.05)}
        elif fault == "frozen_sensor":
            self.fault_data = {"frozen_temp": current_temp}
        elif fault == "telemetry_delay":
            self.fault_data = {"delay_ticks": random.randint(1, 3)}
        elif fault == "node_dropout":
            self.fault_data = {"silent": True}

        self._apply_active_fault(current_temp)

    def _apply_active_fault(self, current_temp: float) -> float:
        if self.active_fault == "sensor_drift":
            self.fault_data["drift_bias"] += random.uniform(-0.02, 0.02)
            return current_temp + self.fault_data["drift_bias"]
        elif self.active_fault == "frozen_sensor":
            return self.fault_data["frozen_temp"]
        return current_temp

    def apply_to_temperature(self, current_temp: float) -> float:
        """Return the temperature after applying any active fault effect."""
        if self.active_fault == "sensor_drift":
            return current_temp + self.fault_data.get("drift_bias", 0)
        elif self.active_fault == "frozen_sensor":
            return self.fault_data.get("frozen_temp", current_temp)
        return current_temp
