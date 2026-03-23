import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, config: dict):
        db = config["database"]
        self.dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['dbname']}"
        self.pool: asyncpg.Pool | None = None

    async def connect(self, retries: int = 10, delay: float = 2.0) -> None:
        for attempt in range(1, retries + 1):
            try:
                self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=10)
                logger.info("Connected to PostgreSQL")
                return
            except (asyncpg.PostgresError, OSError) as e:
                logger.warning("PostgreSQL connection attempt %d/%d failed: %s", attempt, retries, e)
                if attempt < retries:
                    await asyncio.sleep(delay)
        raise ConnectionError("Could not connect to PostgreSQL after %d attempts" % retries)

    async def init_db(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS room_states (
                    room_id     VARCHAR(20) PRIMARY KEY,
                    last_temp   FLOAT       NOT NULL,
                    last_humidity FLOAT     NOT NULL,
                    hvac_mode   VARCHAR(10) NOT NULL,
                    target_temp FLOAT       NOT NULL,
                    last_update BIGINT      NOT NULL
                )
            """)
        logger.info("Database table room_states ready")

    async def load_states(self) -> dict[str, dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM room_states")
        return {
            row["room_id"]: {
                "last_temp": row["last_temp"],
                "last_humidity": row["last_humidity"],
                "hvac_mode": row["hvac_mode"],
                "target_temp": row["target_temp"],
            }
            for row in rows
        }

    async def save_states(self, rooms) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO room_states (room_id, last_temp, last_humidity, hvac_mode, target_temp, last_update)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (room_id) DO UPDATE SET
                    last_temp = EXCLUDED.last_temp,
                    last_humidity = EXCLUDED.last_humidity,
                    hvac_mode = EXCLUDED.hvac_mode,
                    target_temp = EXCLUDED.target_temp,
                    last_update = EXCLUDED.last_update
                """,
                [room.to_db_row() for room in rooms],
            )
        logger.debug("Synced %d room states to PostgreSQL", len(rooms))

    async def save_room(self, room) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO room_states (room_id, last_temp, last_humidity, hvac_mode, target_temp, last_update)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (room_id) DO UPDATE SET
                    last_temp = EXCLUDED.last_temp,
                    last_humidity = EXCLUDED.last_humidity,
                    hvac_mode = EXCLUDED.hvac_mode,
                    target_temp = EXCLUDED.target_temp,
                    last_update = EXCLUDED.last_update
                """,
                *room.to_db_row(),
            )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL connection pool closed")
