import logging
from typing import Any, Dict, List, Optional

from app.services.fund_data import get_fund_snapshot

logger = logging.getLogger(__name__)

# Décomposition par transparence (look-through) lue depuis PostgreSQL (tables fund_profiles /
# fund_holdings), alimentées par le scraping Yahoo Finance + JustETF (app/services/fund_data.py).
# Un actif jamais collecté est scrapé à la demande puis enregistré.

_SOURCE_NAMES = {"yahoo": "Yahoo Finance", "justetf": "JustETF"}


async def generate_smart_description(
    ticker: str,
    is_etf: bool,
    name: str,
    category_or_industry: str = "",
    sector: str = "",
    raw_summary: str = "",
    fund_family: str = ""
) -> Optional[str]:
    """
    Résume en français, via le LLM, la description publiée par la source (Yahoo / JustETF).
    Renvoie None si aucune description source n'existe ou si le LLM est indisponible :
    on n'invente pas de description à partir de rien.
    """
    if not raw_summary:
        return None
    try:
        from app.services.llm import DESCRIPTION_MODEL, client
        if not client:
            return None

        prompt = """Tu es un rédacteur financier pour une plateforme d'investissement.
Résume en français la description fournie, en 1 ou 2 phrases courtes (35 mots maximum).
Règles impératives :
1. Utilise UNIQUEMENT les informations présentes dans la description fournie. N'ajoute aucun fait, aucun chiffre.
2. Strictement neutre : aucun terme marketing ou élogieux.
3. Pour un ETF : indice ou univers d'investissement et gestionnaire, sans citer d'entreprise particulière.
4. Pour une action : activité concrète de l'entreprise.
5. Réponds uniquement avec la description, sans titre, sans guillemets, sans astérisques."""

        context = f"""Actif: {ticker} ({name})
Type: {'ETF' if is_etf else 'Action'}
Catégorie / Secteur: {sector or category_or_industry}
Gestionnaire: {fund_family or 'Non précisé'}
Description source: {raw_summary[:600]}"""

        resp = await client.chat.completions.create(
            model=DESCRIPTION_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": context}
            ],
            temperature=0.0,
            max_tokens=90
        )
        desc = (resp.choices[0].message.content or "").strip().strip('"\'«»')
        return desc if len(desc) > 15 else None
    except Exception as e:
        logger.warning(f"Résumé de description indisponible pour {ticker}: {e}")
        return None


def _source_label(sources: List[Optional[str]]) -> str:
    names = [_SOURCE_NAMES.get(s, s) for s in dict.fromkeys(s for s in sources if s)]
    return " & ".join(names) if names else "Yahoo Finance"


async def get_etf_look_through(conn, ticker: str) -> Dict[str, Any]:
    """Construit la fiche look-through d'un actif à partir des données stockées en base."""
    clean_ticker = ticker.strip().upper()
    snapshot = await get_fund_snapshot(conn, clean_ticker)
    yahoo_url = f"https://finance.yahoo.com/quote/{clean_ticker}"

    if snapshot is None:
        return {
            "ticker": clean_ticker,
            "name": clean_ticker,
            "is_etf": None,
            "source_name": "Aucune source",
            "source_url": yahoo_url,
            "factsheet_url": yahoo_url,
            "num_constituents": 0,
            "holdings": [],
            "total_top_holdings_weight": 0.0,
            "verified": False,
            "empty_message": f"Aucune donnée n'a pu être collectée pour {clean_ticker}.",
            "description": None,
        }

    profile = snapshot["profile"]
    is_etf = (profile.get("quote_type") or "").upper() != "EQUITY"
    isin = profile.get("isin")
    justetf_url = f"https://www.justetf.com/fr/etf-profile.html?isin={isin}" if isin else None

    # Résumé français généré une seule fois puis stocké (invalidé si la description source change)
    description = profile.get("description_fr")
    if not description and profile.get("description"):
        description = await generate_smart_description(
            ticker=clean_ticker,
            is_etf=is_etf,
            name=profile.get("name") or clean_ticker,
            category_or_industry=profile.get("category") or "",
            raw_summary=profile.get("description") or "",
            fund_family=profile.get("fund_family") or "",
        )
        if description:
            await conn.execute(
                "UPDATE fund_profiles SET description_fr = $2 WHERE asset_id = $1;", profile["asset_id"], description
            )

    holdings = [
        {"symbol": h.get("symbol") or "", "name": h["name"], "weight_pct": round(float(h["weight_pct"]), 2), "sector": None}
        for h in snapshot["holdings"]
    ]
    total_top = round(sum(h["weight_pct"] for h in holdings), 2)
    holdings_source = snapshot["holdings"][0]["source"] if snapshot["holdings"] else None

    return {
        "ticker": clean_ticker,
        "name": profile.get("name") or clean_ticker,
        "is_etf": is_etf,
        "fund_family": profile.get("fund_family"),
        "category": profile.get("category"),
        "isin": isin,
        "expense_ratio_pct": profile.get("expense_ratio_pct"),
        "source_name": _source_label([holdings_source] + (profile.get("sources") or "").split(",")),
        "source_url": justetf_url if holdings_source == "justetf" else yahoo_url,
        "justetf_url": justetf_url,
        "factsheet_url": justetf_url or yahoo_url,
        "num_constituents": len(holdings),
        "holdings": holdings,
        "total_top_holdings_weight": total_top,
        "verified": bool(holdings),
        "updated_at": profile["updated_at"].isoformat() if profile.get("updated_at") else None,
        "empty_message": None if holdings else (
            "La composition de cet actif n'est publiée ni par Yahoo Finance ni par JustETF "
            "(fréquent pour les fonds obligataires ou monétaires). Consultez le rapport de l'émetteur."
        ),
        "description": description,
    }
