"""
Crée un portefeuille de DÉMONSTRATION (optionnel) dans la base.

Les quantités sont un exemple ; les prix d'achat ne sont pas inventés : ce sont les cours de
clôture réels (convertis en EUR) aux dates d'achat, lus dans market_data. Le script refuse de
s'exécuter si le portefeuille contient déjà des opérations.
"""
import asyncio
import os
import sys
from datetime import date, timedelta

import asyncpg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import DATABASE_URL
from app.services.market_data import ensure_schema
from app.services.portfolio import PortfolioError, add_transaction, get_default_portfolio_id

# (ticker, quantité, ancienneté de l'achat en jours) : dates relatives pour rester dans l'historique de 3 ans
DEMO_BUYS = [
    ("CW8.PA", 15, 900),
    ("SPY", 10, 700),
    ("QQQ", 8, 500),
    ("GLD", 5, 300),
]


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await ensure_schema(conn)
        pid = await get_default_portfolio_id(conn)
        if await conn.fetchval("SELECT COUNT(*) FROM portfolio_transactions WHERE portfolio_id = $1;", pid):
            print("Le portefeuille contient déjà des opérations : aucune donnée de démonstration ajoutée.")
            return
        for ticker, quantity, days_ago in DEMO_BUYS:
            trade_date = date.today() - timedelta(days=days_ago)
            try:
                await add_transaction(conn, pid, ticker, trade_date, "buy", quantity, price_eur=None)
                print(f"Achat de démonstration : {quantity} {ticker} le {trade_date} au cours de clôture.")
            except PortfolioError as e:
                print(f"{ticker} ignoré : {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
