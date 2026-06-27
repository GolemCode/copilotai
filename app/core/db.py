import asyncpg
import os
from typing import AsyncGenerator

# Récupération stricte des variables d'environnement injectées par Docker
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_NAME = os.getenv("DB_NAME", "copilot_db")
DB_HOST = os.getenv("DB_HOST", "db") # 'db' est le nom du service dans docker-compose
DB_PORT = os.getenv("DB_PORT", "5432")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

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