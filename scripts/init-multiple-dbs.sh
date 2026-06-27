#!/bin/bash
# Arrête le script immédiatement si une commande échoue
set -e

# Se connecte à Postgres en tant que superutilisateur et crée la seconde base
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE airflow_db;
    GRANT ALL PRIVILEGES ON DATABASE airflow_db TO "$POSTGRES_USER";
EOSQL

echo "Base de données airflow_db créée avec succès."