"""
Tests d'intégration sur un VRAI PostgreSQL (pgvector inclus), démarré par le paquet `pgserver`.

Le SQL du projet est réellement exécuté (schéma, migrations, upserts, requêtes des endpoints) ;
seuls les appels réseau (Yahoo Finance, JustETF, LLM) et Redis sont remplacés par des doublures.
"""
import asyncio
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

pgserver = pytest.importorskip("pgserver")
asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def _business_days(n_days):
    d, out = date.today() - timedelta(days=int(n_days * 1.45)), []
    while d <= date.today():
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _business_days(800)
_rng = np.random.default_rng(1)


def _series(mu, sigma, start):
    return start * np.exp(np.cumsum(_rng.normal(mu, sigma, len(DAYS))))


RAW = {
    "SPY": _series(3e-4, 0.011, 450.0),
    "CW8.PA": _series(3e-4, 0.009, 450.0),
    "QQQ": _series(4e-4, 0.014, 380.0),
    "EURUSD=X": _series(0.0, 0.004, 1.08),
    "XEON.DE": 140.0 * np.exp(np.cumsum(np.full(len(DAYS), 0.025 / 252))),  # €STR ≈ 2,5 %/an
}
DIVIDEND_FACTOR = {"SPY": 0.98}  # historique ajusté de SPY : −2 % de dividendes


async def _fake_download(tickers, period):
    from app.services import market_data as md
    n = 5 if period == md.INCREMENTAL_PERIOD else len(DAYS)
    out = {}
    for t in tickers:
        if t not in RAW:
            out[t] = []
            continue
        f = DIVIDEND_FACTOR.get(t, 1.0)
        out[t] = [
            (d, float(RAW[t][i]), float(RAW[t][i]) * (f if i < len(DAYS) - 1 else 1.0), 1000)
            for i, d in list(enumerate(DAYS))[-n:]
        ]
    return out


async def _no_refresh(conn, tickers):
    return {"refreshed": [], "failed": list(tickers)}


class _FakeRedis:
    def __init__(self, *args, **kwargs):
        pass

    async def set(self, *args, **kwargs):
        return True

    async def get(self, *args, **kwargs):
        return None

    async def close(self):
        return None


CW8_SNAPSHOT = {
    "ticker": "CW8.PA",
    "profile": {"isin": "LU1681043599", "quote_type": "ETF", "name": "Amundi MSCI World", "fund_family": "Amundi",
                "category": "Actions Monde", "description": "Réplique le MSCI World.", "total_assets": 5.2e9,
                "total_assets_currency": "EUR", "expense_ratio_pct": None, "sources": "yahoo,justetf"},
    "holdings": [{"symbol": None, "name": n, "weight_pct": w} for n, w in [("Apple", 5.1), ("Microsoft", 4.8), ("NVIDIA", 4.5)]],
    "holdings_source": "justetf",
    "countries": [{"name": "États-Unis", "weight_pct": 71.2}, {"name": "Autres", "weight_pct": 28.8}],
    "countries_source": "justetf",
    "sectors": [{"name": "Technologie", "weight_pct": 25.0}],
    "sectors_source": "justetf",
}


@pytest.fixture(scope="module")
def pg_uri():
    """PostgreSQL embarqué, schéma du projet, cours synthétiques et fiches chargés une fois pour le module."""
    from app.services import fund_data, market_data as md
    import app.api.main as main
    import app.services.llm as llm

    datadir = tempfile.mkdtemp(prefix="pg_copilot_")
    server = pgserver.get_server(datadir, cleanup_mode="stop")
    uri = server.get_uri()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(md, "download_history", _fake_download)
        mp.setattr(fund_data, "refresh_fund_data", _no_refresh)
        mp.setattr(main, "refresh_fund_data", _no_refresh)
        mp.setattr(main.aioredis, "Redis", _FakeRedis)
        mp.setattr(llm, "client", None)

        async def setup():
            conn = await asyncpg.connect(uri)
            sql = (ROOT / "database" / "init_db.sql").read_text(encoding="utf-8")
            await conn.execute(sql.replace("CREATE DATABASE airflow_db;", ""))  # hors transaction, inutile ici
            await md.ensure_schema(conn)
            ids = await md.ensure_assets(conn, list(RAW))
            await md.sync_market_data(conn, ids, full=True)
            await fund_data.save_fund_snapshot(conn, ids["CW8.PA"], CW8_SNAPSHOT)
            spy_profile = dict(CW8_SNAPSHOT["profile"], isin=None, name="SPDR S&P 500", total_assets=5.5e11,
                               total_assets_currency="USD")
            await fund_data.save_fund_snapshot(conn, ids["SPY"], dict(CW8_SNAPSHOT, ticker="SPY", profile=spy_profile))
            await conn.close()

        asyncio.run(setup())
        yield uri

    server.cleanup()
    shutil.rmtree(datadir, ignore_errors=True)


def _run(uri, scenario):
    async def wrapper():
        conn = await asyncpg.connect(uri)
        try:
            return await scenario(conn)
        finally:
            await conn.close()
    return asyncio.run(wrapper())


def test_schema_migration_is_idempotent(pg_uri):
    from app.services import market_data as md

    async def scenario(conn):
        await md.ensure_schema(conn)
        await md.ensure_schema(conn)
        return await conn.fetchval("SELECT currency FROM assets WHERE ticker = 'CW8.PA'")

    assert _run(pg_uri, scenario) == "EUR"


def test_incremental_sync_and_dividend_detection(pg_uri):
    from app.services import market_data as md

    async def scenario(conn):
        ids = await md.ensure_assets(conn, ["SPY", "CW8.PA"])
        unchanged = await md.sync_market_data(conn, ids, full=False)
        DIVIDEND_FACTOR["SPY"] = 0.97  # nouveau dividende : Yahoo ré-ajuste tout l'historique
        try:
            changed = await md.sync_market_data(conn, ids, full=False)
        finally:
            DIVIDEND_FACTOR["SPY"] = 0.98
            await md.sync_market_data(conn, ids, full=True)
        return unchanged, changed

    unchanged, changed = _run(pg_uri, scenario)
    assert unchanged["full_refresh"] == [] and unchanged["failed"] == []
    assert changed["full_refresh"] == ["SPY"]


def test_eur_panel_and_risk_free_rate(pg_uri):
    from app.services import market_data as md

    async def scenario(conn):
        _, close, _ = await md.load_base_currency_panel(conn, ["SPY", "CW8.PA"], years=3)
        return close.tail(1).to_dicts()[0], await md.risk_free_rate(conn)

    last, rf = _run(pg_uri, scenario)
    assert last["SPY"] == pytest.approx(RAW["SPY"][-1] / RAW["EURUSD=X"][-1])
    assert rf["rate"] == pytest.approx(0.025, abs=0.002)


def test_read_endpoints(pg_uri):
    import app.api.main as m
    from app.api import schemas as sc

    async def scenario(conn):
        return {
            "assets": await m.get_available_assets(conn=conn),
            "look_through": await m.get_etf_look_through_endpoint("CW8.PA", conn=conn),
            "geo": await m.get_portfolio_geographic_exposure_endpoint(
                sc.PortfolioGeoExposureRequest(tickers=["CW8.PA", "SCPI-EUR"]), conn=conn),
            "popular": await m.get_popular_etfs_endpoint(quick_only=True, conn=conn),
            "metrics": await m.calculate_portfolio_metrics_endpoint(sc.PortfolioMetricsRequest(
                assets=[{"ticker": "SPY", "shares": 10}, {"ticker": "CW8.PA", "shares": 10}], years=3), conn=conn),
            "stress": await m.stress_scenarios_endpoint(sc.StressScenarioRequest(
                assets=[{"ticker": "SPY", "shares": 10}, {"ticker": "QQQ", "shares": 5}, {"ticker": "SCPI-EUR", "shares": 1}]),
                conn=conn),
        }

    r = _run(pg_uri, scenario)
    assert all(not a["ticker"].endswith("=X") for a in r["assets"])
    assert r["look_through"]["num_constituents"] == 3 and r["look_through"]["source_name"].startswith("JustETF")
    geo = r["geo"] if isinstance(r["geo"], dict) else r["geo"].model_dump()
    assert geo["assets"]["SCPI-EUR"]["regions"] == {"Non ventilé": 100.0}
    assert "Autres pays (non détaillés)" in geo["assets"]["CW8.PA"]["regions"]
    spy = next(p for p in r["popular"] if p["ticker"] == "SPY")
    assert spy["aum_eur"] == pytest.approx(5.5e11 / RAW["EURUSD=X"][-1], rel=1e-6)
    assert r["metrics"]["summary"]["risk_free_source"].startswith("€STR")
    assert [s["id"] for s in r["stress"]["scenarios"]] == ["worst_period", "worst_month", "worst_market_month"]
    assert r["stress"]["excluded_non_listed"] == ["SCPI-EUR"]


def test_portfolio_lifecycle_and_real_performance(pg_uri):
    import app.api.main as m
    from app.api import schemas as sc

    buy_day, sell_day = DAYS[-300], DAYS[-10]

    async def scenario(conn):
        out = {"empty": await m.get_portfolio_endpoint(conn=conn)}
        out["after_buy"] = await m.add_portfolio_transaction_endpoint(
            sc.TransactionCreateRequest(ticker="SPY", date=buy_day, type="buy", shares=3), conn=conn)
        await m.add_portfolio_transaction_endpoint(
            sc.TransactionCreateRequest(ticker="SCPI-EUR", date=buy_day, type="buy", shares=2, price=200.0), conn=conn)
        try:
            await m.add_portfolio_transaction_endpoint(
                sc.TransactionCreateRequest(ticker="SPY", date=DAYS[-400], type="sell", shares=1), conn=conn)
            out["early_sell"] = None
        except m.HTTPException as e:
            out["early_sell"] = e.status_code
        after_sell = await m.add_portfolio_transaction_endpoint(
            sc.TransactionCreateRequest(ticker="SPY", date=sell_day, type="sell", shares=1, price=500.0), conn=conn)
        buy_id = next(t["id"] for a in after_sell["assets"] if a["ticker"] == "SPY" for t in a["transactions"] if t["type"] == "buy")
        try:
            await m.delete_portfolio_transaction_endpoint(buy_id, conn=conn)
            out["unsafe_delete"] = None
        except m.HTTPException as e:
            out["unsafe_delete"] = e.status_code
        out["performance"] = await m.portfolio_performance_endpoint(benchmark="CW8.PA", conn=conn)
        out["holdings"] = await m.portfolio_holdings_history_endpoint(years=3, benchmark="CW8.PA", conn=conn)
        out["final"] = await m.delete_portfolio_asset_endpoint("SCPI-EUR", conn=conn)
        return out

    r = _run(pg_uri, scenario)
    assert r["empty"]["assets"] == []
    tx = r["after_buy"]["assets"][0]["transactions"][0]
    i = DAYS.index(buy_day)
    assert tx["price_source"] == "market_close"
    assert tx["price"] == pytest.approx(RAW["SPY"][i] / RAW["EURUSD=X"][i], rel=1e-5)
    assert r["early_sell"] == 400 and r["unsafe_delete"] == 400

    perf = r["performance"]
    assert perf["excluded_non_listed"] == ["SCPI-EUR"]
    assert perf["summary"]["net_invested_eur"] == pytest.approx(3 * tx["price"] - 500.0, rel=1e-4)
    assert perf["benchmark"]["ticker"] == "CW8.PA" and perf["time_series"]
    assert [a["ticker"] for a in r["final"]["assets"]] == ["SPY"]

    holdings = r["holdings"]
    assert holdings["quantities"] == {"SPY": 2.0} and holdings["excluded_non_listed"] == ["SCPI-EUR"]
    # Série continue sur toute la période, même avant la date d'achat
    assert holdings["time_series"][0]["date"] < str(buy_day) and len(holdings["time_series"]) > 300
    last = holdings["time_series"][-1]
    assert last["portfolio_value"] == pytest.approx(2 * RAW["SPY"][-1] / RAW["EURUSD=X"][-1], rel=1e-4)


def test_pipeline_and_admin_guard(pg_uri):
    import app.api.main as m
    from app.api import schemas as sc

    async def scenario(conn):
        pipeline = await m.run_daily_market_pipeline_endpoint(m.PipelineScrapeRequest(tickers=["SPY", "CW8.PA"]), conn=conn)
        status = await m.get_pipeline_status_endpoint(conn=conn)
        try:
            await m.rag_ingest_documents_endpoint(
                sc.DocumentIngestRequest(documents=[{"content": "Document injecté pour test"}]), x_admin_token=None)
            guard = None
        except m.HTTPException as e:
            guard = e.status_code
        return pipeline, status, guard

    pipeline, status, guard = _run(pg_uri, scenario)
    assert pipeline["status"] == "success"
    assert status["database"]["total_assets"] >= 5
    assert guard == 403
