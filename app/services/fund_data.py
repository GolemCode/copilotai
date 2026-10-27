"""
Fiches descriptives des actifs (ETF et actions) : scraping Yahoo Finance + JustETF, stockage PostgreSQL.

Sources et priorités :
- Profil (nom, émetteur, catégorie, encours, frais, description) : Yahoo Finance.
- Principales positions : Yahoo Finance (ETF US) sinon fiche JustETF par ISIN (ETF UCITS européens).
- Pays / secteurs : JustETF ; pour une action, pays et secteur de l'entreprise (Yahoo, 100 %).
Aucune valeur de repli inventée : une donnée non publiée par les sources reste absente.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import requests
from bs4 import BeautifulSoup

from app.services.market_data import NON_LISTED_TICKERS, ensure_assets

logger = logging.getLogger(__name__)

# Référentiel d'identifiants (ISIN) des ETF suivis : nécessaire pour interroger JustETF,
# Yahoo ne renvoyant pas toujours l'ISIN. Ce sont des identifiants, pas des données de marché.
ISIN_REFERENCE = {
    "CW8.PA": "LU1681043599", "WPEA.PA": "IE0002XZSHO1", "PE500.PA": "FR0013412283",
    "ESE.PA": "FR0011550185", "PUST.PA": "FR0011871110", "XEON.DE": "LU0290358497",
    "C3M.PA": "FR0010754200", "ETZ.PA": "LU0908500753", "MEUD.PA": "FR0010261198",
    "CAC.PA": "FR0007054316", "PAEEM.PA": "FR0013412020", "VWCE.DE": "IE00BK5BQT80",
    "SPY": "US78462F1030", "VOO": "US9229083632", "QQQ": "US46090E1038", "AGG": "US4642872265",
    "TLT": "US4642874329", "GLD": "US78463V1070", "IWM": "US4642876555", "XLK": "US81369Y8030",
    "XLF": "US81369Y6059", "EEM": "US4642874659",
}

# Libellés français des clés de secteurs renvoyées par Yahoo (traduction, pas de données)
_YAHOO_SECTOR_LABELS = {
    "realestate": "Immobilier", "consumer_cyclical": "Consommation cyclique",
    "basic_materials": "Matériaux de base", "consumer_defensive": "Consommation de base",
    "technology": "Technologie", "communication_services": "Services de communication",
    "financial_services": "Services financiers", "utilities": "Services publics",
    "industrials": "Industrie", "energy": "Énergie", "healthcare": "Santé",
}

# Au-delà, une fiche lue à la demande est re-collectée (compositions et encours évoluent)
FUND_DATA_MAX_AGE_DAYS = 7

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def parse_percent(text: str) -> Optional[float]:
    try:
        return float(text.replace("%", "").replace(" ", "").replace(",", ".").strip())
    except ValueError:
        return None


def _two_column_rows(table) -> List[Dict[str, Any]]:
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) == 2 and "%" in cells[1]:
            pct = parse_percent(cells[1])
            if pct is not None and pct > 0:
                rows.append({"name": cells[0], "weight_pct": round(pct, 2)})
    return rows


def parse_justetf_profile(html: str) -> Dict[str, Any]:
    """Extrait d'une fiche JustETF (version française) : positions, pays, secteurs et description."""
    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, Any] = {"holdings": [], "countries": [], "sectors": [], "description": ""}

    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = heading.get_text(strip=True).lower()
        table = heading.find_next("table")
        if table is None:
            continue
        if ("principales positions" in title or "top 10" in title) and not result["holdings"]:
            result["holdings"] = _two_column_rows(table)
        elif ("pays" in title or "countries" in title) and not result["countries"]:
            result["countries"] = _two_column_rows(table)
        elif ("secteurs" in title or "sectors" in title) and not result["sectors"]:
            result["sectors"] = _two_column_rows(table)

    desc = soup.find("div", class_="etf-profile-description__contents")
    if desc:
        result["description"] = desc.get_text(" ", strip=True)
    return result


def _scrape_justetf(isin: str) -> Dict[str, Any]:
    url = f"https://www.justetf.com/fr/etf-profile.html?isin={isin}"
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}
        return parse_justetf_profile(resp.text)
    except Exception as e:
        logger.warning(f"JustETF inaccessible pour {isin} : {e}")
        return {}


def _yahoo_holdings(funds_data) -> List[Dict[str, Any]]:
    holdings = []
    try:
        top = getattr(funds_data, "top_holdings", None)
        if top is None or top.empty:
            return []
        for symbol, row in top.head(10).iterrows():
            raw = row.get("Holding Percent", row.get("holdingPercent"))
            try:
                weight = float(raw) * 100.0  # Yahoo renvoie une fraction (0.071 = 7,1 %)
            except (TypeError, ValueError):
                continue
            holdings.append({"symbol": str(symbol), "name": str(row.get("Name", symbol)), "weight_pct": round(weight, 2)})
    except Exception as e:
        logger.warning(f"Positions Yahoo illisibles : {e}")
    return holdings


def _yahoo_sectors(funds_data) -> List[Dict[str, Any]]:
    try:
        weights = getattr(funds_data, "sector_weightings", None) or {}
        return [
            {"name": _YAHOO_SECTOR_LABELS.get(k, k), "weight_pct": round(float(v) * 100.0, 2)}
            for k, v in weights.items() if v and float(v) > 0
        ]
    except Exception:
        return []


def scrape_fund_snapshot(ticker: str) -> Dict[str, Any]:
    """Collecte (bloquante) de la fiche complète d'un actif. Les champs non publiés restent vides."""
    import yfinance as yf

    yt = yf.Ticker(ticker)
    info = yt.info or {}
    quote_type = (info.get("quoteType") or "").upper()
    sources = ["yahoo"]

    snapshot: Dict[str, Any] = {
        "ticker": ticker,
        "profile": {
            "isin": ISIN_REFERENCE.get(ticker),
            "quote_type": quote_type or None,
            "name": info.get("longName") or info.get("shortName"),
            "fund_family": info.get("fundFamily"),
            "category": info.get("category") or info.get("industry"),
            "description": info.get("longBusinessSummary"),
            "total_assets": float(info["totalAssets"]) if isinstance(info.get("totalAssets"), (int, float)) else None,
            "total_assets_currency": info.get("currency"),
            # netExpenseRatio est exprimé en pourcentage par Yahoo (0.0945 = 0,0945 %)
            "expense_ratio_pct": float(info["netExpenseRatio"]) if isinstance(info.get("netExpenseRatio"), (int, float)) else None,
        },
        "holdings": [],
        "holdings_source": None,
        "countries": [],
        "countries_source": None,
        "sectors": [],
        "sectors_source": None,
    }

    if quote_type == "EQUITY":
        # Une action est sa propre et unique position ; pays et secteur = ceux de l'entreprise
        name = snapshot["profile"]["name"] or ticker
        snapshot["holdings"] = [{"symbol": ticker, "name": name, "weight_pct": 100.0}]
        snapshot["holdings_source"] = "yahoo"
        if info.get("country"):
            snapshot["countries"] = [{"name": info["country"], "weight_pct": 100.0}]
            snapshot["countries_source"] = "yahoo"
        if info.get("sector"):
            snapshot["sectors"] = [{"name": info["sector"], "weight_pct": 100.0}]
            snapshot["sectors_source"] = "yahoo"
    else:
        funds_data = getattr(yt, "funds_data", None)
        if funds_data is not None:
            holdings = _yahoo_holdings(funds_data)
            if len(holdings) >= 3:
                snapshot["holdings"], snapshot["holdings_source"] = holdings, "yahoo"
            sectors = _yahoo_sectors(funds_data)
            if sectors:
                snapshot["sectors"], snapshot["sectors_source"] = sectors, "yahoo"

        isin = snapshot["profile"]["isin"]
        if not isin:
            try:
                candidate = yt.isin
                if candidate and candidate != "-" and len(candidate) == 12:
                    isin = snapshot["profile"]["isin"] = candidate
            except Exception:
                pass

        if isin:
            justetf = _scrape_justetf(isin)
            if justetf:
                sources.append("justetf")
                if not snapshot["holdings"] and len(justetf.get("holdings", [])) >= 3:
                    snapshot["holdings"] = [{"symbol": None, **h} for h in justetf["holdings"]]
                    snapshot["holdings_source"] = "justetf"
                if justetf.get("countries"):
                    snapshot["countries"], snapshot["countries_source"] = justetf["countries"], "justetf"
                if justetf.get("sectors"):
                    snapshot["sectors"], snapshot["sectors_source"] = justetf["sectors"], "justetf"
                if not snapshot["profile"]["description"] and justetf.get("description"):
                    snapshot["profile"]["description"] = justetf["description"]

    snapshot["profile"]["sources"] = ",".join(sources)
    return snapshot


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------

async def save_fund_snapshot(conn, asset_id: int, snapshot: Dict[str, Any]) -> None:
    p = snapshot["profile"]
    async with conn.transaction():
        await conn.execute("""
            INSERT INTO fund_profiles (asset_id, isin, quote_type, name, fund_family, category, description,
                                       total_assets, total_assets_currency, expense_ratio_pct, sources, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
            ON CONFLICT (asset_id) DO UPDATE SET
                isin = EXCLUDED.isin, quote_type = EXCLUDED.quote_type, name = EXCLUDED.name,
                fund_family = EXCLUDED.fund_family, category = EXCLUDED.category,
                description = EXCLUDED.description,
                -- la description française générée n'est conservée que si la source n'a pas changé
                description_fr = CASE WHEN fund_profiles.description IS DISTINCT FROM EXCLUDED.description
                                      THEN NULL ELSE fund_profiles.description_fr END,
                total_assets = EXCLUDED.total_assets, total_assets_currency = EXCLUDED.total_assets_currency,
                expense_ratio_pct = EXCLUDED.expense_ratio_pct, sources = EXCLUDED.sources, updated_at = NOW();
        """, asset_id, p.get("isin"), p.get("quote_type"), p.get("name"), p.get("fund_family"), p.get("category"),
            p.get("description"), p.get("total_assets"), p.get("total_assets_currency"),
            p.get("expense_ratio_pct"), p.get("sources"))

        await conn.execute("DELETE FROM fund_holdings WHERE asset_id = $1;", asset_id)
        if snapshot["holdings"]:
            await conn.executemany("""
                INSERT INTO fund_holdings (asset_id, rank, symbol, name, weight_pct, source)
                VALUES ($1, $2, $3, $4, $5, $6);
            """, [
                (asset_id, rank, h.get("symbol"), h["name"][:200], h["weight_pct"], snapshot["holdings_source"])
                for rank, h in enumerate(snapshot["holdings"], 1)
            ])

        await conn.execute("DELETE FROM fund_exposures WHERE asset_id = $1;", asset_id)
        exposures = []
        for kind, key in (("country", "countries"), ("sector", "sectors")):
            merged: Dict[str, float] = {}
            for item in snapshot[key]:
                label = item["name"][:120]
                merged[label] = merged.get(label, 0.0) + item["weight_pct"]
            exposures += [(asset_id, kind, label, pct, snapshot[f"{key}_source"]) for label, pct in merged.items()]
        if exposures:
            await conn.executemany("""
                INSERT INTO fund_exposures (asset_id, kind, label, weight_pct, source)
                VALUES ($1, $2, $3, $4, $5);
            """, exposures)


async def refresh_fund_data(conn, tickers: Sequence[str]) -> Dict[str, Any]:
    """Scrape et enregistre la fiche de chaque actif. Renvoie le bilan (réussites / échecs)."""
    ticker_ids = await ensure_assets(conn, tickers)
    refreshed, failed = [], []
    for ticker, asset_id in ticker_ids.items():
        try:
            snapshot = await asyncio.to_thread(scrape_fund_snapshot, ticker)
            await save_fund_snapshot(conn, asset_id, snapshot)
            refreshed.append(ticker)
        except Exception as e:
            logger.warning(f"Fiche non collectée pour {ticker} : {e}")
            failed.append(ticker)
    return {"refreshed": refreshed, "failed": failed}


async def load_fund_snapshot(conn, ticker: str) -> Optional[Dict[str, Any]]:
    """Lit la fiche d'un actif depuis la base (None si jamais collectée)."""
    profile = await conn.fetchrow("""
        SELECT a.id AS asset_id, a.ticker, a.currency, a.asset_class, p.*
        FROM assets a JOIN fund_profiles p ON p.asset_id = a.id
        WHERE a.ticker = $1;
    """, ticker)
    if profile is None:
        return None
    holdings = await conn.fetch(
        "SELECT symbol, name, weight_pct, source FROM fund_holdings WHERE asset_id = $1 ORDER BY rank;",
        profile["asset_id"],
    )
    exposures = await conn.fetch(
        "SELECT kind, label, weight_pct, source FROM fund_exposures WHERE asset_id = $1 ORDER BY weight_pct DESC;",
        profile["asset_id"],
    )
    return {
        "ticker": profile["ticker"],
        "profile": dict(profile),
        "holdings": [dict(h) for h in holdings],
        "countries": [{"name": e["label"], "weight_pct": e["weight_pct"], "source": e["source"]} for e in exposures if e["kind"] == "country"],
        "sectors": [{"name": e["label"], "weight_pct": e["weight_pct"], "source": e["source"]} for e in exposures if e["kind"] == "sector"],
    }


async def get_fund_snapshot(conn, ticker: str) -> Optional[Dict[str, Any]]:
    """
    Lit la fiche en base. Elle est (re)collectée à la demande si l'actif n'a jamais été scrapé
    ou si sa fiche date de plus de FUND_DATA_MAX_AGE_DAYS ; en cas d'échec, la fiche existante est conservée.
    """
    clean = ticker.strip().upper()
    if clean in NON_LISTED_TICKERS:
        return None  # actif non coté : aucune source publique à collecter
    snapshot = await load_fund_snapshot(conn, clean)
    updated_at = snapshot["profile"].get("updated_at") if snapshot else None
    is_stale = updated_at is not None and datetime.now(timezone.utc) - updated_at > timedelta(days=FUND_DATA_MAX_AGE_DAYS)
    if snapshot is None or is_stale:
        await refresh_fund_data(conn, [clean])
        snapshot = await load_fund_snapshot(conn, clean) or snapshot
    return snapshot
