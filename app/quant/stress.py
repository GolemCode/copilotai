"""
Scénarios de stress historiques calculés sur les cours réellement stockés en base (convertis en EUR).

Principe : on rejoue sur le portefeuille ACTUEL (poids actuels, détention sans arbitrage) les pires
épisodes observés dans l'historique disponible. Chaque ligne reçoit le rendement que son actif a
réellement réalisé sur la période : aucun choc n'est fixé à la main.
"""
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import numpy.typing as npt
import polars as pl

from app.quant.estimation import dimson_beta

MONTH_TRADING_DAYS = 21


def _window_returns(prices: npt.NDArray[np.float64], start: int, end: int) -> npt.NDArray[np.float64]:
    return prices[end] / prices[start] - 1.0


def _scenario(
    scenario_id: str, name: str, description: str, dates: List[Any],
    prices: npt.NDArray[np.float64], weights: npt.NDArray[np.float64], tickers: Sequence[str],
    start: int, end: int,
) -> Dict[str, Any]:
    asset_returns = _window_returns(prices, start, end)
    return {
        "id": scenario_id,
        "name": name,
        "description": description,
        "start_date": str(dates[start]),
        "end_date": str(dates[end]),
        "trading_days": int(end - start),
        "portfolio_return_pct": round(float(asset_returns @ weights) * 100.0, 2),
        "asset_returns_pct": {t: round(float(r) * 100.0, 2) for t, r in zip(tickers, asset_returns)},
    }


def worst_buy_and_hold_period(prices: npt.NDArray[np.float64], weights: npt.NDArray[np.float64]) -> tuple:
    """
    Fenêtre [s, e] (s < e) qui minimise le rendement d'un portefeuille acheté en s avec les poids
    actuels et conservé jusqu'en e : R(s, e) = Σ w_i · P_i(e) / P_i(s) − 1. Recherche exhaustive O(T²).
    """
    n_days = prices.shape[0]
    total = np.zeros((n_days, n_days))
    for i in range(prices.shape[1]):
        total += weights[i] * np.outer(1.0 / prices[:, i], prices[:, i])
    total -= 1.0
    total[np.tril_indices(n_days)] = np.inf  # uniquement e > s
    start, end = np.unravel_index(np.argmin(total), total.shape)
    return int(start), int(end)


def worst_rolling_window(series_returns: npt.NDArray[np.float64]) -> int:
    """Indice de départ de la pire fenêtre glissante (rendements déjà calculés par fenêtre)."""
    return int(np.argmin(series_returns))


def compute_historical_stress(
    prices_df: pl.DataFrame,
    tickers: Sequence[str],
    weights: npt.NDArray[np.float64],
    market_ticker: Optional[str] = None,
    window: int = MONTH_TRADING_DAYS,
) -> Dict[str, Any]:
    """
    prices_df : market_date + une colonne de cours ajusté (EUR) par ticker (et éventuellement le marché).
    weights   : poids actuels des actifs cotés (somme = 1), dans l'ordre de `tickers`.
    """
    dates = prices_df["market_date"].to_list()
    prices = prices_df.select(list(tickers)).to_numpy().astype(float)
    weights = np.asarray(weights, dtype=float)
    n_days = prices.shape[0]
    if n_days <= window:
        raise ValueError(f"Historique insuffisant : {n_days} séances pour une fenêtre de {window}.")

    scenarios: List[Dict[str, Any]] = []
    period = f"{dates[0]} → {dates[-1]}"

    s, e = worst_buy_and_hold_period(prices, weights)
    scenarios.append(_scenario(
        "worst_period", "Pire baisse historique",
        f"Plus forte perte d'un achat suivi d'une détention du portefeuille actuel, sur l'historique disponible ({period}).",
        dates, prices, weights, tickers, s, e,
    ))

    window_returns = (prices[window:] / prices[:-window] - 1.0) @ weights
    s = worst_rolling_window(window_returns)
    scenarios.append(_scenario(
        "worst_month", "Pire mois du portefeuille",
        f"Pire variation sur {window} séances consécutives du portefeuille actuel ({period}).",
        dates, prices, weights, tickers, s, s + window,
    ))

    betas: Dict[str, Optional[float]] = {t: None for t in tickers}
    if market_ticker and market_ticker in prices_df.columns:
        market = prices_df[market_ticker].to_numpy().astype(float)
        market_window = market[window:] / market[:-window] - 1.0
        s = worst_rolling_window(market_window)
        scenarios.append(_scenario(
            "worst_market_month", f"Pire mois du marché ({market_ticker})",
            f"Pire variation sur {window} séances de l'indice de référence {market_ticker} ({period}) : "
            f"rendement réalisé par chaque ligne sur la même période.",
            dates, prices, weights, tickers, s, s + window,
        ))

        # Bêta de Dimson (covariances décalées d'une séance incluses) : les clôtures de New York et de
        # Paris n'étant pas synchrones, le bêta quotidien simple sous-estime l'exposition d'un actif US
        daily = prices[1:] / prices[:-1] - 1.0
        market_daily = market[1:] / market[:-1] - 1.0
        for i, t in enumerate(tickers):
            beta = dimson_beta(daily[:, i], market_daily)
            betas[t] = round(beta, 3) if np.isfinite(beta) else None

    return {
        "history_start": str(dates[0]),
        "history_end": str(dates[-1]),
        "market_ticker": market_ticker if market_ticker in prices_df.columns else None,
        "betas": betas,
        "scenarios": scenarios,
    }
