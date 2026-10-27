import logging
from typing import Any, Dict, List, Optional

from app.services.market_data import BASE_CURRENCY, fx_ticker_for, normalize_currency

logger = logging.getLogger(__name__)

# Sélection éditoriale d'ETF de référence (liquides, frais réduits). Ne contient que des
# métadonnées de classement : nom, émetteur, encours et devise sont lus en base
# (fund_profiles, alimentée par le scraping), jamais saisis à la main.
POPULAR_ETF_CATALOG: List[Dict[str, Any]] = [
    {'ticker': 'CW8.PA', 'category': 'Actions Monde', 'benchmark': 'MSCI World', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'WPEA.PA', 'category': 'Actions Monde', 'benchmark': 'MSCI World', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'VWCE.DE', 'category': 'Actions Monde (All-World)', 'benchmark': 'FTSE All-World', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'PE500.PA', 'category': 'Actions US (S&P 500)', 'benchmark': 'S&P 500', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'ESE.PA', 'category': 'Actions US (S&P 500)', 'benchmark': 'S&P 500', 'pea_eligible': True, 'is_quick_pick': False},
    {'ticker': 'SPY', 'category': 'Actions US (S&P 500)', 'benchmark': 'S&P 500', 'pea_eligible': False, 'is_quick_pick': True},
    {'ticker': 'VOO', 'category': 'Actions US (S&P 500)', 'benchmark': 'S&P 500', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'PUST.PA', 'category': 'Actions US Tech', 'benchmark': 'NASDAQ-100', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'QQQ', 'category': 'Actions US Tech', 'benchmark': 'NASDAQ-100', 'pea_eligible': False, 'is_quick_pick': True},
    {'ticker': 'XLK', 'category': 'Technologie US', 'benchmark': 'Technology Select Sector Index', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'XEON.DE', 'category': 'Monétaire (€STR)', 'benchmark': 'Solactive €STR +8.5bps', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'C3M.PA', 'category': 'Monétaire (Trésorerie Court Terme)', 'benchmark': 'Obligations Souveraines Euro 0-6M', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'ETZ.PA', 'category': 'Actions Europe', 'benchmark': 'STOXX Europe 600', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'MEUD.PA', 'category': 'Actions Europe', 'benchmark': 'STOXX Europe 600', 'pea_eligible': True, 'is_quick_pick': False},
    {'ticker': 'CAC.PA', 'category': 'Actions France', 'benchmark': 'CAC 40', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'PAEEM.PA', 'category': 'Marchés Émergents', 'benchmark': 'MSCI Emerging Markets', 'pea_eligible': True, 'is_quick_pick': True},
    {'ticker': 'EEM', 'category': 'Marchés Émergents', 'benchmark': 'MSCI Emerging Markets', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'GLD', 'category': 'Matières Premières (Or Physique)', 'benchmark': 'LBMA Gold Price PM', 'pea_eligible': False, 'is_quick_pick': True},
    {'ticker': 'TLT', 'category': 'Obligations Souveraines US', 'benchmark': 'ICE U.S. Treasury 20+ Year', 'pea_eligible': False, 'is_quick_pick': True},
    {'ticker': 'AGG', 'category': 'Obligations Agrégées US', 'benchmark': 'Bloomberg U.S. Aggregate', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'IWM', 'category': 'Small Caps US', 'benchmark': 'Russell 2000', 'pea_eligible': False, 'is_quick_pick': False},
    {'ticker': 'XLF', 'category': 'Finance US', 'benchmark': 'Financial Select Sector Index', 'pea_eligible': False, 'is_quick_pick': False},
]

# ETF dont la fiche est collectée et vectorisée par défaut (+ tous ceux du catalogue de référence).
# PABZ.PA, XDPE.PA et BGRN remplacent les anciennes fiches climat rédigées à la main.
RAG_REFERENCE_TICKERS = sorted(set([
    "CW8.PA", "WPEA.PA", "PE500.PA", "PUST.PA", "CAC.PA", "ETZ.PA", "PAEEM.PA",
    "SPY", "QQQ", "GLD", "TLT", "AGG", "XLK", "XLF", "XEON.DE",
    "PABZ.PA", "XDPE.PA", "BGRN",
] + [c["ticker"] for c in POPULAR_ETF_CATALOG]))

MIN_AUM_EUR = 1_000_000_000.0  # seuil d'encours : 1 Md€


async def _latest_fx_rates(conn, currencies: List[str]) -> Dict[str, float]:
    """Dernier taux EUR/devise connu en base (ex. EURUSD=X : USD pour 1 EUR)."""
    fx_tickers = [fx_ticker_for(c) for c in currencies]
    rows = await conn.fetch("""
        SELECT DISTINCT ON (a.ticker) a.ticker, m.close_price
        FROM market_data m JOIN assets a ON a.id = m.asset_id
        WHERE a.ticker = ANY($1::varchar[])
        ORDER BY a.ticker, m.market_date DESC;
    """, fx_tickers)
    by_ticker = {r["ticker"]: float(r["close_price"]) for r in rows}
    return {c: by_ticker[fx_ticker_for(c)] for c in currencies if fx_ticker_for(c) in by_ticker}


async def get_popular_etfs(conn, quick_only: bool = False) -> List[Dict[str, Any]]:
    """
    Catalogue enrichi des données scrapées : nom, émetteur, encours converti en EUR.
    - quick_only=True : sélection courte pour les suggestions rapides de l'interface.
    - aum_eur vaut None si l'encours n'est pas publié ; meets_aum_threshold vaut alors None.
    """
    items = [c for c in POPULAR_ETF_CATALOG if c.get("is_quick_pick") or not quick_only]
    rows = await conn.fetch("""
        SELECT a.ticker, p.name, p.fund_family, p.total_assets, p.total_assets_currency, p.expense_ratio_pct, p.updated_at
        FROM assets a JOIN fund_profiles p ON p.asset_id = a.id
        WHERE a.ticker = ANY($1::varchar[]);
    """, [c["ticker"] for c in items])
    profiles = {r["ticker"]: r for r in rows}

    currencies = sorted({
        normalize_currency(r["total_assets_currency"])[0] for r in rows
        if r["total_assets_currency"] and normalize_currency(r["total_assets_currency"])[0] != BASE_CURRENCY
    })
    fx = await _latest_fx_rates(conn, currencies) if currencies else {}

    result = []
    for item in items:
        p = profiles.get(item["ticker"])
        aum_eur: Optional[float] = None
        if p and p["total_assets"]:
            currency, scale = normalize_currency(p["total_assets_currency"])
            if currency == BASE_CURRENCY:
                aum_eur = p["total_assets"] * scale
            elif currency in fx:
                aum_eur = p["total_assets"] * scale / fx[currency]
        result.append({
            **item,
            "name": p["name"] if p else None,
            "fund_family": p["fund_family"] if p else None,
            "expense_ratio_pct": p["expense_ratio_pct"] if p else None,
            "aum_eur": round(aum_eur, 0) if aum_eur is not None else None,
            "meets_aum_threshold": (aum_eur >= MIN_AUM_EUR) if aum_eur is not None else None,
            "data_updated_at": p["updated_at"].isoformat() if p and p["updated_at"] else None,
        })
    return result
