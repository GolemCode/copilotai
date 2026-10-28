import numpy as np
import pytest

from app.quant.projection import (
    invested_capital,
    portfolio_daily_volatility,
    project_savings_plan,
    simulate_savings_paths,
)


def test_invested_capital_counts_initial_and_monthly_contributions():
    assert invested_capital(1000, 100, 12).tolist() == [1000 + 100 * m for m in range(13)]


def test_without_volatility_plan_follows_compound_interest():
    """Sans aléa, 1 000 € à 4 % pendant un an valent exactement 1 040 €."""
    shocks = np.zeros((1, 12))
    values = simulate_savings_paths(0.04, 0.0, 1000, 0, shocks)
    assert values[0, -1] == pytest.approx(1040.0)


def test_expected_growth_matches_annual_return_hypothesis():
    """E[valeur à 1 an] = mise × (1 + r), quelle que soit la volatilité."""
    shocks = np.random.default_rng(0).standard_normal((200_000, 12))
    values = simulate_savings_paths(0.07, 0.20, 1000, 0, shocks)
    assert values[:, -1].mean() == pytest.approx(1070.0, rel=0.01)


def test_portfolio_volatility_accounts_for_diversification():
    cov = np.array([[0.0001, 0.0], [0.0, 0.0001]])
    assert portfolio_daily_volatility([0.5, 0.5], cov) == pytest.approx(0.01 / np.sqrt(2))


def test_projection_orders_scenarios_and_reports_invested_capital():
    result = project_savings_plan(0.01, 1000, 200, years=10, num_paths=2000, seed=42)
    assert result["months"] == 120
    assert result["total_invested"] == pytest.approx(1000 + 200 * 120)
    medians = [s["final"]["median"] for s in result["scenarios"]]
    assert medians == sorted(medians)
    for scenario in result["scenarios"]:
        last = scenario["timeline"][-1]
        assert last["p5"] <= last["p25"] <= last["p50"] <= last["p75"] <= last["p95"]
    # Hypothèse prudente (rendement nul) : la volatilité seule fait finir sous les versements plus d'une fois sur deux
    assert result["scenarios"][0]["final"]["probability_below_invested"] > 0.5


def test_projection_rejects_empty_plan():
    with pytest.raises(ValueError):
        project_savings_plan(0.01, 0, 0, years=5)
