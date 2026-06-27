CREATE TABLE IF NOT EXISTS assets (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(15) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    sector VARCHAR(100),
    asset_class VARCHAR(50)
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