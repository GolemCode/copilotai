-- Exécuté une seule fois par l'image Postgres, connecté à la base métier ($POSTGRES_DB) :
-- aucun nom de base ni mot de passe n'est écrit ici.

-- Base dédiée aux métadonnées d'Airflow (accès avec l'utilisateur défini dans .env)
CREATE DATABASE airflow_db;

-- Activation de l'extension vectorielle pour le RAG
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS assets (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(15) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    sector VARCHAR(100),
    asset_class VARCHAR(50),
    -- Devise de cotation (EUR, USD, GBp...) : nécessaire pour convertir les prix en EUR
    currency VARCHAR(5)
);

CREATE TABLE IF NOT EXISTS market_data (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    market_date DATE NOT NULL,
    close_price DOUBLE PRECISION NOT NULL,
    adjusted_close DOUBLE PRECISION NOT NULL,
    volume BIGINT,
    PRIMARY KEY (asset_id, market_date)
);

CREATE INDEX IF NOT EXISTS idx_market_data_date ON market_data(market_date);
CREATE INDEX IF NOT EXISTS idx_market_data_asset_date ON market_data(asset_id, market_date DESC);

-- Les tables fund_profiles, fund_holdings, fund_exposures, portfolios et portfolio_transactions
-- sont créées au démarrage de l'API par app/services/market_data.py (SCHEMA_MIGRATIONS),
-- afin que les bases déjà initialisées soient migrées de la même façon.