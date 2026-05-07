"""Plan A.2 — round-trip tests for MQTT (global) <-> ThingsBoard (local) IDs."""

from simulator import addressing


def test_round_trip_all_200_rooms():
    for floor in range(1, 11):
        for room_on_floor in range(1, 21):
            n = addressing.mqtt_room_number(floor, room_on_floor)
            assert addressing.mqtt_to_tb_room_on_floor(n) == room_on_floor
            assert addressing.tb_to_mqtt_room_number(room_on_floor, floor) == n
            assert addressing.tb_device_name("b01", floor, room_on_floor).endswith(
                addressing.tb_room_slug(room_on_floor)
            )
            assert addressing.mqtt_room_slug(floor, room_on_floor) == f"r{n:03d}"


def test_known_mappings():
    assert addressing.mqtt_room_number(1, 1) == 101
    assert addressing.mqtt_room_number(5, 20) == 520
    assert addressing.mqtt_to_tb_room_on_floor(520) == 20
    assert addressing.tb_device_name("b01", 5, 20) == "b01-f05-r020"
    assert addressing.mqtt_room_slug(5, 20) == "r520"
