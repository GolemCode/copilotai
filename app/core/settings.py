"""
Configuration lue dans l'environnement (fichier .env chargé par docker-compose) : point unique pour
l'API, le worker Celery, les services et les scripts.
"""
import os

# PostgreSQL (les noms POSTGRES_* de l'image officielle sont acceptés en repli)
DB_USER = os.getenv("DB_USER") or os.getenv("POSTGRES_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD") if os.getenv("DB_PASSWORD") is not None else os.getenv("POSTGRES_PASSWORD", "password")
DB_NAME = os.getenv("DB_NAME") or os.getenv("POSTGRES_DB", "copilot_db")
DB_HOST = os.getenv("DB_HOST", "db")  # nom du service dans docker-compose
DB_PORT = os.getenv("DB_PORT", "5432")

# Même base, trois pilotes : asyncpg (API), SQLAlchemy/psycopg (pgvector via LangChain), psycopg (accès direct)
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
SQLALCHEMY_DATABASE_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
PSYCOPG_CONNINFO = f"dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD} host={DB_HOST} port={DB_PORT}"

# Redis (broker Celery et canal des résultats de simulation)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
