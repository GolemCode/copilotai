"""
Projection d'un plan d'épargne (mise de départ + versements mensuels) sur plusieurs années.

Pas de temps mensuel : le portefeuille, rééquilibré à poids constants, suit un mouvement brownien géométrique
de volatilité σ_p = √(wᵀΣw) estimée sur l'historique. Le rendement annuel espéré n'est PAS estimé
(le passé est un mauvais prédicteur de la tendance) : il est fixé par trois hypothèses explicites,
qui partagent les mêmes tirages aléatoires pour être comparables entre elles.
"""
from typing import Any, Dict, List, Optional

import numpy as np
import numpy.typing as npt

TRADING_DAYS_PER_YEAR = 252
MONTHS_PER_YEAR = 12

# Hypothèses de rendement annuel moyen (en espérance), affichées telles quelles à l'utilisateur
RETURN_SCENARIOS: List[Dict[str, Any]] = [
    {"key": "prudent", "label": "Prudente", "annual_return": 0.00},
    {"key": "central", "label": "Centrale", "annual_return": 0.04},
    {"key": "favorable", "label": "Favorable", "annual_return": 0.07},
]
PERCENTILES = (5, 25, 50, 75, 95)


def portfolio_daily_volatility(weights: npt.ArrayLike, daily_covariance: npt.ArrayLike) -> float:
    """Volatilité journalière d'un portefeuille rééquilibré : √(wᵀΣw)."""
    w = np.asarray(weights, dtype=float)
    cov = np.atleast_2d(np.asarray(daily_covariance, dtype=float))
    return float(np.sqrt(max(w @ cov @ w, 0.0)))


def invested_capital(initial_amount: float, monthly_contribution: float, months: int) -> npt.NDArray[np.float64]:
    """Capital cumulé versé à chaque fin de mois (mois 0 = mise de départ)."""
    return initial_amount + monthly_contribution * np.arange(months + 1, dtype=float)


def simulate_savings_paths(
    annual_return: float,
    annual_volatility: float,
    initial_amount: float,
    monthly_contribution: float,
    shocks: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """
    Valeur du plan à chaque fin de mois, pour chaque trajectoire (num_paths × (mois + 1)).
    Le versement mensuel est investi en fin de mois, après l'évolution du mois.
    Calibrage : E[croissance sur 12 mois] = 1 + annual_return, donc μ_log mensuel = ln(1 + r)/12 − σ²/2.
    """
    sigma_m = annual_volatility / np.sqrt(MONTHS_PER_YEAR)
    mu_m = np.log1p(annual_return) / MONTHS_PER_YEAR - 0.5 * sigma_m ** 2
    growth = np.exp(mu_m + sigma_m * shocks)

    num_paths, months = shocks.shape
    values = np.empty((num_paths, months + 1))
    values[:, 0] = initial_amount
    for m in range(months):
        values[:, m + 1] = values[:, m] * growth[:, m] + monthly_contribution
    return values


def project_savings_plan(
    daily_volatility: float,
    initial_amount: float,
    monthly_contribution: float,
    years: int,
    num_paths: int = 5000,
    seed: Optional[int] = None,
    scenarios: List[Dict[str, Any]] = RETURN_SCENARIOS,
) -> Dict[str, Any]:
    """
    Projette le plan sous chaque hypothèse de rendement.
    Retourne, par hypothèse : les centiles mois par mois (bandes du graphique) et un résumé à l'horizon
    (médiane, fourchette à 90 %, probabilité de finir sous le capital versé).
    """
    if initial_amount < 0 or monthly_contribution < 0:
        raise ValueError("Les montants doivent être positifs.")
    if initial_amount + monthly_contribution <= 0:
        raise ValueError("Indiquez une mise de départ ou un versement mensuel.")
    if years < 1:
        raise ValueError("L'horizon doit être d'au moins un an.")

    months = int(years) * MONTHS_PER_YEAR
    annual_volatility = daily_volatility * np.sqrt(TRADING_DAYS_PER_YEAR)
    shocks = np.random.default_rng(seed).standard_normal((num_paths, months))
    invested = invested_capital(initial_amount, monthly_contribution, months)

    results = []
    for scenario in scenarios:
        values = simulate_savings_paths(scenario["annual_return"], annual_volatility,
                                        initial_amount, monthly_contribution, shocks)
        bands = np.percentile(values, PERCENTILES, axis=0)
        final = values[:, -1]
        results.append({
            **scenario,
            "timeline": [
                {"month": m, **{f"p{p}": round(float(bands[i, m]), 2) for i, p in enumerate(PERCENTILES)}}
                for m in range(months + 1)
            ],
            "final": {
                "median": round(float(np.median(final)), 2),
                "p5": round(float(np.percentile(final, 5)), 2),
                "p95": round(float(np.percentile(final, 95)), 2),
                "mean": round(float(final.mean()), 2),
                "probability_below_invested": round(float((final < invested[-1]).mean()), 4),
            },
        })

    return {
        "months": months,
        "annual_volatility": round(float(annual_volatility), 4),
        "invested": [round(float(v), 2) for v in invested],
        "total_invested": round(float(invested[-1]), 2),
        "scenarios": results,
    }
