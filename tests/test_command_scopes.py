"""Plan A.4 — broad command resolves to all rooms (MQTT + CoAP)."""

import asyncio
from unittest.mock import AsyncMock

from simulator.config import load_config
from simulator.domain.room import Room
from simulator.engine.commands import CommandHandler


def _build_rooms(config) -> list[Room]:
    rooms = []
    for f in range(1, config["building"]["floors"] + 1):
        for r in range(1, config["building"]["rooms_per_floor"] + 1):
            rooms.append(Room(config["building"]["id"], f, r, config))
    return rooms


def test_broadcast_resolves_to_all_200_rooms():
    config = load_config()
    rooms = _build_rooms(config)
    rooms_by_id = {room.id: room for room in rooms}
    handler = CommandHandler(config, rooms, rooms_by_id, db=AsyncMock(), get_time_fn=lambda: 0)
    targets = handler.resolve_targets("campus/b01/cmd")
    assert len(targets) == 200


def test_floor_resolves_to_20_rooms_mqtt_and_coap():
    config = load_config()
    rooms = _build_rooms(config)
    handler = CommandHandler(config, rooms, {r.id: r for r in rooms}, db=AsyncMock(), get_time_fn=lambda: 0)
    targets = handler.resolve_targets("campus/b01/f05/cmd")
    assert len(targets) == 20
    assert any(r.uses_coap for r in targets)
    assert any(r.uses_mqtt for r in targets)


def test_per_room_topic_only_mqtt():
    config = load_config()
    rooms = _build_rooms(config)
    handler = CommandHandler(config, rooms, {r.id: r for r in rooms}, db=AsyncMock(), get_time_fn=lambda: 0)
    # MQTT room (01-10): resolves
    targets = handler.resolve_targets("campus/b01/f01/r101/cmd")
    assert len(targets) == 1 and targets[0].uses_mqtt
    # CoAP room: no MQTT delivery
    targets = handler.resolve_targets("campus/b01/f01/r115/cmd")
    assert targets == []
