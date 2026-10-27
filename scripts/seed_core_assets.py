import asyncio
import os
import sys

import asyncpg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import DATABASE_URL
from app.services.market_data import ensure_assets, ensure_schema, sync_market_data

CORE_ASSETS = [
    {"ticker": "CW8.PA", "name": "Amundi MSCI World UCITS ETF (PEA)", "sector": "Monde", "asset_class": "Actions"},
    {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "sector": "US Large Cap", "asset_class": "Actions"},
    {"ticker": "QQQ", "name": "Invesco QQQ Trust (Nasdaq 100)", "sector": "Technologie", "asset_class": "Actions"},
    {"ticker": "GLD", "name": "SPDR Gold Shares", "sector": "Matières Premières", "asset_class": "Commodities"},
    {"ticker": "AGG", "name": "iShares Core US Aggregate Bond ETF", "sector": "Obligations", "asset_class": "Fixed Income"},
    {"ticker": "PE500.PA", "name": "Amundi S&P 500 UCITS ETF (PEA)", "sector": "US Large Cap", "asset_class": "Actions"},
    {"ticker": "PUST.PA", "name": "Amundi PEA Nasdaq-100 UCITS ETF", "sector": "Technologie", "asset_class": "Actions"},
    {"ticker": "CAC.PA", "name": "Amundi CAC 40 UCITS ETF", "sector": "France", "asset_class": "Actions"},
    {"ticker": "MC.PA", "name": "LVMH Moët Hennessy Louis Vuitton", "sector": "Luxe", "asset_class": "Actions"},
    {"ticker": "TTE.PA", "name": "TotalEnergies SE", "sector": "Énergie", "asset_class": "Actions"},
    {"ticker": "AIR.PA", "name": "Airbus SE", "sector": "Aéronautique", "asset_class": "Actions"},
    {"ticker": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "sector": "Obligations Long Terme", "asset_class": "Fixed Income"},
    {"ticker": "XLK", "name": "Technology Select Sector SPDR Fund", "sector": "Technologie", "asset_class": "Actions"},
    {"ticker": "XLF", "name": "Financial Select Sector SPDR Fund", "sector": "Finance", "asset_class": "Actions"}
]

async def seed_core():
    print("Connexion à PostgreSQL...")
    conn = await asyncpg.connect(DATABASE_URL)
    print("Connecté.")

    try:
        await ensure_schema(conn)
        metadata = {item["ticker"]: item for item in CORE_ASSETS}
        ticker_ids = await ensure_assets(conn, list(metadata), metadata=metadata)
        # Taux EUR/USD : indispensable pour convertir en EUR les actifs cotés en dollars
        ticker_ids.update(await ensure_assets(conn, ["EURUSD=X"]))

        summary = await sync_market_data(conn, ticker_ids, full=False)
        print(f"{summary['records_upserted']} cotations enregistrées, échecs : {summary['failed'] or 'aucun'}")
        print("Peuplement du référentiel d'actifs terminé.")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(seed_core())
