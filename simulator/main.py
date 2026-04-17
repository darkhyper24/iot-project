import asyncio
import logging
import os
import signal
import ssl

from gmqtt import Client as MQTTClient

from simulator.config import load_config
from simulator.engine.world_engine import WorldEngine
from simulator.persistence.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _connect_admin_client(config: dict) -> MQTTClient:
    """Admin client owns the fleet topic + building/floor command fan-in."""
    security = config.get("security", {})
    tls_enabled = bool(security.get("mqtt_tls", True))
    broker_host = config["mqtt"]["broker_host"]
    broker_port = (
        config["mqtt"]["broker_port"] if tls_enabled
        else config["mqtt"].get("plaintext_port", 1883)
    )

    admin_id = config.get("admin", {}).get("client_id", "world-engine-admin")
    client = MQTTClient(client_id=admin_id, clean_session=True)

    admin_user = os.environ.get("ADMIN_MQTT_USER", "admin")
    admin_pass = os.environ.get("ADMIN_MQTT_PASSWORD", "admin-secret")
    client.set_auth_credentials(admin_user, admin_pass)

    ssl_ctx: ssl.SSLContext | bool = False
    if tls_enabled:
        ca_path = security.get("mqtt_ca_path") or "/certs/ca.crt"
        ssl_ctx = ssl.create_default_context(cafile=ca_path if os.path.exists(ca_path) else None)
        if security.get("mqtt_insecure_skip_verify", True):
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(1, 11):
        try:
            await client.connect(broker_host, broker_port, ssl=ssl_ctx, keepalive=30)
            logger.info(
                "Admin MQTT client connected id=%s %s:%d tls=%s",
                admin_id, broker_host, broker_port, tls_enabled,
            )
            return client
        except Exception as e:
            logger.warning("Admin MQTT connect attempt %d/10 failed: %s", attempt, e)
            if attempt == 10:
                raise ConnectionError("Could not connect admin MQTT client")
            await asyncio.sleep(2)


async def main() -> None:
    config = load_config()
    logger.info("Configuration loaded")

    db = Database(config)
    await db.connect()
    await db.init_db()

    admin_mqtt = await _connect_admin_client(config)

    engine = WorldEngine(config, db, admin_mqtt)
    await engine.initialize()
    engine.setup_command_handler()

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
    try:
        await admin_mqtt.disconnect()
    except Exception:
        logger.exception("admin mqtt disconnect error")
    await db.close()
    logger.info("Clean shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
