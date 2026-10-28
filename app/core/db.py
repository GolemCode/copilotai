import asyncpg
from typing import AsyncGenerator

from app.core.settings import DATABASE_URL

class DatabaseManager:
    def __init__(self):
        self.pool = None

    async def connect(self):
        """Initialise le pool de connexions lors du démarrage de l'API."""
        self.pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=60.0
        )
        # Migration idempotente des bases créées avant l'ajout de la colonne devise
        from app.services.market_data import ensure_schema
        async with self.pool.acquire() as conn:
            await ensure_schema(conn)

    async def disconnect(self):
        """Ferme proprement les connexions lors de l'arrêt du serveur."""
        if self.pool:
            await self.pool.close()

db_manager = DatabaseManager()

async def get_db_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Dépendance FastAPI pour fournir une connexion isolée à chaque route."""
    if db_manager.pool is None:
        raise RuntimeError("Le pool de base de données n'est pas initialisé.")
    
    async with db_manager.pool.acquire() as connection:
        yield connection