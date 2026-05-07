"""Phase 2 topic and URI addressing for MQTT (PDF) and CoAP (aiocoap)."""

from __future__ import annotations


def campus_prefix(config: dict) -> str:
    return config["mqtt"]["topic_prefix"]


def building_slug(config: dict) -> str:
    return str(config["building"]["id"]).lower()


def mqtt_topic_base(config: dict, floor: int, room_number: int) -> str:
    return f"{campus_prefix(config)}/{building_slug(config)}/f{floor:02d}/r{room_number:03d}"


def mqtt_telemetry_topic(config: dict, floor: int, room_number: int) -> str:
    return f"{mqtt_topic_base(config, floor, room_number)}/telemetry"


def mqtt_cmd_topic(config: dict, floor: int, room_number: int) -> str:
    return f"{mqtt_topic_base(config, floor, room_number)}/cmd"


def mqtt_heartbeat_topic(config: dict, floor: int, room_number: int) -> str:
    return f"{mqtt_topic_base(config, floor, room_number)}/heartbeat"


def mqtt_lwt_topic(config: dict, floor: int, room_number: int) -> str:
    return f"{mqtt_topic_base(config, floor, room_number)}/lwt"


def fleet_monitoring_topic(config: dict) -> str:
    return f"{campus_prefix(config)}/{building_slug(config)}/fleet_monitoring/heartbeat"


def coap_path_segments_telemetry(floor: int, room_number: int) -> tuple[str, ...]:
    return (f"f{floor:02d}", f"r{room_number:03d}", "telemetry")


def coap_path_segments_hvac(floor: int, room_number: int) -> tuple[str, ...]:
    return (f"f{floor:02d}", f"r{room_number:03d}", "actuators", "hvac")


def coap_path_segments_sentinel(floor: int, room_number: int) -> tuple[str, ...]:
    return (f"f{floor:02d}", f"r{room_number:03d}", "alerts", "sentinel")


def coap_path_string(segments: tuple[str, ...]) -> str:
    return "/" + "/".join(segments)


def is_mqtt_room(room_num_on_floor: int, config: dict) -> bool:
    """First N rooms per floor use MQTT; remainder use CoAP (default N=10)."""
    split = int(config.get("phase2", {}).get("mqtt_rooms_per_floor", 10))
    return 1 <= room_num_on_floor <= split


def is_coap_room(room_num_on_floor: int, config: dict) -> bool:
    return not is_mqtt_room(room_num_on_floor, config)


# ---------------------------------------------------------------------------
# Plan A.2 — canonical MQTT (global) <-> ThingsBoard (per-floor) ID mapping.
# Two namespaces co-exist intentionally:
#   * MQTT topic / simulator:   r{floor*100 + room_on_floor}, e.g. r109
#   * ThingsBoard device/asset: r{room_on_floor:03d},          e.g. r009
# Node-RED gateways translate between them.
# ---------------------------------------------------------------------------


def mqtt_room_number(floor: int, room_on_floor: int) -> int:
    """Global room number used in MQTT topics + simulator (e.g. floor 1 r9 -> 109)."""
    return floor * 100 + room_on_floor


def mqtt_room_slug(floor: int, room_on_floor: int) -> str:
    """e.g. 'r109'."""
    return f"r{mqtt_room_number(floor, room_on_floor):03d}"


def tb_room_slug(room_on_floor: int) -> str:
    """e.g. 'r009'."""
    return f"r{room_on_floor:03d}"


def tb_device_name(building_id: str, floor: int, room_on_floor: int) -> str:
    """e.g. 'b01-f01-r009'."""
    return f"{building_id}-f{floor:02d}-{tb_room_slug(room_on_floor)}"


def tb_to_mqtt_room_number(tb_room_on_floor: int, floor: int) -> int:
    """Reverse of mqtt_room_number for the TB-style local index."""
    return mqtt_room_number(floor, tb_room_on_floor)


def mqtt_to_tb_room_on_floor(mqtt_room_number_value: int) -> int:
    """Reverse: 109 -> 9."""
    return mqtt_room_number_value % 100
