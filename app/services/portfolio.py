"""
Portefeuille de l'utilisateur et journal de ses opérations (tables portfolios / portfolio_transactions).

Conventions :
- Les prix de transaction sont exprimés en EUR (montant réellement payé ou encaissé par l'investisseur).
- Si l'utilisateur ne saisit pas de prix pour un actif coté, on enregistre le cours de clôture
  du jour de l'opération (ou du dernier jour coté avant), converti en EUR, et on le signale
  (price_source = 'market_close').
- Les actifs non cotés (SCPI, private equity, liquidités) n'ont pas de cours : le prix est obligatoire.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.services.market_data import (
    BASE_CURRENCY,
    NON_LISTED_TICKERS,
    ensure_assets,
    fx_ticker_for,
    infer_currency,
    normalize_currency,
    sync_market_data,
)

DEFAULT_PORTFOLIO_NAME = "Portefeuille principal"


class PortfolioError(ValueError):
    """Opération refusée (message destiné à l'utilisateur)."""


async def get_default_portfolio_id(conn) -> int:
    """Application mono-utilisateur : un portefeuille par défaut, créé au premier accès."""
    pid = await conn.fetchval("SELECT id FROM portfolios ORDER BY id LIMIT 1;")
    if pid is None:
        pid = await conn.fetchval(
            "INSERT INTO portfolios (name, base_currency) VALUES ($1, $2) RETURNING id;",
            DEFAULT_PORTFOLIO_NAME, BASE_CURRENCY,
        )
    return pid


def holdings_never_negative(transactions: List[Tuple[date, str, float]]) -> bool:
    """Vérifie, dans l'ordre chronologique, qu'aucune vente ne dépasse la quantité détenue à sa date."""
    held = 0.0
    # À date égale, les achats sont comptés avant les ventes
    for _, side, qty in sorted(transactions, key=lambda t: (t[0], t[1] != "buy")):
        held += qty if side == "buy" else -qty
        if held < -1e-9:
            return False
    return True


async def market_close_eur(conn, ticker: str, trade_date: date) -> Optional[float]:
    """Cours de clôture brut à la date (ou au dernier jour coté avant), converti en EUR."""
    row = await conn.fetchrow("""
        SELECT a.currency, m.close_price, m.market_date
        FROM market_data m JOIN assets a ON a.id = m.asset_id
        WHERE a.ticker = $1 AND m.market_date <= $2
        ORDER BY m.market_date DESC LIMIT 1;
    """, ticker, trade_date)
    if row is None:
        return None
    currency, scale = normalize_currency(row["currency"] or infer_currency(ticker))
    price = float(row["close_price"]) * scale
    if currency == BASE_CURRENCY:
        return price
    fx = await conn.fetchval("""
        SELECT m.close_price FROM market_data m JOIN assets a ON a.id = m.asset_id
        WHERE a.ticker = $1 AND m.market_date <= $2
        ORDER BY m.market_date DESC LIMIT 1;
    """, fx_ticker_for(currency), row["market_date"])
    return price / float(fx) if fx else None


async def load_portfolio(conn, portfolio_id: int) -> Dict[str, Any]:
    meta = await conn.fetchrow("SELECT id, name, base_currency FROM portfolios WHERE id = $1;", portfolio_id)
    rows = await conn.fetch("""
        SELECT id, ticker, trade_date, side, quantity, price_eur, price_source
        FROM portfolio_transactions
        WHERE portfolio_id = $1
        ORDER BY MIN(created_at) OVER (PARTITION BY ticker), ticker, trade_date DESC, id DESC;
    """, portfolio_id)
    assets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        assets.setdefault(r["ticker"], {"ticker": r["ticker"], "transactions": []})["transactions"].append({
            "id": r["id"],
            "date": r["trade_date"].isoformat(),
            "shares": r["quantity"],
            "price": r["price_eur"],
            "type": r["side"],
            "price_source": r["price_source"],
        })
    return {
        "id": meta["id"],
        "name": meta["name"],
        "base_currency": meta["base_currency"],
        "assets": list(assets.values()),
    }


async def add_transaction(
    conn,
    portfolio_id: int,
    ticker: str,
    trade_date: date,
    side: str,
    quantity: float,
    price_eur: Optional[float],
) -> int:
    ticker = ticker.strip().upper()
    if trade_date > date.today():
        raise PortfolioError("La date d'une opération ne peut pas être dans le futur.")

    price_source = "user"
    if price_eur is None:
        if ticker in NON_LISTED_TICKERS:
            raise PortfolioError("Actif non coté : le prix unitaire doit être saisi.")
        price_eur = await market_close_eur(conn, ticker, trade_date)
        if price_eur is None:
            # Actif jamais téléchargé : on récupère son historique puis on réessaie
            ids = await ensure_assets(conn, [ticker])
            await sync_market_data(conn, ids, full=True)
            price_eur = await market_close_eur(conn, ticker, trade_date)
        if price_eur is None:
            raise PortfolioError(
                f"Aucun cours disponible pour {ticker} au {trade_date.isoformat()} : saisissez le prix payé."
            )
        price_source = "market_close"

    existing = await conn.fetch("""
        SELECT trade_date, side, quantity FROM portfolio_transactions
        WHERE portfolio_id = $1 AND ticker = $2;
    """, portfolio_id, ticker)
    history = [(r["trade_date"], r["side"], r["quantity"]) for r in existing] + [(trade_date, side, quantity)]
    if not holdings_never_negative(history):
        raise PortfolioError("Vente refusée : la quantité vendue dépasse la quantité détenue à cette date.")

    return await conn.fetchval("""
        INSERT INTO portfolio_transactions (portfolio_id, ticker, trade_date, side, quantity, price_eur, price_source)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id;
    """, portfolio_id, ticker, trade_date, side, quantity, round(float(price_eur), 4), price_source)


async def delete_transaction(conn, portfolio_id: int, transaction_id: int) -> None:
    row = await conn.fetchrow(
        "SELECT ticker FROM portfolio_transactions WHERE id = $1 AND portfolio_id = $2;", transaction_id, portfolio_id
    )
    if row is None:
        raise PortfolioError("Opération introuvable.")
    remaining = await conn.fetch("""
        SELECT trade_date, side, quantity FROM portfolio_transactions
        WHERE portfolio_id = $1 AND ticker = $2 AND id <> $3;
    """, portfolio_id, row["ticker"], transaction_id)
    if not holdings_never_negative([(r["trade_date"], r["side"], r["quantity"]) for r in remaining]):
        raise PortfolioError("Suppression refusée : une vente ultérieure dépasserait alors la quantité détenue.")
    await conn.execute("DELETE FROM portfolio_transactions WHERE id = $1;", transaction_id)


async def delete_asset(conn, portfolio_id: int, ticker: str) -> None:
    await conn.execute(
        "DELETE FROM portfolio_transactions WHERE portfolio_id = $1 AND ticker = $2;", portfolio_id, ticker.strip().upper()
    )
