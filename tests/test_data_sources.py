import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.quant.estimation import dimson_beta
from app.quant.stress import compute_historical_stress, worst_buy_and_hold_period
from app.services import fund_data
from app.services.fund_data import parse_justetf_profile, scrape_fund_snapshot
from app.services.geo_exposure import OTHER_COUNTRIES_REGION, aggregate_macro_regions
from app.services.portfolio import PortfolioError, add_transaction, holdings_never_negative


# ---------------------------------------------------------------------------
# Scraping JustETF / Yahoo : aucune valeur inventée
# ---------------------------------------------------------------------------

JUSTETF_HTML = """
<html><body>
  <h3>Principales positions</h3>
  <table>
    <tr><td>Apple</td><td>5,12%</td></tr>
    <tr><td>Microsoft</td><td>4,80%</td></tr>
    <tr><td>NVIDIA</td><td>4,51%</td></tr>
  </table>
  <h3>Pays</h3>
  <table>
    <tr><td>États-Unis</td><td>71,20%</td></tr>
    <tr><td>Japon</td><td>5,40%</td></tr>
    <tr><td>Autres</td><td>23,40%</td></tr>
  </table>
  <h3>Secteurs</h3>
  <table><tr><td>Technologie</td><td>25,00%</td></tr></table>
  <div class="etf-profile-description__contents">Réplique l'indice MSCI World.</div>
</body></html>
"""


def test_parse_justetf_profile_reads_tables():
    parsed = parse_justetf_profile(JUSTETF_HTML)
    assert [h["name"] for h in parsed["holdings"]] == ["Apple", "Microsoft", "NVIDIA"]
    assert parsed["holdings"][0]["weight_pct"] == 5.12
    assert parsed["countries"][-1] == {"name": "Autres", "weight_pct": 23.4}
    assert parsed["sectors"] == [{"name": "Technologie", "weight_pct": 25.0}]
    assert parsed["description"] == "Réplique l'indice MSCI World."


def _fake_yf(info, funds_data=None, isin="-"):
    ticker = SimpleNamespace(info=info, funds_data=funds_data, isin=isin)
    return SimpleNamespace(Ticker=lambda t: ticker)


def test_etf_without_published_data_stays_empty(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "yfinance", _fake_yf({"quoteType": "ETF", "longName": "Fonds X"}))
    monkeypatch.setattr(fund_data, "_scrape_justetf", lambda isin: {})
    snap = scrape_fund_snapshot("XYZ.PA")
    # Ni positions, ni pays, ni encours inventés
    assert snap["holdings"] == [] and snap["countries"] == []
    assert snap["profile"]["total_assets"] is None


def test_european_etf_uses_justetf_when_yahoo_has_no_holdings(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "yfinance", _fake_yf({"quoteType": "ETF", "totalAssets": 5e9, "currency": "EUR"}))
    monkeypatch.setattr(fund_data, "_scrape_justetf", lambda isin: parse_justetf_profile(JUSTETF_HTML))
    snap = scrape_fund_snapshot("CW8.PA")  # ISIN connu du référentiel
    assert snap["holdings_source"] == "justetf" and len(snap["holdings"]) == 3
    assert snap["countries_source"] == "justetf"
    assert snap["profile"]["total_assets"] == 5e9


def test_equity_exposure_comes_from_company_country(monkeypatch):
    info = {"quoteType": "EQUITY", "longName": "Airbus SE", "country": "France", "sector": "Industrials"}
    monkeypatch.setitem(__import__("sys").modules, "yfinance", _fake_yf(info))
    snap = scrape_fund_snapshot("AIR.PA")
    assert snap["countries"] == [{"name": "France", "weight_pct": 100.0}]
    assert snap["holdings"] == [{"symbol": "AIR.PA", "name": "Airbus SE", "weight_pct": 100.0}]


# ---------------------------------------------------------------------------
# Géographie et RAG construits depuis les données stockées
# ---------------------------------------------------------------------------

def test_other_countries_are_not_redistributed():
    regions = aggregate_macro_regions([
        {"name": "États-Unis", "weight_pct": 71.2},
        {"name": "Japon", "weight_pct": 5.4},
        {"name": "Autres", "weight_pct": 23.4},
    ])
    assert regions == {"États-Unis": 71.2, "Asie & Pacifique": 5.4, OTHER_COUNTRIES_REGION: 23.4}
    assert aggregate_macro_regions([]) == {}


def test_rag_document_built_from_db_snapshot_marks_missing_fields():
    rag_ingestion = pytest.importorskip("app.services.rag_ingestion")
    llm = pytest.importorskip("app.services.llm")
    snapshot = {
        "ticker": "CW8.PA",
        "profile": {"name": "Amundi MSCI World", "isin": "LU1681043599", "category": None, "fund_family": "Amundi",
                    "currency": "EUR", "total_assets": None, "expense_ratio_pct": None, "description": "Réplique le MSCI World.",
                    "sources": "yahoo,justetf", "updated_at": datetime(2026, 10, 27, tzinfo=timezone.utc)},
        "holdings": [{"symbol": None, "name": "Apple", "weight_pct": 5.12}],
        "countries": [{"name": "États-Unis", "weight_pct": 71.2}],
        "sectors": [],
    }
    doc = rag_ingestion.build_fund_document(snapshot)
    assert "Actifs sous gestion (AUM) : Non publié" in doc.page_content
    assert "Frais courants (TER) : Non publié" in doc.page_content
    assert "- Apple : 5,12%" in doc.page_content

    parsed = llm.extract_doc_structured_info(doc.page_content)
    assert parsed["holdings"] == ["Apple : 5,12%"]  # la répartition par pays n'est pas lue comme une position
    assert parsed["strategy"] == "Réplique le MSCI World."


# ---------------------------------------------------------------------------
# Portefeuille persistant
# ---------------------------------------------------------------------------

def test_sell_cannot_exceed_holdings_at_its_date():
    d1, d2, d3 = date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)
    assert holdings_never_negative([(d1, "buy", 10), (d2, "sell", 10)]) is True
    assert holdings_never_negative([(d2, "buy", 10), (d1, "sell", 5)]) is False  # vente avant l'achat
    assert holdings_never_negative([(d1, "buy", 10), (d2, "sell", 6), (d3, "sell", 6)]) is False


class _PortfolioConn:
    """Base minimale : SPY coté en USD à 550 $, EUR/USD à 1,10, aucune opération existante."""

    def __init__(self):
        self.inserted = None

    async def fetchrow(self, query, *args):
        if "a.currency, m.close_price" in query:
            return {"currency": "USD", "close_price": 550.0, "market_date": date(2026, 10, 23)} if args[0] == "SPY" else None
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if "SELECT m.close_price" in query:
            return 1.10
        if "INSERT INTO portfolio_transactions" in query:
            self.inserted = args
            return 1
        raise AssertionError(query)

    async def fetch(self, query, *args):
        return []


def test_transaction_without_price_uses_market_close_in_eur():
    conn = _PortfolioConn()
    asyncio.run(add_transaction(conn, 1, "spy", date(2026, 10, 24), "buy", 2, None))
    _, ticker, _, side, qty, price_eur, source = conn.inserted
    assert (ticker, side, qty, source) == ("SPY", "buy", 2, "market_close")
    assert price_eur == pytest.approx(500.0)  # 550 USD / 1,10


def test_non_listed_asset_requires_a_price():
    with pytest.raises(PortfolioError):
        asyncio.run(add_transaction(_PortfolioConn(), 1, "SCPI-EUR", date(2026, 10, 24), "buy", 1, None))


# ---------------------------------------------------------------------------
# Stress tests historiques
# ---------------------------------------------------------------------------

def _price_panel():
    n = 120
    days = [date(2026, 1, 1) + np.timedelta64(i, "D").astype(object) for i in range(n)]
    rng = np.random.default_rng(3)
    market = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    market[60:80] *= np.linspace(1.0, 0.80, 20)  # krach de -20 % sur 20 séances
    market[80:] *= 0.80
    levered = 100 * (1 + 2 * np.concatenate([[0], market[1:] / market[:-1] - 1])).cumprod()  # bêta 2
    gold = np.full(n, 50.0)
    return pl.DataFrame({"market_date": days, "MKT": market, "LEV": levered, "GLD": gold})


def test_worst_period_is_found_exhaustively():
    prices = np.array([[100.0], [120.0], [90.0], [110.0], [80.0], [95.0]])
    assert worst_buy_and_hold_period(prices, np.array([1.0])) == (1, 4)  # 120 -> 80


def test_historical_stress_uses_realised_returns_and_betas():
    panel = _price_panel()
    result = compute_historical_stress(panel, ["LEV", "GLD"], np.array([0.5, 0.5]), market_ticker="MKT")

    # LEV = 2 × marché chaque jour : son bêta de Dimson vaut exactement 2 × celui du marché sur lui-même
    m = panel["MKT"].to_numpy()
    m_ret = m[1:] / m[:-1] - 1.0
    expected_beta = 2.0 * dimson_beta(m_ret, m_ret)
    assert result["betas"]["LEV"] == pytest.approx(expected_beta, abs=1e-3)
    assert result["betas"]["GLD"] == pytest.approx(0.0, abs=1e-12)
    ids = [s["id"] for s in result["scenarios"]]
    assert ids == ["worst_period", "worst_month", "worst_market_month"]

    market_month = result["scenarios"][2]
    assert market_month["asset_returns_pct"]["GLD"] == 0.0  # l'or (prix constant ici) n'a pas bougé
    assert market_month["asset_returns_pct"]["LEV"] < -30.0
    # Rendement du portefeuille = moyenne pondérée des rendements réalisés des lignes
    expected = 0.5 * market_month["asset_returns_pct"]["LEV"] + 0.5 * market_month["asset_returns_pct"]["GLD"]
    assert market_month["portfolio_return_pct"] == pytest.approx(expected, abs=0.01)
