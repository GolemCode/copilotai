import numpy as np
import polars as pl
from datetime import date

from app.quant.metrics import compute_portfolio_metrics
from app.api.schemas import (
    PortfolioMetricsRequest,
    RagQueryResponse,
    RagSourceItem
)

def test_portfolio_metrics_calculation():
    # Génération d'un historique simulé sur 1 an (252 séances)
    n_days = 252
    dates = [date(2023, 1, 1)]
    for i in range(1, n_days):
        dates.append(date.fromordinal(dates[0].toordinal() + i))

    np.random.seed(42)
    # Simulation de rendements géométriques browniens
    ret_a = np.random.normal(0.0006, 0.012, n_days)
    ret_b = np.random.normal(0.0004, 0.009, n_days)
    ret_bench = np.random.normal(0.0005, 0.010, n_days)

    price_a = 100.0 * np.cumprod(1.0 + ret_a)
    price_b = 50.0 * np.cumprod(1.0 + ret_b)
    price_bench = 400.0 * np.cumprod(1.0 + ret_bench)

    df = pl.DataFrame({
        "market_date": dates,
        "ASSET_A": price_a,
        "ASSET_B": price_b,
        "SPY": price_bench
    })

    tickers = ["ASSET_A", "ASSET_B"]
    weights = np.array([0.6, 0.4])

    results = compute_portfolio_metrics(
        daily_prices_df=df,
        tickers=tickers,
        weights=weights,
        initial_investment=10000.0,
        risk_free_rate=0.03,
        benchmark_col="SPY"
    )

    assert "summary" in results
    assert "time_series" in results
    assert "asset_breakdown" in results
    assert "benchmark" in results

    summary = results["summary"]
    assert summary["initial_investment"] == 10000.0
    assert summary["final_value"] > 0
    assert isinstance(summary["sharpe_ratio"], float)
    assert isinstance(summary["annualized_volatility_pct"], float)
    assert summary["max_drawdown_pct"] <= 0.0  # Le drawdown est négatif ou nul

    # Vérification du benchmark
    bench = results["benchmark"]
    assert bench["ticker"] == "SPY"
    assert bench["annualized_return_pct"] is not None

    # Vérification du détail des actifs
    breakdown = results["asset_breakdown"]
    assert len(breakdown) == 2
    assert breakdown[0]["ticker"] == "ASSET_A"
    assert breakdown[0]["weight_pct"] == 60.0

def test_pydantic_schemas():
    # Test RagQueryResponse
    source = RagSourceItem(
        ticker="CW8.PA",
        name="Amundi MSCI World",
        category="ETF Monde",
        similarity_score_pct=91.5,
        snippet="Fonds répliquant l'indice MSCI World."
    )
    res = RagQueryResponse(
        answer="CW8 est un ETF diversifié mondialement.",
        sources=[source],
        latency_ms=350.2
    )
    assert res.answer.startswith("CW8")
    assert len(res.sources) == 1
    assert res.sources[0].ticker == "CW8.PA"

    # Test PortfolioMetricsRequest
    req = PortfolioMetricsRequest(
        assets=[{"ticker": "SPY", "shares": 10}],
        risk_free_rate=0.03,
        years=3
    )
    assert len(req.assets) == 1
    assert req.assets[0].shares == 10
