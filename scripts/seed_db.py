import os
import sys
import asyncio

# Ajout de la racine du projet au PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.db_ingestion import seed_database

if __name__ == "__main__":
    print("Démarrage du peuplement de la base relationnelle...")
    asyncio.run(seed_database())
