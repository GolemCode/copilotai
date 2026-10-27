"""
Optimisation moyenne-variance de Markowitz, long-only (0 ≤ w_i ≤ 1, Σ w_i = 1).

Résolution numérique sous contraintes (SLSQP, scipy) :
- portefeuille de variance minimale : min w'Σw ;
- portefeuille de Sharpe maximal (tangence) : max (w'μ − rf) / √(w'Σw), plusieurs points de départ ;
- frontière efficiente : pour chaque rendement cible, min w'Σw sous w'μ = cible.

Limite à connaître : μ est estimé par la moyenne historique des rendements, très bruitée ;
les allocations optimales sont donc instables (Michaud, 1989). Résultat indicatif, pas une recommandation.
"""
from typing import Any, Dict, List, Optional

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

_BUDGET = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}


def _stats(w: npt.NDArray[np.float64], mu: npt.NDArray[np.float64], cov: npt.NDArray[np.float64], rf: float) -> Dict[str, float]:
    ret = float(w @ mu)
    vol = float(np.sqrt(max(w @ cov @ w, 0.0)))
    return {"ret": ret, "vol": vol, "sharpe": (ret - rf) / vol if vol > 1e-12 else 0.0}


def _solve(objective, n: int, constraints: List[dict], x0: Optional[npt.NDArray[np.float64]] = None) -> npt.NDArray[np.float64]:
    x0 = np.full(n, 1.0 / n) if x0 is None else x0
    res = minimize(objective, x0, method="SLSQP", bounds=[(0.0, 1.0)] * n, constraints=constraints,
                   options={"ftol": 1e-12, "maxiter": 500})
    w = np.clip(res.x, 0.0, 1.0)
    return w / w.sum()


def min_variance_weights(cov: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    n = cov.shape[0]
    return _solve(lambda w: w @ cov @ w, n, [_BUDGET])


def max_sharpe_weights(mu: npt.NDArray[np.float64], cov: npt.NDArray[np.float64], rf: float) -> npt.NDArray[np.float64]:
    n = len(mu)

    def neg_sharpe(w):
        vol = np.sqrt(max(w @ cov @ w, 1e-16))
        return -(w @ mu - rf) / vol

    # Le ratio de Sharpe n'est pas concave : plusieurs départs (équipondéré + chaque actif pur)
    starts = [np.full(n, 1.0 / n)] + [np.where(np.arange(n) == i, 0.9, 0.1 / max(n - 1, 1)) for i in range(n)]
    candidates = [_solve(neg_sharpe, n, [_BUDGET], x0) for x0 in starts]
    return min(candidates, key=neg_sharpe)


def efficient_frontier(mu: npt.NDArray[np.float64], cov: npt.NDArray[np.float64], n_points: int = 25) -> List[npt.NDArray[np.float64]]:
    """Portefeuilles efficients entre la variance minimale et le rendement maximal atteignable."""
    w_min = min_variance_weights(cov)
    targets = np.linspace(float(w_min @ mu), float(np.max(mu)), n_points)
    frontier = []
    for target in targets:
        cons = [_BUDGET, {"type": "eq", "fun": lambda w, t=target: w @ mu - t}]
        frontier.append(_solve(lambda w: w @ cov @ w, len(mu), cons, w_min))
    return frontier


def optimize_portfolio(
    expected_returns: npt.NDArray[np.float64],
    covariance_matrix: npt.NDArray[np.float64],
    tickers: List[str],
    risk_free_rate: float = 0.0,
    current_weights: Optional[npt.NDArray[np.float64]] = None,
) -> Dict[str, Any]:
    """
    Args:
        expected_returns: rendements moyens annualisés (N,) — moyenne arithmétique × 252
        covariance_matrix: matrice de covariance annualisée (N, N)
        tickers: symboles des actifs (au moins 2)
        risk_free_rate: taux sans risque annuel
        current_weights: poids actuels (optionnel) pour situer le portefeuille sur le graphique
    """
    mu = np.asarray(expected_returns, dtype=float)
    cov = np.asarray(covariance_matrix, dtype=float)
    if len(tickers) < 2:
        raise ValueError("L'optimisation nécessite au moins deux actifs.")

    def describe(w):
        s = _stats(w, mu, cov, risk_free_rate)
        return {
            "weights": {t: round(float(x) * 100, 2) for t, x in zip(tickers, w)},
            "annualized_return_pct": round(s["ret"] * 100, 2),
            "annualized_volatility_pct": round(s["vol"] * 100, 2),
            "sharpe_ratio": round(s["sharpe"], 2),
        }

    frontier = []
    for w in efficient_frontier(mu, cov):
        s = _stats(w, mu, cov, risk_free_rate)
        frontier.append({
            "volatility": round(s["vol"] * 100, 2),
            "return": round(s["ret"] * 100, 2),
            "sharpe": round(s["sharpe"], 2),
        })

    result = {
        "max_sharpe": describe(max_sharpe_weights(mu, cov, risk_free_rate)),
        "min_volatility": describe(min_variance_weights(cov)),
        "efficient_frontier": frontier,
        "risk_free_rate_pct": round(risk_free_rate * 100, 2),
    }
    if current_weights is not None:
        w = np.asarray(current_weights, dtype=float)
        result["current"] = describe(w / w.sum())
    return result
