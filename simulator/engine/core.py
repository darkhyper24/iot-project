"""Shared simulation core used by both MQTT and CoAP transport drivers.

Owns: tick cadence, startup jitter, physics + fault application, drift
compensation, telemetry/heartbeat payload assembly. Transport drivers
subscribe to per-tick callbacks to publish via their own protocol.
"""
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from simulator.models.room import Room

logger = logging.getLogger(__name__)


@dataclass
class TickResult:
    timestamp: int
    telemetry: dict
    heartbeat: dict | None   # None when not due this tick
    dropped: bool            # node_dropout fault active -> skip publish
    delay_seconds: float     # telemetry_delay fault contribution


TickCallback = Callable[[Room, TickResult], Awaitable[None]]


class SimulationCore:
    def __init__(
        self,
        config: dict,
        get_sim_time: Callable[[], int],
    ):
        self.config = config
        self._get_sim_time = get_sim_time
        self.tick_interval = config["simulation"]["tick_interval"]
        self.max_jitter = config["simulation"]["max_jitter"]
        self.heartbeat_interval = config["heartbeat"]["interval"]

    async def run_room(self, room: Room, on_tick: TickCallback) -> None:
        """Per-room event loop. Runs forever; cancelled on shutdown."""
        await asyncio.sleep(random.uniform(0, self.max_jitter))
        last_heartbeat_at = 0

        while True:
            start = time.perf_counter()
            timestamp = self._get_sim_time()

            room.tick(self.config, timestamp)
            room.maybe_inject_fault(self.config)

            dropped = room.active_fault == "node_dropout"
            delay_s = 0.0
            if room.active_fault == "telemetry_delay":
                delay_s = room.fault_data.get("delay_ticks", 1) * self.tick_interval * 0.1

            heartbeat = None
            if timestamp - last_heartbeat_at >= self.heartbeat_interval:
                heartbeat = room.heartbeat_payload(timestamp)
                last_heartbeat_at = timestamp

            result = TickResult(
                timestamp=timestamp,
                telemetry=room.to_telemetry(timestamp),
                heartbeat=heartbeat,
                dropped=dropped,
                delay_seconds=delay_s,
            )

            try:
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                if not dropped:
                    await on_tick(room, result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Transport publish failed for room %s", room.id)

            elapsed = time.perf_counter() - start
            await asyncio.sleep(max(0, self.tick_interval - elapsed))


def serialize(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()
