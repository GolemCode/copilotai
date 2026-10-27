"""
Performance RÉELLE du portefeuille, reconstituée à partir du journal des transactions.

Méthode (valorisation quotidienne, flux en cours de journée) :
- Gain du jour = variation des positions détenues la veille (rendement total : cours ajusté,
  dividende inclus) + écart entre le prix de transaction et la clôture pour les opérations du jour.
- Rendement du jour R_t = gain / (valeur de la veille + achats du jour)  (Dietz modifié journalier).
- TWR (rendement pondéré dans le temps) = Π(1 + R_t) − 1 : neutralise l'effet des apports et
  retraits, c'est la mesure de la qualité de gestion (norme GIPS).
- MWR (rendement pondéré par les capitaux, XIRR) : taux qui annule la valeur actuelle des flux ;
  il dépend du calendrier des versements, c'est la performance vécue par l'investisseur.
- Règle GIPS : une performance sur moins d'un an n'est pas annualisée.
- Richesse = valeur de marché + dividendes perçus (non réinvestis), en EUR.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import polars as pl

from app.quant.ratios import (
    MIN_OBSERVATIONS,
    annualized_volatility,
    max_drawdown,
    round_or_none,
    sharpe_ratio,
    sortino_ratio,
)

DAYS_PER_YEAR = 365.0  # convention de la fonction XIRR d'Excel (base exact/365)


def xirr(cash_flows: Sequence[tuple]) -> Optional[float]:
    """
    Taux de rendement interne annualisé de flux datés [(date, montant)] :
    montants négatifs = apports de l'investisseur, positifs = retraits / valeur finale.
    Résolution par dichotomie sur la valeur actuelle nette (monotone pour ce profil de flux).
    """
    flows = [(d, float(a)) for d, a in cash_flows if abs(a) > 1e-9]
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** ((d - t0).days / DAYS_PER_YEAR) for d, a in flows)

    low, high = -0.9999, 10.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            break
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2.0


def compute_realized_performance(
    transactions: List[Dict[str, Any]],
    adjusted_eur: pl.DataFrame,
    close_eur: pl.DataFrame,
    benchmark: Optional[str] = None,
    risk_free_rate: float = 0.0,
) -> Dict[str, Any]:
    """
    transactions : dicts {ticker, trade_date (date), side ('buy'|'sell'), quantity, price_eur}
    adjusted_eur / close_eur : market_date + une colonne par ticker (cours ajustés / bruts, en EUR)
    """
    panel = adjusted_eur.join(close_eur, on="market_date", how="inner", suffix="__close").sort("market_date")
    dates: List[date] = panel["market_date"].to_list()
    tickers = sorted({t["ticker"] for t in transactions if t["ticker"] in adjusted_eur.columns and f"{t['ticker']}__close" in panel.columns})
    excluded = sorted({t["ticker"] for t in transactions} - set(tickers))
    if not tickers or len(dates) < 2:
        raise ValueError("Aucune transaction sur un actif disposant d'un historique de cours.")

    n_days, n_assets = len(dates), len(tickers)
    col = {t: i for i, t in enumerate(tickers)}
    adj = panel.select(tickers).to_numpy().astype(float)
    close = panel.select([f"{t}__close" for t in tickers]).to_numpy().astype(float)
    dates_np = np.array(dates, dtype="datetime64[D]")

    dq = np.zeros((n_days, n_assets))       # variation des quantités détenues
    trade_gain = np.zeros(n_days)            # (clôture − prix) des opérations du jour
    flows = np.zeros(n_days)                 # apports (+) / retraits (−) de l'investisseur
    buys = np.zeros(n_days)
    cash_flows: List[tuple] = []
    truncated_before: Optional[date] = None

    for tx in transactions:
        if tx["ticker"] not in col:
            continue
        i = col[tx["ticker"]]
        qty = float(tx["quantity"])
        price = float(tx["price_eur"])
        trade_date = tx["trade_date"]
        idx = int(np.searchsorted(dates_np, np.datetime64(trade_date, "D"), side="left"))
        if idx >= n_days:
            idx = n_days - 1  # opération postérieure au dernier cours disponible
        if trade_date < dates[0]:
            # Opération antérieure à l'historique de cours : intégrée au premier jour, à sa clôture
            truncated_before = dates[0]
            price, trade_date = close[0, i], dates[0]
        sign = 1.0 if tx["side"] == "buy" else -1.0
        dq[idx, i] += sign * qty
        amount = qty * price
        flows[idx] += sign * amount
        trade_gain[idx] += sign * qty * (close[idx, i] - price)
        if sign > 0:
            buys[idx] += amount
        cash_flows.append((trade_date, -sign * amount))

    holdings = np.cumsum(dq, axis=0)                     # quantités en fin de journée
    held_before = np.vstack([np.zeros((1, n_assets)), holdings[:-1]])
    prev_close = np.vstack([close[:1], close[:-1]])
    asset_returns = np.vstack([np.zeros((1, n_assets)), adj[1:] / adj[:-1] - 1.0])  # rendement total (dividendes inclus)

    value_before = np.sum(held_before * prev_close, axis=1)
    market_gain = np.sum(held_before * prev_close * asset_returns, axis=1)
    gain = market_gain + trade_gain
    base = value_before + buys
    active = base > 1e-9
    daily_returns = np.where(active, gain / np.where(active, base, 1.0), 0.0)

    start = int(np.argmax(active))  # premier jour avec du capital investi
    # Indice TWR (1,0 avant le premier achat ; les jours sans capital investi ont un rendement nul)
    twr_index = np.cumprod(1.0 + daily_returns)
    wealth = np.cumsum(gain + flows)                      # valeur de marché + dividendes perçus
    market_value = np.sum(holdings * close, axis=1)
    invested = np.cumsum(flows)

    # Benchmark : mêmes versements investis dans l'indice de référence (au cours de clôture du jour)
    bench_index = bench_wealth = None
    if benchmark and benchmark in adjusted_eur.columns:
        b_adj = panel[benchmark].to_numpy().astype(float)
        b_ret = np.concatenate([[0.0], b_adj[1:] / b_adj[:-1] - 1.0])
        bench_wealth = np.zeros(n_days)
        running = 0.0
        for t in range(n_days):
            running = max(running * (1.0 + b_ret[t]) + flows[t], 0.0)
            bench_wealth[t] = running
        bench_index = b_adj / b_adj[start]

    realized = daily_returns[start:][active[start:]]
    period_days = (dates[-1] - dates[start]).days
    twr_total = float(twr_index[-1] - 1.0)
    annualize = period_days >= 365

    def annualized(total: float) -> Optional[float]:
        return (1.0 + total) ** (DAYS_PER_YEAR / period_days) - 1.0 if annualize and total > -1.0 else None

    enough = realized.size >= MIN_OBSERVATIONS
    mwr = xirr(cash_flows + [(dates[-1], float(wealth[-1]))]) if annualize else None

    series = []
    for t in range(start, n_days):
        point = {
            "date": str(dates[t]),
            "portfolio_value": round(float(wealth[t]), 2),
            "market_value": round(float(market_value[t]), 2),
            "invested_capital": round(float(invested[t]), 2),
            "cumulative_gain": round(float(wealth[t] - invested[t]), 2),
            "twr_index": round(float(twr_index[t] * 100.0), 4),
        }
        if bench_index is not None:
            point["benchmark_value"] = round(float(bench_wealth[t]), 2)
            point["benchmark_index"] = round(float(bench_index[t] * 100.0), 4)
        series.append(point)

    bench_block = None
    if bench_index is not None:
        bench_total = float(bench_index[-1] - 1.0)
        bench_block = {
            "ticker": benchmark,
            "twr_cumulative_pct": round(bench_total * 100, 2),
            "twr_annualized_pct": round_or_none(annualized(bench_total), scale=100),
            "value_with_same_flows": round(float(bench_wealth[-1]), 2),
        }

    return {
        "summary": {
            "start_date": str(dates[start]),
            "end_date": str(dates[-1]),
            "period_days": period_days,
            "is_annualized": annualize,
            "twr_cumulative_pct": round(twr_total * 100, 2),
            "twr_annualized_pct": round_or_none(annualized(twr_total), scale=100),
            "mwr_annualized_pct": round_or_none(mwr, scale=100),
            "volatility_pct": round_or_none(annualized_volatility(realized), scale=100) if enough else None,
            "sharpe_ratio": round_or_none(sharpe_ratio(realized, risk_free_rate)) if enough else None,
            "sortino_ratio": round_or_none(sortino_ratio(realized, risk_free_rate)) if enough else None,
            # 1,0 = valeur au prix d'achat : une baisse dès le premier jour compte dans le drawdown
            "max_drawdown_pct": round(max_drawdown(np.concatenate([[1.0], twr_index[start:]])) * 100, 2),
            "current_wealth_eur": round(float(wealth[-1]), 2),
            "current_market_value_eur": round(float(market_value[-1]), 2),
            "net_invested_eur": round(float(invested[-1]), 2),
            "total_gain_eur": round(float(wealth[-1] - invested[-1]), 2),
            "risk_free_rate_pct": round(risk_free_rate * 100, 2),
            "observations": int(realized.size),
            "history_truncated_before": str(truncated_before) if truncated_before else None,
            "excluded_tickers": excluded,
        },
        "benchmark": bench_block,
        "time_series": series,
    }
