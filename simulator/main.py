import asyncio
import json
import logging
import signal

from gmqtt import Client as MQTTClient
from gmqtt.client import Message as MQTTWillMessage
from gmqtt.mqtt.constants import MQTTv311, MQTTv50

from simulator import addressing
from simulator.coap_server import CampusCoAPSite
from simulator.config import load_config
from simulator.credentials import load_mqtt_credentials_map, mqtt_user_pass_for_room
from simulator.engine.commands import CommandHandler
from simulator.engine.world_engine import WorldEngine
from simulator.mqtt_tls import ssl_context_from_config
from simulator.persistence.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _mqtt_protocol_version(config: dict):
    v = str(config["mqtt"].get("protocol_version", "5"))
    return MQTTv311 if v.startswith("3") else MQTTv50


async def _create_and_connect_mqtt_clients(
    config: dict,
    rooms: list,
) -> dict[str, MQTTClient]:
    broker_host = config["mqtt"]["broker_host"]
    broker_port = int(config["mqtt"]["broker_port"])
    ssl_ctx = ssl_context_from_config(config)
    ssl_arg = ssl_ctx if ssl_ctx is not None else False
    stagger = float(config.get("phase2", {}).get("mqtt_connect_stagger_s", 0.05))
    proto_ver = _mqtt_protocol_version(config)

    cred_map = load_mqtt_credentials_map(config)

    clients: dict[str, MQTTClient] = {}

    mqtt_rooms = [r for r in rooms if r.uses_mqtt]
    for i, room in enumerate(mqtt_rooms):
        cid = f"sim-b{room.room_number:04d}"
        lwt_topic = addressing.mqtt_lwt_topic(config, room.floor_number, room.room_number)
        will_msg = MQTTWillMessage(
            lwt_topic,
            json.dumps({"room_id": room.id, "status": "offline"}),
            qos=1,
            retain=False,
        )
        client = MQTTClient(client_id=cid, will_message=will_msg)

        auth = mqtt_user_pass_for_room(config, room, cred_map)
        if not auth:
            raise RuntimeError(
                f"No MQTT credentials for room {room.id}. "
                "Run: python scripts/generate_campus_secrets.py "
                "or set mqtt.username / mqtt.password in config.",
            )
        client.set_auth_credentials(auth[0], auth[1])

        clients[room.id] = client

        for attempt in range(1, 11):
            try:
                await client.connect(broker_host, broker_port, ssl=ssl_arg, version=proto_ver)
                logger.info("MQTT connected %s -> %s:%s", cid, broker_host, broker_port)
                break
            except Exception as e:
                logger.warning("MQTT connect %s attempt %d/10 failed: %s", cid, attempt, e)
                if attempt == 10:
                    raise ConnectionError(f"Could not connect MQTT client {cid}") from e
                await asyncio.sleep(2)

        if i < len(mqtt_rooms) - 1 and stagger > 0:
            await asyncio.sleep(stagger)

    return clients


async def main() -> None:
    config = load_config()
    logger.info("Configuration loaded")

    db = Database(config)
    await db.connect()
    await db.init_db()

    engine = WorldEngine(config, db, mqtt_clients={}, coap=None)
    await engine.initialize()

    cmd_handler = CommandHandler(
        config,
        engine.rooms,
        engine._rooms_by_id,
        db,
        engine._simulation_time,
    )

    coap_site = CampusCoAPSite(config, engine.rooms, cmd_handler)
    coap_site.build()
    await coap_site.start()
    engine.coap = coap_site

    mqtt_clients = await _create_and_connect_mqtt_clients(config, engine.rooms)
    engine.mqtt_clients = mqtt_clients
    engine.setup_mqtt(cmd_handler)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    async def monitor_event_loop(period: float = 1.0, threshold: float = 0.2) -> None:
        next_time = loop.time() + period
        while True:
            await asyncio.sleep(max(0, next_time - loop.time()))
            now = loop.time()
            delay = now - next_time
            logger.info("Event loop latency %.5f s", delay)
            if delay > threshold:
                logger.warning(
                    "Event loop latency %.5f s exceeds %.5f s",
                    delay,
                    threshold,
                )
            next_time += period

    def _handle_signal():
        logger.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    monitor_task = asyncio.create_task(monitor_event_loop())

    engine_task = asyncio.create_task(engine.run())
    engine_task.add_done_callback(lambda t: stop_event.set() if t.exception() else None)

    await stop_event.wait()

    if not engine_task.done():
        engine_task.cancel()
    monitor_task.cancel()

    await engine.shutdown()

    for room_id, client in list(mqtt_clients.items()):
        try:
            await client.disconnect()
            logger.debug("MQTT disconnected %s", room_id)
        except Exception:
            logger.exception("MQTT disconnect error for %s", room_id)

    await coap_site.shutdown()
    await db.close()
    logger.info("Clean shutdown complete")
