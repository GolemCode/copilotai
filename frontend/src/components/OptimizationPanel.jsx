import { useState } from 'react';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer
} from 'recharts';
import { formatPct } from '../utils/format';

/**
 * Optimisation moyenne-variance (Markowitz, long-only) des lignes cotées du portefeuille.
 * Les calculs sont faits par l'API (optimisation sous contraintes sur 3 ans d'historique en EUR).
 */
export const OptimizationPanel = ({ assetBreakdown = [] }) => {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  const tickers = assetBreakdown.map((a) => a.ticker);
  const currentWeights = Object.fromEntries(assetBreakdown.map((a) => [a.ticker, a.weight_pct]));

  const handleOptimize = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/v1/portfolio/optimize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tickers, current_weights: currentWeights })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Optimisation impossible.");
      setResult(data);
    } catch (err) {
      setResult(null);
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  const portfolios = result
    ? [
        { key: 'current', label: 'Actuel', data: result.current },
        { key: 'max_sharpe', label: 'Sharpe maximal', data: result.max_sharpe },
        { key: 'min_volatility', label: 'Variance minimale', data: result.min_volatility }
      ].filter((p) => p.data)
    : [];

  const point = (p) => [{
    volatility: p.data.annualized_volatility_pct,
    return: p.data.annualized_return_pct,
    sharpe: p.data.sharpe_ratio,
    label: p.label
  }];

  return (
    <div className="dashboard-panel">
      <h3 className="section-title">Optimisation de Markowitz (frontière efficiente)</h3>
      <p className="section-subtitle">
        Allocations long-only qui maximisent le ratio de Sharpe ou minimisent la variance, estimées sur 3 ans de
        rendements en EUR. Les rendements attendus étant des moyennes historiques (très bruitées), ces allocations
        sont instables : à lire comme une illustration, pas comme une recommandation.
      </p>

      {tickers.length < 2 ? (
        <p className="section-subtitle">Au moins deux lignes cotées sont nécessaires pour optimiser l'allocation.</p>
      ) : (
        <button type="button" onClick={handleOptimize} disabled={isLoading} className="btn-simulate">
          {isLoading ? 'Optimisation en cours...' : `Optimiser l'allocation (${tickers.length} lignes cotées)`}
        </button>
      )}
      {error && <p className="section-subtitle text-danger">{error}</p>}

      {result && (
        <>
          <p className="section-subtitle">
            Historique du {new Date(result.history_start).toLocaleDateString('fr-FR')} au {new Date(result.history_end).toLocaleDateString('fr-FR')} ·
            taux sans risque {formatPct(result.risk_free_rate_pct)} ({result.risk_free_source})
          </p>

          <div className="table-wrapper">
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Actif</th>
                  {portfolios.map((p) => <th key={p.key} className="right">{p.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {tickers.map((t) => (
                  <tr key={t}>
                    <td><strong>{t}</strong></td>
                    {portfolios.map((p) => <td key={p.key} className="right">{formatPct(p.data.weights[t])}</td>)}
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="table-summary-row">
                  <td>Rendement attendu</td>
                  {portfolios.map((p) => <td key={p.key} className="right">{formatPct(p.data.annualized_return_pct)}</td>)}
                </tr>
                <tr className="table-summary-row">
                  <td>Volatilité</td>
                  {portfolios.map((p) => <td key={p.key} className="right">{formatPct(p.data.annualized_volatility_pct)}</td>)}
                </tr>
                <tr className="table-summary-row">
                  <td>Ratio de Sharpe</td>
                  {portfolios.map((p) => <td key={p.key} className="right">{p.data.sharpe_ratio.toFixed(2)}</td>)}
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="chart-wrapper medium">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 20, right: 30, left: 10, bottom: 20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" dataKey="volatility" name="Volatilité" unit=" %" domain={['auto', 'auto']} />
                <YAxis type="number" dataKey="return" name="Rendement attendu" unit=" %" domain={['auto', 'auto']} />
                <Tooltip
                  cursor={{ strokeDasharray: '3 3' }}
                  formatter={(value, name) => [`${Number(value).toFixed(2)} %`, name]}
                />
                <Legend />
                <Scatter name="Frontière efficiente" data={result.efficient_frontier} fill="#3b82f6" line shape="circle" />
                {portfolios.map((p, idx) => (
                  <Scatter
                    key={p.key}
                    name={p.label}
                    data={point(p)}
                    fill={['#0f172a', '#10b981', '#f59e0b'][idx]}
                    shape={idx === 0 ? 'diamond' : 'star'}
                  />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
};
