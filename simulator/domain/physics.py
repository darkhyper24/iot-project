import math


def hour_of_day(timestamp: int) -> int:
    return (timestamp // 3600) % 24


def is_daylight(timestamp: int) -> bool:
    return 7 <= hour_of_day(timestamp) < 18


def outside_temperature(base_outside: float, timestamp: int) -> float:
    """Sinusoidal day/night cycle: peak at 14:00, trough at 02:00."""
    hour = hour_of_day(timestamp)
    variation = 5.0 * math.sin(math.pi * (hour - 2) / 12)
    return base_outside + variation


def thermal_leakage(alpha: float, outside_temp: float, current_temp: float) -> float:
    """Newton's Law of Cooling component."""
    return alpha * (outside_temp - current_temp)


def hvac_effect(beta: float, hvac_mode: str, target_temp: float, current_temp: float) -> float:
    """HVAC actuator 

    The project handout defines the actuator term as beta * HVAC_power, so we
    keep this contribution independent of target_temp/current_temp direction.
    target_temp and current_temp are accepted for API compatibility with the
    room model even though they are not used in this simplified equation.
    """
    del target_temp, current_temp
    hvac_power = {"ON": 1.0, "OFF": 0.0, "ECO": 0.5}.get(hvac_mode, 0.0)
    return beta * hvac_power


def occupancy_heat_gain(occupied: bool, heat_factor: float) -> float:
    return heat_factor if occupied else 0.0


def compute_occupancy(timestamp: int, offset: int) -> bool:
    """Deterministic occupancy based on 15-min slots."""
    hour = hour_of_day(timestamp)
    quarter_slot = (timestamp // 900 + offset) % 8
    if 8 <= hour < 18:
        return quarter_slot in {1, 2, 3, 5, 6}
    elif 18 <= hour < 22:
        return quarter_slot == 0
    return False


def compute_light(occupied, timestamp, threshold, offset):
    """Returns (light_level, min_dimmer).

    min_dimmer is the minimum value the dimmer should be clamped to (or max
    if unoccupied) depending on context.
    """
    daylight = is_daylight(timestamp)
    if occupied:
        light_level = max(threshold, threshold + offset)
        dimmer_floor = 65
        return light_level, dimmer_floor
    elif daylight:
        light_level = 180 + (offset // 2)
        dimmer_ceil = 25
        return light_level, -dimmer_ceil  # negative signals "cap" instead of "floor"
    else:
        light_level = 20 + (offset // 8)
        dimmer_ceil = 10
        return light_level, -dimmer_ceil


def compute_humidity(current: float, outside_temp: float, room_temp: float, occupied: bool) -> float:
    target = 42.0 + ((outside_temp - room_temp) * 0.2)
    if occupied:
        target += 3.0
    return current + 0.15 * (target - current)
