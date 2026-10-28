import asyncio
import os
import sys

import asyncpg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.settings import DATABASE_URL
from app.services.fund_data import load_fund_snapshot, refresh_fund_data
from app.services.market_data import ensure_schema
from app.services.rag_ingestion import index_fund_documents, purge_legacy_documents
from app.services.etf_screener import RAG_REFERENCE_TICKERS


async def main():
    print("=== Collecte des fiches ETF et indexation vectorielle ===")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await ensure_schema(conn)
        summary = await refresh_fund_data(conn, RAG_REFERENCE_TICKERS)
        snapshots = [s for s in [await load_fund_snapshot(conn, t) for t in summary["refreshed"]] if s]
    finally:
        await conn.close()

    purge_legacy_documents()
    count = index_fund_documents(snapshots)
    print(f"{len(snapshots)} fiches enregistrées en base, {count} segments vectorisés, échecs : {summary['failed'] or 'aucun'}")


if __name__ == "__main__":
    asyncio.run(main())
