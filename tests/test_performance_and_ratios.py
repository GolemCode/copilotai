from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.quant.estimation import dimson_beta, weekly_covariance
from app.quant.monte_carlo import generate_monte_carlo_simulation
from app.quant.optimization import min_variance_weights, optimize_portfolio
from app.quant.performance import compute_realized_performance, xirr
from app.quant.ratios import daily_risk_free, downside_deviation, sharpe_ratio, sortino_ratio
from app.services.market_data import trailing_annual_return


# ---------------------------------------------------------------------------
# Ratios : une seule convention, vérifiée sur des valeurs calculées à la main
# ---------------------------------------------------------------------------

def test_sortino_uses_downside_deviation_over_all_observations():
    r = np.array([0.02, -0.01, 0.03, -0.02, 0.01])
    shortfall = np.minimum(r, 0.0)
    expected_dd = np.sqrt(np.mean(shortfall ** 2)) * np.sqrt(252)
    assert downside_deviation(r, 0.0) == pytest.approx(expected_dd)
    assert sortino_ratio(r, 0.0) == pytest.approx(np.mean(r) * 252 / expected_dd)
    # L'ancienne formule (écart-type des seuls rendements négatifs) donnait un résultat différent
    wrong = np.mean(r) * 252 / (np.std(r[r < 0], ddof=1) * np.sqrt(252))
    assert sortino_ratio(r, 0.0) != pytest.approx(wrong)


def test_sharpe_is_mean_excess_over_std_annualized():
    r = np.array([0.01, -0.005, 0.004, 0.002, -0.001, 0.003])
    rf = 0.02
    expected = np.mean(r - daily_risk_free(rf)) / np.std(r, ddof=1) * np.sqrt(252)
    assert sharpe_ratio(r, rf) == pytest.approx(expected)
    assert sharpe_ratio(np.full(10, 0.001), rf) is None  # volatilité nulle : ratio non défini


def test_risk_free_rate_from_money_market_history():
    rows = [(date(2025, 10, 27), 100.0), (date(2026, 4, 27), 101.2), (date(2026, 10, 27), 102.5)]
    rate, as_of = trailing_annual_return(rows)
    assert as_of == date(2026, 10, 27)
    assert rate == pytest.approx(0.025, abs=1e-4)


# ---------------------------------------------------------------------------
# Non-synchronicité des clôtures (Paris / New York)
# ---------------------------------------------------------------------------

def _lagged_markets(n=1500, seed=4):
    """Un même facteur, observé un jour plus tard sur la seconde place (clôture décalée)."""
    factor = np.random.default_rng(seed).normal(0.0, 0.01, n + 1)
    first_market = factor[1:]   # intègre l'information le jour même
    second_market = factor[:-1]  # ne l'intègre que le lendemain
    return first_market, second_market


def test_dimson_beta_corrects_asynchronous_closes():
    first_market, second_market = _lagged_markets()
    naive = np.cov(second_market, first_market, ddof=1)[0, 1] / np.var(first_market, ddof=1)
    assert abs(naive) < 0.1                                   # le bêta quotidien simple ne voit (presque) rien
    assert dimson_beta(second_market, first_market) == pytest.approx(1.0, abs=0.1)


def test_weekly_covariance_recovers_correlation_lost_daily():
    first_market, second_market = _lagged_markets()
    daily = np.column_stack([first_market, second_market])
    daily_corr = np.corrcoef(daily, rowvar=False)[0, 1]
    weekly = weekly_covariance(daily)
    weekly_corr = weekly[0, 1] / np.sqrt(weekly[0, 0] * weekly[1, 1])
    assert abs(daily_corr) < 0.1
    assert weekly_corr > 0.7  # le décalage d'un jour est absorbé dans les blocs de 5 séances


# ---------------------------------------------------------------------------
# Performance réelle à partir des transactions
# ---------------------------------------------------------------------------

D = [date(2026, 1, 5) + timedelta(days=i) for i in range(6)]
PX = [100.0, 110.0, 110.0, 121.0, 121.0, 133.1]


def _panel(adjusted=None):
    adj = pl.DataFrame({"market_date": D, "X": adjusted or PX, "B": [50.0] * 6})
    close = pl.DataFrame({"market_date": D, "X": PX, "B": [50.0] * 6})
    return adj, close


def _tx(day, side, qty, price, ticker="X"):
    return {"ticker": ticker, "trade_date": day, "side": side, "quantity": qty, "price_eur": price}


def test_twr_is_not_distorted_by_deposits():
    adj, close = _panel()
    txs = [_tx(D[0], "buy", 10, 100.0), _tx(D[2], "buy", 90, 110.0)]  # apport de 9 900 € en cours de route
    result = compute_realized_performance(txs, adj, close, benchmark="B")
    summary = result["summary"]
    assert summary["twr_cumulative_pct"] == pytest.approx(33.1)   # 1,1³ − 1
    assert summary["net_invested_eur"] == pytest.approx(10_900.0)
    assert summary["current_wealth_eur"] == pytest.approx(13_310.0)
    assert result["benchmark"]["value_with_same_flows"] == pytest.approx(10_900.0)  # benchmark plat
    assert summary["is_annualized"] is False and summary["twr_annualized_pct"] is None


def test_purchase_below_close_and_sale_are_accounted():
    adj, close = _panel()
    txs = [_tx(D[0], "buy", 10, 95.0), _tx(D[3], "sell", 10, 121.0)]
    s = compute_realized_performance(txs, adj, close)["summary"]
    # Achat à 95 € (clôture 100 €), vente à 121 € : richesse finale = produit de la vente, sans positions
    assert s["current_market_value_eur"] == pytest.approx(0.0)
    assert s["total_gain_eur"] == pytest.approx(10 * (121.0 - 95.0))
    assert s["twr_cumulative_pct"] == pytest.approx((121.0 / 95.0 - 1) * 100, abs=0.01)


def test_dividends_count_in_total_return():
    # Cours ajusté : +5 % de dividende le jour 3 alors que le cours brut est inchangé
    adjusted = [100.0, 110.0, 110.0 * 1.05, 121.0 * 1.05, 121.0 * 1.05, 133.1 * 1.05]
    adj, close = _panel(adjusted)
    s = compute_realized_performance([_tx(D[0], "buy", 10, 100.0)], adj, close)["summary"]
    assert s["twr_cumulative_pct"] == pytest.approx((1.331 * 1.05 - 1) * 100, abs=0.01)
    assert s["current_wealth_eur"] > s["current_market_value_eur"]  # dividendes perçus en plus de la valeur


def test_transactions_before_price_history_start_at_first_close():
    adj, close = _panel()
    s = compute_realized_performance([_tx(date(2020, 1, 1), "buy", 10, 20.0)], adj, close)["summary"]
    assert s["history_truncated_before"] == str(D[0])
    assert s["twr_cumulative_pct"] == pytest.approx(33.1)  # pas de gain fictif 20 € -> 100 € le premier jour


def test_xirr_matches_excel_convention():
    assert xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), 110.0)]) == pytest.approx(0.10, abs=1e-6)
    assert xirr([(date(2025, 1, 1), -100.0)]) is None


# ---------------------------------------------------------------------------
# Monte Carlo : DCA en euros
# ---------------------------------------------------------------------------

def test_dca_invests_a_fixed_amount_per_period():
    res = generate_monte_carlo_simulation(
        expected_returns=np.array([0.0002]), covariance_matrix=np.array([[0.0001]]), weights=np.array([1.0]),
        num_paths=500, time_horizon_days=63, initial_investment=10_000.0,
        strategy={"type": "dca", "params": {"amount_eur": 500.0, "frequency_days": 21}}, seed=3,
    )
    contributions = [t for t in res["trade_history"] if t["action"].startswith("Versement")]
    assert [t["day"] for t in contributions] == [21, 42, 63]
    assert res["metrics"]["median_invested"] == pytest.approx(10_000.0 + 3 * 500.0)


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError):
        generate_monte_carlo_simulation(
            np.array([0.0]), np.array([[0.0001]]), np.array([1.0]), 100, 20, 1000.0,
            strategy={"type": "momentum"}, seed=1,
        )


# ---------------------------------------------------------------------------
# Markowitz : optimisation réelle
# ---------------------------------------------------------------------------

def test_min_variance_matches_two_asset_closed_form():
    cov = np.array([[0.04, 0.006], [0.006, 0.09]])
    w1 = (0.09 - 0.006) / (0.04 + 0.09 - 2 * 0.006)
    assert min_variance_weights(cov)[0] == pytest.approx(w1, abs=1e-5)


def test_max_sharpe_beats_random_portfolios():
    mu = np.array([0.15, 0.12, 0.10, 0.08, 0.05])
    a = np.random.default_rng(0).normal(size=(5, 5))
    cov = a @ a.T / 50 + np.eye(5) * 0.01
    result = optimize_portfolio(mu, cov, list("ABCDE"), risk_free_rate=0.02, current_weights=np.ones(5))
    w = np.random.default_rng(1).dirichlet(np.ones(5), 20_000)
    random_sharpes = (w @ mu - 0.02) / np.sqrt(np.einsum("ij,jk,ik->i", w, cov, w))
    assert result["max_sharpe"]["sharpe_ratio"] >= round(float(random_sharpes.max()), 2)
    assert sum(result["max_sharpe"]["weights"].values()) == pytest.approx(100.0, abs=0.05)
    assert result["current"]["weights"]["A"] == pytest.approx(20.0)


def test_holdings_history_values_current_quantities_on_past_prices():
    from app.quant.performance import compute_holdings_history
    close = pl.DataFrame({"market_date": D[:3], "X": [100.0, 110.0, 99.0], "Y": [50.0, 50.0, 55.0], "B": [10.0, 11.0, 12.0]})
    series = compute_holdings_history({"X": 2, "Y": 4, "Z": 0}, close, benchmark="B")
    assert [p["portfolio_value"] for p in series] == [400.0, 420.0, 418.0]   # 2·X + 4·Y à chaque date
    assert series[-1]["cumulative_gain"] == pytest.approx(18.0)
    assert series[-1]["twr_index"] == pytest.approx(104.5)
    assert [p["benchmark_value"] for p in series] == [400.0, 440.0, 480.0]   # indice ramené à 400 €
    assert series[0]["invested_capital"] is None
