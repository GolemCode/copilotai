"""
Ratios de performance et de risque : UNE seule convention pour tout le projet
(métriques du portefeuille, benchmark, performance réelle, optimisation).

- Rendements journaliers simples ; annualisation sur 252 séances.
- Sharpe ex-post (Sharpe, 1994) : moyenne des rendements excédentaires / écart-type, × √252.
- Sortino : moyenne des rendements excédentaires annualisée / écart à la baisse (downside deviation),
  calculé sur TOUTES les séances : √(moyenne de min(r − rf, 0)²) × √252.
- Taux sans risque journalier : (1 + rf annuel)^(1/252) − 1.
"""
from typing import Optional

import numpy as np
import numpy.typing as npt

TRADING_DAYS = 252
MIN_OBSERVATIONS = 20  # en dessous, volatilité et ratios ne sont pas significatifs


def daily_risk_free(rf_annual: float) -> float:
    return (1.0 + rf_annual) ** (1.0 / TRADING_DAYS) - 1.0


def cagr(growth: float, n_periods: int, periods_per_year: float = TRADING_DAYS) -> Optional[float]:
    """Taux de croissance annuel composé à partir d'un facteur de croissance sur n périodes."""
    if growth <= 0 or n_periods <= 0:
        return None
    return float(growth ** (periods_per_year / n_periods) - 1.0)


def annualized_volatility(daily_returns: npt.NDArray[np.float64]) -> Optional[float]:
    r = np.asarray(daily_returns, dtype=float)
    if r.size < 2:
        return None
    return float(np.std(r, ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe_ratio(daily_returns: npt.NDArray[np.float64], rf_annual: float) -> Optional[float]:
    r = np.asarray(daily_returns, dtype=float)
    if r.size < 2:
        return None
    sd = float(np.std(r, ddof=1))
    if sd <= 1e-12:
        return None
    excess = r - daily_risk_free(rf_annual)
    return float(np.mean(excess) / sd * np.sqrt(TRADING_DAYS))


def downside_deviation(daily_returns: npt.NDArray[np.float64], rf_annual: float) -> Optional[float]:
    """Écart à la baisse annualisé : seules les séances sous le taux sans risque pénalisent."""
    r = np.asarray(daily_returns, dtype=float)
    if r.size < 2:
        return None
    shortfall = np.minimum(r - daily_risk_free(rf_annual), 0.0)
    return float(np.sqrt(np.mean(shortfall ** 2)) * np.sqrt(TRADING_DAYS))


def sortino_ratio(daily_returns: npt.NDArray[np.float64], rf_annual: float) -> Optional[float]:
    r = np.asarray(daily_returns, dtype=float)
    dd = downside_deviation(r, rf_annual)
    if dd is None or dd <= 1e-12:
        return None
    excess = r - daily_risk_free(rf_annual)
    return float(np.mean(excess) * TRADING_DAYS / dd)


def max_drawdown(wealth_index: npt.NDArray[np.float64]) -> float:
    """Perte maximale depuis un plus haut (valeur négative, ex. -0.18 pour -18 %)."""
    w = np.asarray(wealth_index, dtype=float)
    if w.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(w)
    return float(np.min(w / running_max - 1.0))


def round_or_none(value: Optional[float], digits: int = 2, scale: float = 1.0) -> Optional[float]:
    return None if value is None else round(float(value) * scale, digits)
