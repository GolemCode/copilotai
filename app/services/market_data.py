"""
Collecte et préparation des cours de marché (point d'entrée unique pour l'API, le pipeline et les scripts).

Trois règles financières sont appliquées ici :
1. On stocke le cours BRUT (close_price, pour valoriser les positions) ET le cours AJUSTÉ des
   dividendes et splits (adjusted_close, pour calculer des rendements totaux).
2. Le cours ajusté de Yahoo est recalculé rétroactivement à chaque dividende : un téléchargement
   incrémental n'est recollé à l'historique que si son facteur d'ajustement est identique à celui
   déjà stocké. Sinon (nouveau dividende/split, ou trou dans les données), l'historique est
   intégralement re-téléchargé.
3. Tous les prix sont convertis dans la devise de référence de l'investisseur (EUR) avant de
   calculer poids et rendements : un actif coté en USD porte aussi le risque de change EUR/USD.
"""
import asyncio
import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import polars as pl

logger = logging.getLogger(__name__)

BASE_CURRENCY = "EUR"
# Catégories d'actifs non cotés déclarées par l'utilisateur : aucune source de cours,
# valorisées au prix de transaction saisi (valeur comptable).
NON_LISTED_TICKERS = {"SCPI-EUR", "PE-EUR", "CASH-EUR"}
FULL_HISTORY_PERIOD = "3y"
INCREMENTAL_PERIOD = "5d"
# Écart relatif toléré entre cours ajusté stocké et cours ajusté frais sur une même date.
# Au-delà, un dividende ou un split a modifié le facteur d'ajustement de tout l'historique.
ADJUSTMENT_TOLERANCE = 1e-4

# Devise de cotation déduite du suffixe de place boursière Yahoo (repli si la base n'en a pas)
_SUFFIX_CURRENCY = {
    ".PA": "EUR", ".DE": "EUR", ".F": "EUR", ".AS": "EUR", ".MI": "EUR", ".MC": "EUR",
    ".BR": "EUR", ".LS": "EUR", ".VI": "EUR", ".HE": "EUR", ".IR": "EUR",
    ".L": "GBp", ".SW": "CHF", ".TO": "CAD", ".T": "JPY", ".HK": "HKD",
}


# ---------------------------------------------------------------------------
# Devises
# ---------------------------------------------------------------------------

def is_fx_ticker(ticker: str) -> bool:
    return ticker.upper().endswith("=X")


def infer_currency(ticker: str) -> Optional[str]:
    """Devise de cotation probable d'après le suffixe Yahoo (sans suffixe : place américaine, USD)."""
    t = ticker.upper()
    if is_fx_ticker(t):
        return None
    for suffix, currency in _SUFFIX_CURRENCY.items():
        if t.endswith(suffix.upper()):
            return currency
    return "USD"


def normalize_currency(raw: Optional[str]) -> Tuple[str, float]:
    """
    Renvoie (devise ISO, facteur d'échelle). Londres cote souvent en pence (GBp / GBX) :
    on convertit alors en livres (x 0,01) avant d'appliquer le taux de change.
    """
    if not raw:
        return BASE_CURRENCY, 1.0
    if raw in ("GBp", "GBX"):
        return "GBP", 0.01
    return raw.upper(), 1.0


def fx_ticker_for(currency: str) -> str:
    """Ticker Yahoo du taux EUR/devise : EURUSD=X = nombre d'USD pour 1 EUR."""
    return f"{BASE_CURRENCY}{currency}=X"


def required_fx_tickers(currencies: Dict[str, Optional[str]]) -> List[str]:
    needed = set()
    for raw in currencies.values():
        currency, _ = normalize_currency(raw)
        if currency != BASE_CURRENCY:
            needed.add(fx_ticker_for(currency))
    return sorted(needed)


# ---------------------------------------------------------------------------
# Téléchargement Yahoo Finance
# ---------------------------------------------------------------------------

def parse_download_frame(df: Optional[pd.DataFrame]) -> List[Tuple[date, float, float, int]]:
    """
    Transforme un DataFrame yfinance (auto_adjust=False) d'un seul ticker en lignes
    (date, cours brut, cours ajusté, volume), en écartant les cotations invalides.
    """
    if df is None or df.empty or "Close" not in df.columns:
        return []
    frame = df.reset_index()
    date_col = "Date" if "Date" in frame.columns else frame.columns[0]
    close = pd.to_numeric(frame["Close"], errors="coerce")
    adj = pd.to_numeric(frame["Adj Close"], errors="coerce") if "Adj Close" in frame.columns else close
    volume = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0) if "Volume" in frame.columns else 0

    clean = pd.DataFrame({"date": frame[date_col], "close": close, "adj": adj, "volume": volume})
    clean = clean.dropna(subset=["date", "close", "adj"])
    clean = clean[(clean["close"] > 0) & (clean["adj"] > 0)]

    return [
        (
            row.date.date() if hasattr(row.date, "date") else row.date,
            float(row.close),
            float(row.adj),
            int(row.volume),
        )
        for row in clean.itertuples(index=False)
    ]


def _download_sync(tickers: Sequence[str], period: str) -> Dict[str, List[Tuple[date, float, float, int]]]:
    import yfinance as yf

    if not tickers:
        return {}
    # auto_adjust=False : on récupère à la fois "Close" (brut) et "Adj Close" (dividendes réinvestis)
    raw = yf.download(
        list(tickers), period=period, auto_adjust=False, actions=False,
        group_by="ticker", progress=False, threads=True,
    )
    result: Dict[str, List[Tuple[date, float, float, int]]] = {}
    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    result[ticker] = []
                    continue
                frame = raw[ticker]
            else:
                frame = raw
            result[ticker] = parse_download_frame(frame)
        except Exception as e:
            logger.warning(f"Lecture des cours impossible pour {ticker} : {e}")
            result[ticker] = []
    return result


async def download_history(tickers: Sequence[str], period: str) -> Dict[str, List[Tuple[date, float, float, int]]]:
    """Téléchargement groupé, exécuté hors de la boucle asyncio (yfinance est bloquant)."""
    return await asyncio.to_thread(_download_sync, list(tickers), period)


# ---------------------------------------------------------------------------
# Persistance PostgreSQL
# ---------------------------------------------------------------------------

SCHEMA_MIGRATIONS = """
ALTER TABLE assets ADD COLUMN IF NOT EXISTS currency VARCHAR(5);

-- Fiches fonds / actions scrapées (Yahoo Finance + JustETF)
CREATE TABLE IF NOT EXISTS fund_profiles (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    isin VARCHAR(12),
    quote_type VARCHAR(20),
    name VARCHAR(200),
    fund_family VARCHAR(150),
    category VARCHAR(150),
    description TEXT,
    description_fr TEXT,
    total_assets DOUBLE PRECISION,
    total_assets_currency VARCHAR(5),
    expense_ratio_pct DOUBLE PRECISION,
    sources TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Principales positions (look-through)
CREATE TABLE IF NOT EXISTS fund_holdings (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    rank SMALLINT NOT NULL,
    symbol VARCHAR(20),
    name VARCHAR(200) NOT NULL,
    weight_pct DOUBLE PRECISION NOT NULL,
    source VARCHAR(30) NOT NULL,
    PRIMARY KEY (asset_id, rank)
);

-- Répartitions par pays et par secteur
CREATE TABLE IF NOT EXISTS fund_exposures (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind VARCHAR(10) NOT NULL CHECK (kind IN ('country', 'sector')),
    label VARCHAR(120) NOT NULL,
    weight_pct DOUBLE PRECISION NOT NULL,
    source VARCHAR(30) NOT NULL,
    PRIMARY KEY (asset_id, kind, label)
);

-- Portefeuille de l'utilisateur et journal des opérations
CREATE TABLE IF NOT EXISTS portfolios (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    base_currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id SERIAL PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker VARCHAR(15) NOT NULL,
    trade_date DATE NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
    price_eur DOUBLE PRECISION NOT NULL CHECK (price_eur >= 0),
    price_source VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portfolio_tx ON portfolio_transactions(portfolio_id, ticker, trade_date);
"""


async def ensure_schema(conn) -> None:
    """Migration idempotente : ajoute colonnes et tables aux bases créées avec un schéma plus ancien."""
    await conn.execute(SCHEMA_MIGRATIONS)


async def ensure_assets(
    conn,
    tickers: Iterable[str],
    metadata: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Crée les actifs manquants (avec leur devise) et renvoie le mapping ticker -> id."""
    metadata = metadata or {}
    ids: Dict[str, int] = {}
    for raw_ticker in tickers:
        ticker = raw_ticker.strip().upper()
        meta = metadata.get(ticker, {})
        fx = is_fx_ticker(ticker)
        currency = meta.get("currency") or infer_currency(ticker)
        asset_id = await conn.fetchval("""
            INSERT INTO assets (ticker, name, sector, asset_class, currency)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticker) DO UPDATE SET
                name = CASE WHEN $6 THEN EXCLUDED.name ELSE assets.name END,
                sector = CASE WHEN $6 THEN COALESCE(EXCLUDED.sector, assets.sector) ELSE assets.sector END,
                asset_class = CASE WHEN $6 THEN COALESCE(EXCLUDED.asset_class, assets.asset_class) ELSE assets.asset_class END,
                currency = COALESCE(assets.currency, EXCLUDED.currency)
            RETURNING id;
        """,
            ticker,
            meta.get("name") or (f"Taux de change {ticker[:6]}" if fx else f"Actif {ticker}"),
            meta.get("sector") or ("Devises" if fx else "Général"),
            meta.get("asset_class") or ("Devise" if fx else "Actions"),
            currency,
            bool(meta),  # n'écrase nom / secteur / classe que si des métadonnées explicites sont fournies
        )
        ids[ticker] = asset_id
    return ids


async def _upsert_rows(conn, asset_id: int, rows: List[Tuple[date, float, float, int]]) -> int:
    if not rows:
        return 0
    await conn.executemany("""
        INSERT INTO market_data (asset_id, market_date, close_price, adjusted_close, volume)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (asset_id, market_date)
        DO UPDATE SET close_price = EXCLUDED.close_price,
                      adjusted_close = EXCLUDED.adjusted_close,
                      volume = EXCLUDED.volume;
    """, [(asset_id, d, c, a, v) for d, c, a, v in rows])
    return len(rows)


async def _replace_history(conn, asset_id: int, rows: List[Tuple[date, float, float, int]]) -> int:
    """Remplace tout l'historique d'un actif, dans une transaction (pas de mélange de facteurs)."""
    if not rows:
        return 0
    async with conn.transaction():
        await conn.execute("DELETE FROM market_data WHERE asset_id = $1;", asset_id)
        return await _upsert_rows(conn, asset_id, rows)


def needs_full_refresh(
    stored_adjusted: Dict[date, float],
    fresh_rows: List[Tuple[date, float, float, int]],
    tolerance: float = ADJUSTMENT_TOLERANCE,
) -> bool:
    """
    Indique si l'historique stocké doit être re-téléchargé avant d'y ajouter des cours frais.

    - Aucune date commune : actif vierge ou trou de données (pipeline non exécuté plusieurs jours).
    - Cours ajusté différent sur une date commune : Yahoo a appliqué un nouveau dividende/split à
      tout l'historique ; recoller tel quel effacerait ce dividende des rendements.
    """
    overlap = [(stored_adjusted[d], adj) for d, _, adj, _ in fresh_rows if d in stored_adjusted]
    if not overlap:
        return True
    return any(abs(adj / stored - 1.0) > tolerance for stored, adj in overlap)


async def sync_market_data(conn, ticker_ids: Dict[str, int], full: bool = False) -> Dict[str, Any]:
    """
    Met à jour les cours d'un ensemble d'actifs.
    full=False : téléchargement incrémental (5 jours), avec re-téléchargement complet si nécessaire.
    full=True  : historique complet (3 ans) pour tous les actifs.
    """
    tickers = list(ticker_ids)
    upserted = 0
    to_refresh: List[str] = list(tickers) if full else []
    failed: List[str] = []

    if not full:
        fresh = await download_history(tickers, INCREMENTAL_PERIOD)
        for ticker in tickers:
            rows = fresh.get(ticker) or []
            if not rows:
                failed.append(ticker)
                continue
            first_date = min(r[0] for r in rows)
            stored = await conn.fetch(
                "SELECT market_date, adjusted_close FROM market_data WHERE asset_id = $1 AND market_date >= $2;",
                ticker_ids[ticker], first_date,
            )
            stored_adjusted = {r["market_date"]: float(r["adjusted_close"]) for r in stored}
            if needs_full_refresh(stored_adjusted, rows):
                to_refresh.append(ticker)
            else:
                upserted += await _upsert_rows(conn, ticker_ids[ticker], rows)

    if to_refresh:
        history = await download_history(to_refresh, FULL_HISTORY_PERIOD)
        for ticker in to_refresh:
            rows = history.get(ticker) or []
            if not rows:
                if ticker not in failed:
                    failed.append(ticker)
                continue
            upserted += await _replace_history(conn, ticker_ids[ticker], rows)

    return {
        "records_upserted": upserted,
        "full_refresh": [t for t in to_refresh if t not in failed],
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Lecture : panel de prix convertis en EUR
# ---------------------------------------------------------------------------

def build_base_currency_panel(
    records: List[Dict[str, Any]],
    tickers: Sequence[str],
    currencies: Dict[str, Optional[str]],
) -> Tuple[pl.DataFrame, pl.DataFrame]:
    """
    Construit deux tableaux larges (market_date + une colonne par ticker), en EUR :
    - cours ajustés (pour les rendements), - cours bruts (pour la valorisation).

    Chaque prix en devise C est divisé par le taux EUR/C du même jour (ou du dernier jour
    coté avant, via une jointure asof). Les jours fériés d'une place sont comblés par le dernier
    cours connu (rendement nul ce jour-là, rattrapé le jour suivant).
    """
    fx_tickers = required_fx_tickers({t: currencies.get(t) for t in tickers})
    df = (
        pl.DataFrame(records)
        .with_columns(pl.col("close_price").cast(pl.Float64), pl.col("adjusted_close").cast(pl.Float64))
        .with_columns(pl.col("close_price").fill_nan(None), pl.col("adjusted_close").fill_nan(None))
        .unique(subset=["market_date", "ticker"], keep="last")
    )

    present_fx = set(df.filter(pl.col("ticker").is_in(fx_tickers))["ticker"].unique().to_list())
    missing_fx = [fx for fx in fx_tickers if fx not in present_fx]
    if missing_fx:
        raise ValueError(f"Taux de change indisponibles en base : {', '.join(missing_fx)}")

    fx_wide = None
    if fx_tickers:
        fx_wide = (
            df.filter(pl.col("ticker").is_in(fx_tickers))
            .pivot(on="ticker", index="market_date", values="close_price")
            .sort("market_date")
            .fill_null(strategy="forward")
        )

    def to_base(value_col: str) -> pl.DataFrame:
        wide = (
            df.filter(pl.col("ticker").is_in(list(tickers)))
            .pivot(on="ticker", index="market_date", values=value_col)
            .sort("market_date")
        )
        if fx_wide is not None:
            # Taux du jour, ou dernier taux connu avant cette date
            wide = wide.join_asof(fx_wide, on="market_date", strategy="backward")

        exprs = []
        for t in tickers:
            if t not in wide.columns:
                continue
            currency, scale = normalize_currency(currencies.get(t))
            if currency == BASE_CURRENCY:
                exprs.append((pl.col(t) * scale).alias(t))
            else:
                exprs.append((pl.col(t) * scale / pl.col(fx_ticker_for(currency))).alias(t))

        return (
            wide.select([pl.col("market_date")] + exprs)
            .fill_null(strategy="forward")
            .drop_nulls()
        )

    return to_base("adjusted_close"), to_base("close_price")


async def load_base_currency_panel(conn, tickers: Sequence[str], years: int) -> Tuple[pl.DataFrame, pl.DataFrame, Dict[str, Optional[str]]]:
    """
    Charge depuis PostgreSQL les cours des actifs demandés, convertis en EUR.
    Télécharge à la volée un taux de change manquant (première utilisation d'une devise).
    """
    currency_rows = await conn.fetch(
        "SELECT ticker, currency FROM assets WHERE ticker = ANY($1::varchar[]);", list(tickers)
    )
    currencies = {r["ticker"]: (r["currency"] or infer_currency(r["ticker"])) for r in currency_rows}
    fx_tickers = required_fx_tickers(currencies)

    if fx_tickers:
        have_fx = await conn.fetch("""
            SELECT DISTINCT a.ticker FROM market_data m JOIN assets a ON m.asset_id = a.id
            WHERE a.ticker = ANY($1::varchar[]);
        """, fx_tickers)
        missing = [t for t in fx_tickers if t not in {r["ticker"] for r in have_fx}]
        if missing:
            ids = await ensure_assets(conn, missing)
            await sync_market_data(conn, ids, full=True)

    # Marge de 10 jours pour que la jointure asof trouve un taux dès la première date
    records = await conn.fetch("""
        SELECT a.ticker, m.market_date, m.close_price, m.adjusted_close
        FROM market_data m
        JOIN assets a ON m.asset_id = a.id
        WHERE a.ticker = ANY($1::varchar[])
          AND m.market_date >= CURRENT_DATE - make_interval(years => $2) - INTERVAL '10 days'
        ORDER BY m.market_date ASC;
    """, list(tickers) + fx_tickers, int(years))

    if not records:
        return pl.DataFrame(), pl.DataFrame(), currencies

    adjusted, close = build_base_currency_panel([dict(r) for r in records], tickers, currencies)
    return adjusted, close, currencies


# ---------------------------------------------------------------------------
# Taux sans risque
# ---------------------------------------------------------------------------

# ETF monétaire répliquant l'€STR (taux au jour le jour de la BCE) : son rendement sur 12 mois
# glissants sert de taux sans risque en EUR. Biais connu : sous-estimation des frais du fonds (~0,1 %/an).
RISK_FREE_PROXY = "XEON.DE"


def trailing_annual_return(rows: List[Tuple[date, float]]) -> Optional[Tuple[float, date]]:
    """Rendement annualisé sur ~12 mois à partir de (date, cours ajusté) triés par date."""
    if len(rows) < 2:
        return None
    last_date, last_price = rows[-1]
    target = date.fromordinal(last_date.toordinal() - 365)
    base_date, base_price = next(((d, p) for d, p in rows if d >= target), rows[0])
    days = (last_date - base_date).days
    if days < 30 or base_price <= 0:
        return None
    return float((last_price / base_price) ** (365.0 / days) - 1.0), last_date


async def risk_free_rate(conn) -> Dict[str, Any]:
    """Taux sans risque annuel déduit de l'€STR (via XEON.DE en base, téléchargé si absent)."""
    query = """
        SELECT m.market_date, m.adjusted_close FROM market_data m JOIN assets a ON a.id = m.asset_id
        WHERE a.ticker = $1 AND m.market_date >= CURRENT_DATE - INTERVAL '400 days'
        ORDER BY m.market_date;
    """
    rows = await conn.fetch(query, RISK_FREE_PROXY)
    if len(rows) < 2:
        ids = await ensure_assets(conn, [RISK_FREE_PROXY])
        await sync_market_data(conn, ids, full=True)
        rows = await conn.fetch(query, RISK_FREE_PROXY)

    computed = trailing_annual_return([(r["market_date"], float(r["adjusted_close"])) for r in rows])
    if computed is None:
        return {"rate": 0.0, "source": "indisponible (historique €STR absent) : 0 % retenu", "as_of": None}
    rate, as_of = computed
    return {"rate": rate, "source": f"€STR via {RISK_FREE_PROXY} (12 mois glissants)", "as_of": as_of.isoformat()}
