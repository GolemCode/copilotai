import { formatDate, formatPct, toneClass } from '../utils/format';

/**
 * Indicateurs clés du portefeuille.
 * - performance : performance RÉELLE depuis les achats (TWR), reconstituée à partir des transactions.
 * - summary : composition actuelle appliquée à l'historique de cours (poids d'aujourd'hui, période choisie).
 */
export const MetricsCards = ({
  summary = null,
  performance = null,
  totalCurrentValue = null,
  totalCostBasis = null
}) => {
  if (!summary) {
    return null;
  }

  // 1. Valorisation et plus-value latente (positions détenues, prix de revient)
  const portVal = totalCurrentValue ?? summary.final_value ?? 0;
  const costBasis = totalCostBasis ?? 0;
  const unrealizedPnl = portVal - costBasis;
  const unrealizedPnlPct = costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : null;

  // 2. Performance réelle : annualisée seulement au-delà d'un an (règle GIPS)
  const realPerf = performance
    ? (performance.is_annualized ? performance.twr_annualized_pct : performance.twr_cumulative_pct)
    : null;
  const realPerfSubtitle = performance
    ? (performance.is_annualized
      ? `Annualisée depuis le ${formatDate(performance.start_date)}`
      : `Depuis le ${formatDate(performance.start_date)}, hors effet des apports`)
    : 'Aucune opération sur un actif coté';

  // 3. Performance historique : ce qu'aurait rapporté la composition actuelle sur la période
  const historyYears = summary.history_days ? summary.history_days / (summary.trading_days_per_year || 252) : null;
  const periodLabel = historyYears
    ? `${historyYears.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} an${historyYears >= 2 ? 's' : ''}`
    : null;

  return (
    <div className="metrics-cards-grid metrics-cards-clean">
      {/* 1. Valorisation et plus-value latente */}
      <div className="kpi-card">
        <span className="kpi-title">Valorisation du portefeuille</span>
        <div className="kpi-value">
          {portVal.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
        </div>
        <div className="kpi-subtitle">
          Plus-value latente : <strong className={toneClass(unrealizedPnl)}>
            {unrealizedPnl >= 0 ? '+' : ''}{unrealizedPnl.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
            {unrealizedPnlPct !== null && ` (${formatPct(unrealizedPnlPct, true)})`}
          </strong>
        </div>
      </div>

      {/* 2. Performance réelle depuis les achats (pondérée dans le temps) */}
      <div className="kpi-card">
        <span className="kpi-title" title="Rendement pondéré dans le temps (TWR) : neutralise les apports et retraits">
          Performance réelle
        </span>
        <div className={`kpi-value ${toneClass(realPerf)}`}>
          {formatPct(realPerf, true)}
        </div>
        <div className="kpi-subtitle">{realPerfSubtitle}</div>
      </div>

      {/* 3. Performance historique de la composition actuelle */}
      <div className="kpi-card">
        <span className="kpi-title" title="Positions actuelles appliquées aux cours passés, dividendes réinvestis">
          Performance historique{periodLabel ? ` (${periodLabel})` : ''}
        </span>
        <div className={`kpi-value ${toneClass(summary.total_return_pct)}`}>
          {formatPct(summary.total_return_pct, true)}
        </div>
        <div className="kpi-subtitle">
          Soit {formatPct(summary.annualized_return_pct, true)} par an · pire baisse {formatPct(summary.max_drawdown_pct)}
        </div>
      </div>

      {/* 4. Risque : volatilité et ratio de Sharpe sur la même période */}
      <div className="kpi-card">
        <span className="kpi-title">Volatilité annuelle</span>
        <div className="kpi-value">
          {formatPct(summary.annualized_volatility_pct)}
        </div>
        <div
          className="kpi-subtitle"
          title={summary.risk_free_source ? `Taux sans risque : ${summary.risk_free_source}` : undefined}
        >
          Ratio de Sharpe {summary.sharpe_ratio != null ? summary.sharpe_ratio.toFixed(2) : '—'}
          {summary.risk_free_rate_pct != null ? ` (taux sans risque ${summary.risk_free_rate_pct.toFixed(2)} %)` : ''}
        </div>
      </div>
    </div>
  );
};
