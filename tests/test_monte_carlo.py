import numpy as np
import pytest

from app.quant.monte_carlo import generate_monte_carlo_simulation

# Quantile 5 % de la loi normale centrée réduite
Z_005 = -1.6448536269514722

INITIAL = 10_000.0
HORIZON = 252
DAILY_LOG_MEAN = 0.0002   # moyenne des log-rendements journaliers (μ - σ²/2 déjà incluse)
DAILY_VOL = 0.02


def _simulate(seed, num_paths=20_000, log_mean=DAILY_LOG_MEAN, vol=DAILY_VOL):
    return generate_monte_carlo_simulation(
        expected_returns=np.array([log_mean]),
        covariance_matrix=np.array([[vol ** 2]]),
        weights=np.array([1.0]),
        num_paths=num_paths,
        time_horizon_days=HORIZON,
        initial_investment=INITIAL,
        seed=seed,
    )


def test_var_and_es_are_positive_losses_matching_lognormal_closed_form():
    res = _simulate(seed=42)

    # Valeur finale ~ INITIAL * exp(N(m T, σ² T)) : VaR 95 % = INITIAL - quantile 5 % de cette loi
    m_T, s_T = DAILY_LOG_MEAN * HORIZON, DAILY_VOL * np.sqrt(HORIZON)
    expected_var = INITIAL - INITIAL * np.exp(m_T + Z_005 * s_T)

    assert res["value_at_risk_95"] > 0, "La VaR doit être exprimée comme une perte positive"
    assert res["value_at_risk_95"] == pytest.approx(expected_var, rel=0.03)
    # L'Expected Shortfall est toujours au moins égale à la VaR
    assert res["expected_shortfall_95"] >= res["value_at_risk_95"]


def test_drift_is_not_corrected_twice():
    res = _simulate(seed=7)

    # Médiane de la valeur finale = INITIAL * exp(m T) si la dérive log est utilisée telle quelle.
    # Avec la double correction (m - σ²/2), elle tomberait à INITIAL * exp((m - σ²/2) T) ≈ 10 000 €.
    expected_median = INITIAL * np.exp(DAILY_LOG_MEAN * HORIZON)
    double_corrected = INITIAL * np.exp((DAILY_LOG_MEAN - 0.5 * DAILY_VOL ** 2) * HORIZON)

    median = res["metrics"]["median_absolute"]
    assert median == pytest.approx(expected_median, rel=0.01)
    assert abs(median - expected_median) < abs(median - double_corrected)


def test_same_seed_gives_identical_results():
    a = _simulate(seed=123, num_paths=2_000)
    b = _simulate(seed=123, num_paths=2_000)

    assert a["value_at_risk_95"] == b["value_at_risk_95"]
    assert a["expected_shortfall_95"] == b["expected_shortfall_95"]
    assert a["paths"] == b["paths"]


def test_different_seeds_give_different_results():
    a = _simulate(seed=1, num_paths=2_000)
    b = _simulate(seed=2, num_paths=2_000)

    assert a["value_at_risk_95"] != b["value_at_risk_95"]


def test_zero_drift_keeps_expected_value_unchanged():
    from app.quant.monte_carlo import select_drift
    cov = np.array([[DAILY_VOL ** 2]])
    drift = select_drift(np.array([DAILY_LOG_MEAN]), cov, "zero")
    assert drift[0] == pytest.approx(-0.5 * DAILY_VOL ** 2)  # martingale : E[valeur finale] = mise
    res = generate_monte_carlo_simulation(drift, cov, np.array([1.0]), 20_000, HORIZON, INITIAL, seed=11)
    historical = _simulate(seed=11)
    # Sans tendance supposée, la VaR est nettement plus élevée qu'avec la moyenne historique positive
    assert res["value_at_risk_95"] > historical["value_at_risk_95"]
    assert res["metrics"]["median_absolute"] == pytest.approx(INITIAL * np.exp(-0.5 * DAILY_VOL ** 2 * HORIZON), rel=0.01)


def test_unknown_drift_mode_is_rejected():
    from app.quant.monte_carlo import select_drift
    with pytest.raises(ValueError):
        select_drift(np.array([0.0]), np.array([[1e-4]]), "momentum")
