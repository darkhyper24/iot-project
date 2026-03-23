import asyncio
import logging
import signal

from gmqtt import Client as MQTTClient

from simulator.config import load_config
from simulator.engine.world_engine import WorldEngine
from simulator.persistence.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    logger.info("Configuration loaded")

    # Connect to PostgreSQL
    db = Database(config)
    await db.connect()
    await db.init_db()

    # Connect to MQTT broker with retry
    mqtt = MQTTClient(client_id="world-engine")
    broker_host = config["mqtt"]["broker_host"]
    broker_port = config["mqtt"]["broker_port"]

    for attempt in range(1, 11):
        try:
            await mqtt.connect(broker_host, broker_port)
            logger.info("Connected to MQTT broker at %s:%d", broker_host, broker_port)
            break
        except Exception as e:
            logger.warning("MQTT connection attempt %d/10 failed: %s", attempt, e)
            if attempt == 10:
                raise ConnectionError("Could not connect to MQTT broker")
            await asyncio.sleep(2)

    # Initialize and run the world engine
    engine = WorldEngine(config, db, mqtt)
    await engine.initialize()
    engine.setup_mqtt()

    # Graceful shutdown handler
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    # Run engine in background, wait for stop signal or engine crash
    engine_task = asyncio.create_task(engine.run())
    engine_task.add_done_callback(lambda t: stop_event.set() if t.exception() else None)

    await stop_event.wait()

    if not engine_task.done():
        engine_task.cancel()

    # Shutdown
    await engine.shutdown()
    await mqtt.disconnect()
    await db.close()
    logger.info("Clean shutdown complete")
