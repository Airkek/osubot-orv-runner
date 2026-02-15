import asyncpg
import logging

logger = logging.getLogger("orv.migrations")


async def migrate_0001_init_schema(conn):
    """Initial schema creation"""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS beatmap_cache (
            beatmap_hash TEXT PRIMARY KEY,
            beatmapset_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    logger.info("Migration 0001: Initial schema created")


async def migrate_0002_add_import_tables(conn):
    """Add beatmap_imports and skins tables"""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS beatmap_imports (
            beatmap_hash TEXT PRIMARY KEY,
            beatmap_online_id INT NOT NULL,
            beatmapset_online_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS skins (
            skin_filename TEXT PRIMARY KEY,
            skin_name TEXT,
            skin_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    logger.info("Migration 0002: Import tables created")


async def migrate_0003_drop_beatmap_cache(conn):
    """Drop old beatmap_cache table"""
    # Check if beatmap_cache exists and drop it
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'beatmap_cache'
        );
    """)
    
    if result:
        await conn.execute("DROP TABLE IF EXISTS beatmap_cache;")
        logger.info("Migration 0003: Dropped beatmap_cache table")
    else:
        logger.info("Migration 0003: beatmap_cache does not exist, nothing to drop")


async def migrate_0004_add_md5_hash_to_beatmap_imports(conn):
    """Add md5_hash column to beatmap_imports table"""
    # Check if column already exists
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = 'beatmap_imports' AND column_name = 'md5_hash'
        );
    """)
    
    if not result:
        await conn.execute("""
            ALTER TABLE beatmap_imports ADD COLUMN md5_hash TEXT UNIQUE;
        """)
        logger.info("Migration 0004: Added md5_hash column to beatmap_imports")
    else:
        logger.info("Migration 0004: md5_hash column already exists")


MIGRATIONS = [
    ("0001", migrate_0001_init_schema),
    ("0002", migrate_0002_add_import_tables),
    ("0003", migrate_0003_drop_beatmap_cache),
    ("0004", migrate_0004_add_md5_hash_to_beatmap_imports),
]


async def run_migrations(pool):
    """Run all pending migrations"""
    async with pool.acquire() as conn:
        # Create migrations table if it doesn't exist
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                id TEXT PRIMARY KEY,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Run each migration
        for migration_id, migration_func in MIGRATIONS:
            executed = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM migrations WHERE id = $1);",
                migration_id
            )
            
            if not executed:
                logger.info(f"Running migration {migration_id}")
                try:
                    await migration_func(conn)
                    await conn.execute(
                        "INSERT INTO migrations (id) VALUES ($1);",
                        migration_id
                    )
                    logger.info(f"Migration {migration_id} completed successfully")
                except Exception as e:
                    logger.error(f"Migration {migration_id} failed: {e}")
                    raise
            else:
                logger.info(f"Migration {migration_id} already executed")
