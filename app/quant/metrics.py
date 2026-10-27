import numpy as np
import numpy.typing as npt
import polars as pl
from typing import Dict, Any, Optional, List

from app.quant.ratios import (
    TRADING_DAYS,
    annualized_volatility,
    cagr,
    max_drawdown,
    round_or_none,
    sharpe_ratio,
    sortino_ratio,
)


def compute_portfolio_metrics(
    daily_prices_df: pl.DataFrame,
    tickers: List[str],
    weights: npt.NDArray[np.float64],
    initial_investment: float = 10000.0,
    risk_free_rate: float = 0.0,
    benchmark_col: Optional[str] = None,
    target_final_value: Optional[float] = None
) -> Dict[str, Any]:
    """
    Métriques de risque EX-ANTE de la composition actuelle du portefeuille.

    Convention : les poids actuels sont appliqués à chaque séance de l'historique
    (R_p,t = Σ w_i · r_i,t), ce qui revient à un portefeuille rééquilibré quotidiennement.
    C'est l'approche standard pour estimer le risque de l'allocation d'aujourd'hui ; ce n'est PAS
    la performance réellement obtenue par l'investisseur (voir app/quant/performance.py).

    Args:
        daily_prices_df: DataFrame Polars ordonné par date contenant les prix ajustés (EUR)
        tickers: Liste des symboles d'actifs inclus dans le portefeuille
        weights: Vecteur numpy des pondérations (somme = 1.0)
        initial_investment: Montant de départ de la série de valeur (si target_final_value absent)
        risk_free_rate: Taux sans risque annuel (ex: 0.025 pour 2,5 %)
        benchmark_col: Colonne éventuelle de référence (ex: 'SPY')
        target_final_value: Valeur actuelle du portefeuille : la série est recalée pour finir à cette valeur
    """
    if daily_prices_df.height < 5:
        raise ValueError("Historique de prix insuffisant pour calculer des métriques fiables (minimum 5 jours).")

    # 1. Rendements simples journaliers par actif
    returns_expressions = [
        ((pl.col(t) - pl.col(t).shift(1)) / pl.col(t).shift(1)).alias(f"ret_{t}")
        for t in tickers
    ]
    if benchmark_col and benchmark_col in daily_prices_df.columns:
        returns_expressions.append(
            ((pl.col(benchmark_col) - pl.col(benchmark_col).shift(1)) / pl.col(benchmark_col).shift(1)).alias("ret_benchmark")
        )

    returns_df = daily_prices_df.select(["market_date"] + returns_expressions).drop_nulls()

    if returns_df.height < 3:
        raise ValueError("Nombre de rendements valides insuffisant.")

    # 2. Matrice des rendements des actifs (T, N), nettoyée des valeurs non finies
    asset_returns_matrix = returns_df.select([f"ret_{t}" for t in tickers]).to_numpy()
    valid_mask = np.isfinite(asset_returns_matrix).all(axis=1)
    asset_returns_matrix = asset_returns_matrix[valid_mask]
    dates = returns_df["market_date"].to_list()
    dates = [dates[i] for i in range(len(dates)) if valid_mask[i]]

    # 3. Rendement journalier du portefeuille à poids actuels : R_p = X · w
    portfolio_daily_returns = asset_returns_matrix @ weights
    full_wealth_index = np.insert(np.cumprod(1.0 + portfolio_daily_returns), 0, 1.0)
    n_days = len(portfolio_daily_returns)

    # 4. Rendement composé (CAGR), volatilité et ratios (convention unique, cf. ratios.py)
    annualized_return = cagr(float(full_wealth_index[-1]), n_days)
    annualized_vol = annualized_volatility(portfolio_daily_returns)
    sharpe = sharpe_ratio(portfolio_daily_returns, risk_free_rate)
    sortino = sortino_ratio(portfolio_daily_returns, risk_free_rate)
    mdd = max_drawdown(full_wealth_index)

    # 5. Série de valorisation (recalée sur la valeur actuelle si fournie)
    if target_final_value is not None and target_final_value > 0:
        growth_factor = float(full_wealth_index[-1])
        start_value = (target_final_value / growth_factor) if growth_factor > 1e-6 else target_final_value
        reported_initial_invest = round(start_value, 2)
        reported_final_value = round(target_final_value, 2)
    else:
        start_value = initial_investment
        reported_initial_invest = initial_investment
        reported_final_value = round(float(full_wealth_index[-1] * initial_investment), 2)
    portfolio_value_series = full_wealth_index * start_value
    drawdowns = portfolio_value_series / np.maximum.accumulate(portfolio_value_series) - 1.0

    # 6. Benchmark comparatif, mêmes conventions que le portefeuille
    benchmark_series = None
    benchmark_block = None
    if benchmark_col and "ret_benchmark" in returns_df.columns:
        bench_ret = returns_df["ret_benchmark"].to_numpy()[valid_mask]
        bench_wealth = np.insert(np.cumprod(1.0 + bench_ret), 0, 1.0)
        benchmark_series = bench_wealth * start_value
        benchmark_block = {
            "ticker": benchmark_col,
            "annualized_return_pct": round_or_none(cagr(float(bench_wealth[-1]), len(bench_ret)), scale=100),
            "annualized_volatility_pct": round_or_none(annualized_volatility(bench_ret), scale=100),
            "sharpe_ratio": round_or_none(sharpe_ratio(bench_ret, risk_free_rate)),
        }

    # 7. Séries temporelles pour le graphique
    time_series = []
    first_date_str = str(dates[0]) if dates else "Start"
    pt0 = {"date": first_date_str, "portfolio_value": round(float(portfolio_value_series[0]), 2), "drawdown_pct": 0.0}
    if benchmark_series is not None:
        pt0["benchmark_value"] = round(float(benchmark_series[0]), 2)
    time_series.append(pt0)
    for i in range(len(dates)):
        pt = {
            "date": str(dates[i]),
            "portfolio_value": round(float(portfolio_value_series[i + 1]), 2),
            "drawdown_pct": round(float(drawdowns[i + 1] * 100), 2),
        }
        if benchmark_series is not None:
            pt["benchmark_value"] = round(float(benchmark_series[i + 1]), 2)
        time_series.append(pt)

    # 8. Analyse par actif, mêmes conventions (CAGR, Sharpe ex-post)
    asset_breakdown = []
    current_valuation_base = target_final_value if (target_final_value is not None and target_final_value > 0) else initial_investment
    for idx, ticker in enumerate(tickers):
        ret_i = asset_returns_matrix[:, idx]
        w_i = float(weights[idx])
        asset_breakdown.append({
            "ticker": ticker,
            "weight": round(w_i, 4),
            "weight_pct": round(w_i * 100, 2),
            "position_value": round(float(w_i * current_valuation_base), 2),
            "annualized_return": round_or_none(cagr(float(np.prod(1.0 + ret_i)), len(ret_i)), scale=100),
            "annualized_volatility": round_or_none(annualized_volatility(ret_i), scale=100),
            "sharpe_ratio": round_or_none(sharpe_ratio(ret_i, risk_free_rate)),
        })

    final_value = reported_final_value
    total_pnl = round(final_value - reported_initial_invest, 2)
    total_return_pct = round((total_pnl / reported_initial_invest) * 100, 2) if reported_initial_invest > 0 else 0.0

    return {
        "summary": {
            "methodology": "ex_ante_current_weights",
            "initial_investment": reported_initial_invest,
            "final_value": final_value,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "annualized_return_pct": round_or_none(annualized_return, scale=100),
            "annualized_volatility_pct": round_or_none(annualized_vol, scale=100),
            "sharpe_ratio": round_or_none(sharpe),
            "sortino_ratio": round_or_none(sortino),
            "max_drawdown_pct": round(mdd * 100, 2),
            "risk_free_rate_pct": round(risk_free_rate * 100, 2),
            "history_days": len(dates),
            "trading_days_per_year": TRADING_DAYS,
        },
        "benchmark": benchmark_block if benchmark_col else None,
        "asset_breakdown": asset_breakdown,
        "time_series": time_series,
    }
