import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.services.fund_data import get_fund_snapshot
from app.services.market_data import NON_LISTED_TICKERS

logger = logging.getLogger(__name__)

# Exposition géographique calculée à partir des répartitions par pays stockées en base
# (fund_exposures, alimentée par JustETF pour les ETF et par Yahoo Finance pour les actions).
# Aucune répartition de repli : un actif sans donnée publiée est signalé comme non ventilé.

UNALLOCATED_REGION = "Non ventilé"
OTHER_COUNTRIES_REGION = "Autres pays (non détaillés)"

EUROPE_COUNTRIES = {
    "grande bretagne", "royaume uni", "united kingdom", "uk",
    "france", "allemagne", "germany", "suisse", "switzerland",
    "pays bas", "netherlands", "italie", "italy", "espagne", "spain",
    "suede", "sweden", "danemark", "denmark", "finlande", "finland",
    "norvege", "norway", "belgique", "belgium", "irlande", "ireland",
    "autriche", "austria", "portugal", "pologne", "poland", "luxembourg",
    "grece", "greece", "tchequie", "czech republic", "hongrie", "hungary",
    "europe", "union europeenne", "jersey"
}

ASIA_PACIFIC_COUNTRIES = {
    "japon", "japan", "australie", "australia", "hong kong", "singapour",
    "singapore", "nouvelle zelande", "new zealand", "coree du sud", "south korea",
    "coree", "korea", "taiwan", "chine", "china", "inde", "india",
    "indonesie", "indonesia", "malaisie", "malaysia", "thailande", "thailand",
    "philippines", "vietnam"
}

US_COUNTRIES = {
    "etats unis", "united states", "usa", "us", "u.s.", "u.s.a."
}

OTHER_LABELS = {"autre", "autres", "other", "others"}


def normalize_text(text: str) -> str:
    """Supprime les accents, tirets et normalise en minuscules."""
    clean = "".join(
        c for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )
    return clean.replace("-", " ").strip()


def aggregate_macro_regions(countries: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Agrège la répartition par pays publiée en macro-régions.
    La ligne "Autres" publiée par la source reste dans une région dédiée : on ne la
    redistribue pas selon une hypothèse. Les pourcentages sont renormalisés à 100 %.
    """
    regions: Dict[str, float] = {}
    for item in countries:
        norm = normalize_text(item.get("name", ""))
        pct = float(item.get("weight_pct", 0.0))
        if norm in OTHER_LABELS:
            region = OTHER_COUNTRIES_REGION
        elif norm in US_COUNTRIES:
            region = "États-Unis"
        elif norm in EUROPE_COUNTRIES:
            region = "Europe / France"
        elif norm in ASIA_PACIFIC_COUNTRIES:
            region = "Asie & Pacifique"
        else:
            region = "International / Autres"
        regions[region] = regions.get(region, 0.0) + pct

    total = sum(regions.values())
    if total <= 0:
        return {}
    return {k: round(v / total * 100.0, 2) for k, v in regions.items() if v > 0}


async def get_asset_geo_exposure(conn, ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
    """Exposition géographique d'un actif, lue en base (collectée à la demande si absente)."""
    clean_ticker = ticker.strip().upper()
    if force_refresh:
        from app.services.fund_data import refresh_fund_data
        await refresh_fund_data(conn, [clean_ticker])

    snapshot = await get_fund_snapshot(conn, clean_ticker)
    if clean_ticker in NON_LISTED_TICKERS:
        return {
            "ticker": clean_ticker, "countries": [], "regions": {UNALLOCATED_REGION: 100.0},
            "source": "non_cote", "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    countries = [{"name": c["name"], "weight_pct": c["weight_pct"]} for c in (snapshot or {}).get("countries", [])]
    source = snapshot["countries"][0]["source"] if snapshot and snapshot["countries"] else "non_disponible"
    updated_at = snapshot["profile"]["updated_at"].isoformat() if snapshot and snapshot["profile"].get("updated_at") else None

    return {
        "ticker": clean_ticker,
        "countries": countries,
        "regions": aggregate_macro_regions(countries),
        "source": source,
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
    }


async def get_portfolio_geo_exposure(conn, tickers: List[str]) -> Dict[str, Any]:
    """Expositions géographiques de toutes les lignes d'un portefeuille (lecture séquentielle sur une connexion)."""
    clean_tickers = sorted(set(t.strip().upper() for t in tickers if t and t.strip()))
    assets_map: Dict[str, Any] = {}
    for t in clean_tickers:
        try:
            assets_map[t] = await get_asset_geo_exposure(conn, t)
        except Exception as e:
            logger.error(f"Exposition géographique indisponible pour {t}: {e}")
            assets_map[t] = {
                "ticker": t, "countries": [], "regions": {}, "source": "erreur",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    return {"assets": assets_map}
