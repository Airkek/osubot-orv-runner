import asyncpg
import logging
import os
import asyncio
from migrations import run_migrations

logger = logging.getLogger("orv.db")

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self, retries=5, delay=2):
        # We'll use environment variables - no defaults for sensitive data
        user = os.getenv("POSTGRES_USER", "orv")
        password = os.getenv("POSTGRES_PASSWORD")
        database = os.getenv("POSTGRES_DB", "orv_db")
        host = os.getenv("POSTGRES_HOST", "db")

        if not password:
            raise ValueError("POSTGRES_PASSWORD environment variable is required")
        
        logger.info(f"Connecting to database {user}@{host}/{database}")
        
        for i in range(retries):
            try:
                self.pool = await asyncpg.create_pool(
                    user=user,
                    password=password,
                    database=database,
                    host=host
                )
                await self.init_db()
                logger.info("Database connected and initialized")
                return
            except Exception as e:
                logger.warning(f"Failed to connect to database (attempt {i+1}/{retries}): {e}")
                await asyncio.sleep(delay)
        
        logger.error("Could not connect to database after all retries.")

    async def init_db(self):
        if not self.pool:
            return
        await run_migrations(self.pool)
        logger.info("Database migrations completed")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def get_imported_beatmap(self, beatmap_hash: str):
        if not self.pool:
            return None
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT beatmap_online_id, beatmapset_online_id FROM beatmap_imports WHERE md5_hash = $1",
                    beatmap_hash
                )
                if row:
                    logger.info(f"Found imported beatmap in DB by MD5: {beatmap_hash}")
                    return {
                        "beatmap_online_id": row["beatmap_online_id"],
                        "beatmapset_online_id": row["beatmapset_online_id"]
                    }
                logger.info(f"Imported beatmap not found in DB: {beatmap_hash}")
                return None
        except Exception as e:
            logger.error(f"Error reading beatmap import from DB: {e}")
            return None

    async def save_imported_beatmap(self, beatmap_hash: str, beatmap_online_id: int, beatmapset_online_id: int, md5_hash: str = None):
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO beatmap_imports (beatmap_hash, beatmap_online_id, beatmapset_online_id, md5_hash)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (beatmap_hash) DO UPDATE SET
                        beatmap_online_id = EXCLUDED.beatmap_online_id,
                        beatmapset_online_id = EXCLUDED.beatmapset_online_id,
                        md5_hash = EXCLUDED.md5_hash
                """, beatmap_hash, beatmap_online_id, beatmapset_online_id, md5_hash)
        except Exception as e:
            logger.error(f"Failed to save imported beatmap: {e}")

    async def get_skin_id(self, skin_filename: str):
        if not self.pool:
            return None
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT skin_id FROM skins WHERE skin_filename = $1", skin_filename)
                if row:
                    return row["skin_id"]
                return None
        except Exception as e:
            logger.error(f"Error reading skin from DB: {e}")
            return None

    async def save_skin_info(self, skin_filename: str, skin_name: str, skin_id: str):
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO skins (skin_filename, skin_name, skin_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (skin_filename) DO UPDATE SET
                        skin_name = EXCLUDED.skin_name,
                        skin_id = EXCLUDED.skin_id
                """, skin_filename, skin_name, skin_id)
        except Exception as e:
            logger.error(f"Failed to save imported skin: {e}")

db = Database()
